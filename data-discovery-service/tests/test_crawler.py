# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for crawler.crawl_mcp using a local FastAPI MCP stub."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings
import crawler as crawler_mod
from crawler import TenantMismatchError, _resolve_base_url, crawl_all, crawl_mcp


# ---------------------------------------------------------------------------
# Mongo-like stub
# ---------------------------------------------------------------------------


class _MemCol:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    @staticmethod
    def _match(d: Dict[str, Any], q: Dict[str, Any]) -> bool:
        """Enough of the Mongo matcher for these tests: equality + ``$or``."""
        for k, v in q.items():
            if k == "$or":
                if not any(_MemCol._match(d, sub) for sub in v):
                    return False
            elif d.get(k) != v:
                return False
        return True

    async def find_one(self, q, *_a, **_kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    def find(self, q=None, _projection=None, *_a, **_kw):
        """A motor-shaped cursor: async-iterable, with .limit()."""
        rows = [d for d in self.docs if self._match(d, q or {})]

        class _Cursor:
            def __init__(self, r):
                self._rows = list(r)

            def limit(self, n):
                return _Cursor(self._rows[:n])

            def __aiter__(self):
                rows = self._rows

                async def _gen():
                    for r in rows:
                        yield r
                return _gen()

        return _Cursor(rows)

    async def delete_many(self, q):
        keep = [d for d in self.docs if not self._match(d, q)]
        removed = len(self.docs) - len(keep)
        self.docs = keep

        class _R:
            deleted_count = removed
        return _R()

    async def update_one(self, q, update, upsert=False, *_a, **_kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(update.get("$set", {}))

                class _R:
                    matched_count = 1
                return _R()
        if upsert:
            new = dict(q)
            new.update(update.get("$set", {}))
            self.docs.append(new)

        class _R:
            matched_count = 0
        return _R()


# ---------------------------------------------------------------------------
# Fake dept-mcp server
# ---------------------------------------------------------------------------


def _build_fake_mcp() -> FastAPI:
    app = FastAPI()

    @app.get("/datasets")
    def datasets():
        return {
            "datasets": [
                {
                    "id": "claims_db.policies",
                    "source_id": "claims_db",
                    "name": "policies",
                    "kind": "sql",
                }
            ],
            "total": 1,
        }

    @app.get("/datasets/{dataset_id:path}/sample")
    def sample(dataset_id: str, n: int = 100):
        return {
            "id": dataset_id,
            "rows": [
                {"policy_id": "P001", "customer_email": "a@b.com", "premium": "1500.00"},
                {"policy_id": "P002", "customer_email": "c@d.com", "premium": "2500.00"},
                {"policy_id": "P003", "customer_email": "e@f.com", "premium": "3500.00"},
            ],
            "redacted": False,
            "truncated": False,
        }

    @app.get("/datasets/{dataset_id:path}")
    def describe(dataset_id: str, source_id: str | None = None):
        return {
            "id": dataset_id,
            "source_id": "claims_db",
            "name": dataset_id.split(".")[-1],
            "kind": "sql",
            "columns": [
                {"name": "policy_id", "type": "VARCHAR", "nullable": False},
                {"name": "customer_email", "type": "VARCHAR", "nullable": True},
                {"name": "premium", "type": "DECIMAL", "nullable": True},
            ],
            "read_via": {"kind": "sql", "target": "policies"},
            "write_actions": [
                {
                    "id": "create_policy",
                    "verb": "create",
                    "input_schema": {"required": ["policy_id"]},
                }
            ],
            "samples_redacted": True,
            "row_count_approx": 12345,
            "relationships": [],
        }

    return app


@pytest.fixture
def fake_mcp_client() -> tuple[str, TestClient]:
    app = _build_fake_mcp()
    client = TestClient(app)
    # base_url is what crawler will hit; TestClient stores it on .base_url
    return str(client.base_url), client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_mcp_writes_catalogue_entry(fake_mcp_client, monkeypatch):
    base_url, fake_client = fake_mcp_client
    settings = Settings()
    col = _MemCol()

    # Patch httpx.AsyncClient to route to the FastAPI testclient transport.
    transport = httpx.ASGITransport(app=fake_client.app)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            kw.setdefault("base_url", base_url)
            super().__init__(*a, **kw)

    monkeypatch.setattr("crawler.httpx.AsyncClient", _PatchedClient)

    report = await crawl_mcp(
        # org_ids must declare the crawl tenant — the guard is now fail-closed.
        {"tool_id": "fake-mcp", "base_url": base_url, "org_ids": ["bajaj"]},
        settings=settings,
        catalogue_col=col,
        auth_header=None,
        tenant_id="bajaj",
    )
    assert report.datasets_seen == 1
    assert report.datasets_written == 1
    assert not report.errors

    assert len(col.docs) == 1
    doc = col.docs[0]
    assert doc["tenant_id"] == "bajaj"
    assert doc["source_id"] == "claims_db"
    assert doc["dataset_id"] == "claims_db.policies"
    assert doc["has_pii"] is True  # customer_email is PII
    # email column got semantic_type
    cols = {c["name"]: c for c in doc["columns"]}
    assert cols["customer_email"]["semantic_type"] == "email"
    assert cols["customer_email"]["pii"] is True
    assert cols["policy_id"]["pii"] is False
    # write_action survived
    assert any(w["id"] == "create_policy" for w in doc["write_actions"])


@pytest.mark.asyncio
async def test_crawl_mcp_no_base_url():
    settings = Settings()
    col = _MemCol()
    report = await crawl_mcp(
        # org_ids present (tenant guard passes) so we exercise the no-base_url
        # soft-error path specifically.
        {"tool_id": "broken", "org_ids": ["bajaj"]},
        settings=settings,
        catalogue_col=col,
        auth_header=None,
        tenant_id="bajaj",
    )
    assert report.datasets_seen == 0
    assert report.errors and "base_url" in report.errors[0]


# ---------------------------------------------------------------------------
# Tenant-mismatch guard — fail first, fail loud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_mcp_raises_on_tenant_mismatch():
    """A source whose org_ids does not include the crawl tenant_id must abort
    loudly — never file its datasets under the wrong tenant."""
    settings = Settings()
    col = _MemCol()
    with pytest.raises(TenantMismatchError):
        await crawl_mcp(
            {"tool_id": "acme-power-mcp", "base_url": "http://x", "org_ids": ["acme-power"]},
            settings=settings,
            catalogue_col=col,
            auth_header=None,
            tenant_id="_system",
        )
    # Nothing written under the wrong tenant.
    assert col.docs == []


@pytest.mark.asyncio
async def test_crawl_mcp_allows_matching_tenant(fake_mcp_client, monkeypatch):
    """org_ids contains the crawl tenant_id → no mismatch, crawl proceeds."""
    base_url, fake_client = fake_mcp_client
    settings = Settings()
    col = _MemCol()
    transport = httpx.ASGITransport(app=fake_client.app)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            kw.setdefault("base_url", base_url)
            super().__init__(*a, **kw)

    monkeypatch.setattr("crawler.httpx.AsyncClient", _PatchedClient)

    report = await crawl_mcp(
        {"tool_id": "fake-mcp", "base_url": base_url, "org_ids": ["bajaj"]},
        settings=settings,
        catalogue_col=col,
        auth_header=None,
        tenant_id="bajaj",
    )
    assert report.datasets_written == 1
    assert col.docs[0]["tenant_id"] == "bajaj"


@pytest.mark.asyncio
async def test_crawl_mcp_raises_on_missing_org_ids():
    """Missing org_ids = cannot verify the source's org → fail-closed. We refuse
    to crawl rather than file datasets under an unverified tenant. (The on-demand
    /crawl/dataset route resolves org_ids from discovery before calling in, so a
    hand-supplied body is enriched, not blocked.)"""
    settings = Settings()
    col = _MemCol()
    with pytest.raises(TenantMismatchError):
        await crawl_mcp(
            {"tool_id": "legacy", "base_url": "http://x"},  # no org_ids
            settings=settings,
            catalogue_col=col,
            auth_header=None,
            tenant_id="bajaj",
        )
    assert col.docs == []


# ---------------------------------------------------------------------------
# Base-URL resolution + one-source-one-tool dedupe (crawl_all)
# ---------------------------------------------------------------------------


def test_resolve_base_url_prefers_explicit_then_strips_query():
    assert _resolve_base_url({"base_url": "http://m:8503/"}) == "http://m:8503"
    assert _resolve_base_url({"endpoint": "http://m:8503"}) == "http://m:8503"
    # query_endpoint fallback strips the trailing /query
    assert _resolve_base_url({"query_endpoint": "http://m:8503/query"}) == "http://m:8503"
    # no URL at all → None (caller reports it loudly, never a silent drop)
    assert _resolve_base_url({"tool_id": "legacy"}) is None


@pytest.mark.asyncio
async def test_crawl_all_dedupes_sources_to_one_mcp(monkeypatch):
    """discovery advertises one tool PER SOURCE sharing one MCP; crawl_all must
    crawl that MCP exactly ONCE (else it re-embeds the same datasets N times)."""
    # 5 source-tools (mirrors acme-power) all pointing at one MCP's /query, plus a
    # second distinct MCP, plus one bad entry with no URL.
    tools = [
        {"tool_id": "acme-power-billing-billing", "source_id": "billing",
         "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]},
        {"tool_id": "acme-power-ami-smart_meter", "source_id": "smart_meter",
         "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]},
        {"tool_id": "acme-power-dist-outage", "source_id": "outage_management",
         "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]},
        {"tool_id": "acme-power-vig-field_ops", "source_id": "field_operations",
         "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]},
        {"tool_id": "acme-power-pmu-policy", "source_id": "acme-power_policy_library",
         "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]},
        {"tool_id": "other-mcp", "base_url": "http://mcp-b:18504", "org_ids": ["acme-power"]},
        {"tool_id": "broken", "org_ids": ["acme-power"]},  # no URL → kept, reports error
    ]

    async def fake_list(settings, auth_header):
        return tools

    crawled_bases: List[str] = []

    async def fake_crawl_mcp(mcp, **kw):
        crawled_bases.append(_resolve_base_url(mcp) or "<none>")
        from models import CrawlReport
        return CrawlReport(mcp_tool_id=mcp.get("tool_id", "?"))

    monkeypatch.setattr(crawler_mod, "list_registered_mcps", fake_list)
    monkeypatch.setattr(crawler_mod, "crawl_mcp", fake_crawl_mcp)

    reports, _, _ = await crawl_all(
        settings=Settings(), catalogue_col=_MemCol(),
        auth_header=None, tenant_id="acme-power",
    )

    # 7 tool entries → 2 distinct MCPs (mcp-a once, mcp-b once) + 1 no-URL entry
    assert sorted(crawled_bases) == ["<none>", "http://mcp-a:8503", "http://mcp-b:18504"]
    # 3 MCP-crawl reports + 1 platform-side semantic-discovery report (no-op here:
    # dept_sources_col is None, so it discovers nothing but still reports).
    assert len(reports) == 4
    assert reports[-1].mcp_tool_id == "__semantic__"


# ---------------------------------------------------------------------------
# Platform-side semantic-source discovery (RAG short-circuit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_semantic_sources_catalogues_discovery_tools(monkeypatch):
    from crawler import discover_semantic_sources

    async def _noop_index(settings, entries):
        return len(entries)

    monkeypatch.setattr("catalogue_vectors.index_entries", _noop_index, raising=False)

    # The discovery registry returns ONLY active source_type=="semantic" tools
    # (crawl_all pre-filters). Each carries dept/org scope + public_within_org +
    # rag_collection — no dept_sources lookup.
    semantic_tools = [
        {"source_id": "sop_library_operations", "dept_ids": ["operations"],
         "org_ids": ["acme"], "source_type": "semantic",
         "name": "Operations SOP Library", "description": "SOPs for ops",
         "public_within_org": False, "rag_collection": "mcp_dept_libraries"},
        # an org-wide semantic corpus — public_within_org must carry through;
        # empty dept_ids ⇒ dept_id None.
        {"source_id": "policy_library", "dept_ids": [], "org_ids": ["acme"],
         "source_type": "semantic", "name": "Policy Library",
         "public_within_org": True, "rag_collection": "mcp_dept_libraries"},
    ]
    catalogue = _MemCol()

    report = await discover_semantic_sources(
        settings=Settings(), catalogue_col=catalogue,
        semantic_tools=semantic_tools, tenant_id="acme",
    )

    assert report.datasets_seen == 2 and report.datasets_written == 2
    by_source = {d["source_id"]: d for d in catalogue.docs}
    assert set(by_source) == {"sop_library_operations", "policy_library"}
    ops = by_source["sop_library_operations"]
    assert ops["kind"] == "semantic"
    assert ops["dataset_id"] == "sop_library_operations"   # == source_id (matches MCP fallback id)
    assert ops["dept_id"] == "operations"
    assert ops["public_within_org"] is False
    assert ops["columns"] == [] and ops["mcp_tool_id"] is None   # no MCP routing for semantic
    assert by_source["policy_library"]["public_within_org"] is True
    assert by_source["policy_library"]["dept_id"] is None   # empty dept_ids ⇒ None


@pytest.mark.asyncio
async def test_discover_semantic_sources_empty_is_noop():
    from crawler import discover_semantic_sources
    report = await discover_semantic_sources(
        settings=Settings(), catalogue_col=_MemCol(),
        semantic_tools=[], tenant_id="acme",
    )
    assert report.datasets_seen == 0 and report.datasets_written == 0
    assert report.errors == []


# ---------------------------------------------------------------------------
# Orphan prune — retiring rows the registry no longer backs
#
# The catalogue was upsert-only, so a source removed from sources.json kept its
# row forever and /catalogue kept serving it. Found live in prod: acme-power's
# `public_directory.user` was still served two weeks after the source was
# retired, while discovery had already deactivated its tool.
#
# These tests weight the DANGEROUS direction: a false prune deletes a live
# dataset, a missed one is merely stale.
# ---------------------------------------------------------------------------


def _row(source_id: str, dataset_id: str, tenant: str = "acme-power") -> Dict[str, Any]:
    return {"tenant_id": tenant, "source_id": source_id, "dataset_id": dataset_id}


async def _prune(col, *, live, seen, tenant="acme-power", settings=None):
    return await crawler_mod._prune_orphaned_entries(
        settings=settings or Settings(),
        catalogue_col=col,
        tenant_id=tenant,
        live_source_ids=set(live),
        seen_by_source={k: set(v) for k, v in (seen or {}).items()},
    )


@pytest.mark.asyncio
async def test_prune_removes_row_whose_source_is_no_longer_registered():
    """The live bug: source retired from sources.json, discovery deactivated its
    tool, but the catalogue row survived and was still served to the builder."""
    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("public_directory", "public_directory.user")]

    removed = await _prune(col, live={"billing"}, seen={"billing": {"billing.bills"}})

    assert removed == 1
    assert [d["dataset_id"] for d in col.docs] == ["billing.bills"]


@pytest.mark.asyncio
async def test_prune_removes_a_dataset_a_live_source_stopped_serving():
    """Table dropped from a still-registered source."""
    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("billing", "billing.legacy_bills")]

    removed = await _prune(col, live={"billing"}, seen={"billing": {"billing.bills"}})

    assert removed == 1
    assert [d["dataset_id"] for d in col.docs] == ["billing.bills"]


@pytest.mark.asyncio
async def test_prune_refuses_when_the_registry_is_empty():
    """An empty registry is an outage, not "every source was retired". Pruning
    on it would delete the tenant's entire catalogue."""
    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("smart_meter", "smart_meter.meters")]

    removed = await _prune(col, live=set(), seen={})

    assert removed == 0
    assert len(col.docs) == 2


@pytest.mark.asyncio
async def test_prune_leaves_datasets_of_a_source_that_was_not_crawled_this_pass():
    """A source registered but absent from seen_by_source (its crawl errored, or
    it's semantic and never MCP-crawled) keeps every row — "not seen" only means
    "gone" on a clean pass."""
    col = _MemCol()
    col.docs = [
        _row("billing", "billing.bills"),
        _row("acme_power_policy_library", "acme_power_policy_library"),
    ]

    removed = await _prune(col, live={"billing", "acme_power_policy_library"},
                           seen={"billing": {"billing.bills"}})

    assert removed == 0
    assert len(col.docs) == 2


@pytest.mark.asyncio
async def test_prune_is_scoped_to_the_crawled_tenant():
    """A single-tenant crawl must never touch another tenant's rows."""
    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("gone", "gone.x", tenant="other-org")]

    removed = await _prune(col, live={"billing"}, seen={"billing": {"billing.bills"}})

    assert removed == 0
    assert len(col.docs) == 2


@pytest.mark.asyncio
async def test_prune_drops_the_orphan_from_the_vector_index_too():
    """A left-behind vector eats a top_k slot on every search."""
    col = _MemCol()
    col.docs = [_row("public_directory", "public_directory.user")]
    seen_keys: List[Any] = []

    async def _fake_delete(settings, keys):
        seen_keys.extend(keys)
        return len(keys)

    import catalogue_vectors
    orig = catalogue_vectors.delete_entries
    catalogue_vectors.delete_entries = _fake_delete
    try:
        removed = await _prune(col, live={"billing"}, seen={"billing": set()})
    finally:
        catalogue_vectors.delete_entries = orig

    assert removed == 1
    assert seen_keys == [("acme-power", "public_directory", "public_directory.user")]


@pytest.mark.asyncio
async def test_crawl_all_skips_pruning_for_a_source_whose_crawl_errored(monkeypatch):
    """End-to-end guard: a describe failure must not delete the live rows of the
    source that failed — that would turn a transient blip into data loss."""
    tools = [{"tool_id": "t-billing", "source_id": "billing",
              "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]}]

    async def fake_list(settings, auth_header):
        return tools

    async def fake_crawl_mcp(mcp, **kw):
        from models import CrawlReport
        rep = CrawlReport(mcp_tool_id="t-billing")
        # Saw + wrote one dataset, but another failed to describe.
        rep.seen_dataset_ids = {"billing": ["billing.bills"]}
        rep.datasets_written = 1
        rep.errors.append("describe failed for billing.payments")
        return rep

    monkeypatch.setattr(crawler_mod, "list_registered_mcps", fake_list)
    monkeypatch.setattr(crawler_mod, "crawl_mcp", fake_crawl_mcp)

    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("billing", "billing.payments")]

    _reports, _total, pruned = await crawl_all(
        settings=Settings(), catalogue_col=col,
        auth_header=None, tenant_id="acme-power",
    )

    assert pruned == 0
    assert len(col.docs) == 2, "a failed pass must not be read as 'the dataset is gone'"


@pytest.mark.asyncio
async def test_crawl_all_prunes_the_retired_source_on_a_clean_pass(monkeypatch):
    """The prod scenario, end to end through crawl_all."""
    tools = [{"tool_id": "t-billing", "source_id": "billing",
              "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]}]

    async def fake_list(settings, auth_header):
        return tools

    async def fake_crawl_mcp(mcp, **kw):
        from models import CrawlReport
        rep = CrawlReport(mcp_tool_id="t-billing")
        rep.seen_dataset_ids = {"billing": ["billing.bills"]}
        rep.datasets_written = 1
        return rep

    monkeypatch.setattr(crawler_mod, "list_registered_mcps", fake_list)
    monkeypatch.setattr(crawler_mod, "crawl_mcp", fake_crawl_mcp)

    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("public_directory", "public_directory.user")]

    _reports, _total, pruned = await crawl_all(
        settings=Settings(), catalogue_col=col,
        auth_header=None, tenant_id="acme-power",
    )

    assert pruned == 1
    assert [d["dataset_id"] for d in col.docs] == ["billing.bills"]


@pytest.mark.asyncio
async def test_crawl_all_does_not_prune_a_source_seen_by_both_a_clean_and_a_failed_mcp(monkeypatch):
    """If two MCPs serve one source_id and only one pass is clean, the clean
    half's view is PARTIAL — pruning on it would delete rows the failed half
    still serves."""
    tools = [
        {"tool_id": "t-a", "source_id": "billing",
         "query_endpoint": "http://mcp-a:8503/query", "org_ids": ["acme-power"]},
        {"tool_id": "t-b", "source_id": "billing",
         "query_endpoint": "http://mcp-b:18504/query", "org_ids": ["acme-power"]},
    ]

    async def fake_list(settings, auth_header):
        return tools

    async def fake_crawl_mcp(mcp, **kw):
        from models import CrawlReport
        base = _resolve_base_url(mcp) or ""
        rep = CrawlReport(mcp_tool_id=mcp.get("tool_id", "?"))
        if "mcp-a" in base:
            rep.seen_dataset_ids = {"billing": ["billing.bills"]}   # clean, partial
            rep.datasets_written = 1
        else:
            rep.seen_dataset_ids = {"billing": ["billing.payments"]}
            rep.errors.append("sample failed for billing.payments")  # degraded
        return rep

    monkeypatch.setattr(crawler_mod, "list_registered_mcps", fake_list)
    monkeypatch.setattr(crawler_mod, "crawl_mcp", fake_crawl_mcp)

    col = _MemCol()
    col.docs = [_row("billing", "billing.bills"), _row("billing", "billing.payments")]

    _reports, _total, pruned = await crawl_all(
        settings=Settings(), catalogue_col=col,
        auth_header=None, tenant_id="acme-power",
    )

    assert pruned == 0
    assert len(col.docs) == 2
