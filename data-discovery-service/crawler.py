# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
data-discovery-service — Crawler
==================================
Per-MCP pipeline:

  1. Discover registered datasets via GET /datasets
  2. For each dataset: GET /datasets/{id}, GET /datasets/{id}/sample
  3. Run the rule-based PII / semantic-type classifier
  4. Compute a schema fingerprint and check the existing Mongo doc:
       — if fingerprint unchanged AND a mapping already exists, reuse it
       — else, if any name is cryptic, call the configured OpenAI-compatible
         LLM (OpenRouter in the cloud deployment) to propose semantic
         ``name`` + ``description`` for the table and columns
  5. Upsert the enriched CatalogueEntry into ``data_catalogue``

Top-level ``crawl_all`` fans out across MCPs in parallel with a bounded
``asyncio.gather`` (cap from ``settings.crawl_max_parallel_mcps``), then makes
one reconcile pass (``_prune_orphaned_entries``) to delete rows the registry no
longer backs — the crawl was upsert-only, so a retired source kept its row and
``/catalogue`` kept serving a dataset the MCP no longer answers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import jwt
from motor.motor_asyncio import AsyncIOMotorCollection

from classifier import classify_column
from config import Settings
from enricher import (
    apply_manual_overrides,
    compute_schema_fingerprint,
)
from models import CatalogueColumn, CatalogueEntry, CatalogueWriteAction, CrawlReport

logger = logging.getLogger(__name__)


class TenantMismatchError(RuntimeError):
    """Raised when a crawl would file a source's datasets under a tenant the
    source does not belong to.

    The catalogue is keyed by ``(tenant_id, source_id, dataset_id)`` and the
    runtime/builder read it filtered by the caller's tenant. If we crawl a
    source whose ``org_ids`` does not include this crawl's ``tenant_id``, the
    entries land under the wrong tenant — invisible to the real tenant and a
    silent duplicate. Fail first, fail loud rather than write them.
    """


def _assert_tenant_matches(mcp: Dict[str, Any], tenant_id: str, tool_id: str) -> None:
    """Guard: the crawl ``tenant_id`` must be one of the source's ``org_ids``.

    ``org_ids`` comes from the discovery ``/tools/available`` tool object and is
    REQUIRED — we cannot file a source's datasets under a tenant we can't verify
    it belongs to. Two failure modes, both fail-closed:

      • ``org_ids`` absent/empty → we cannot verify the org at all. Refuse, so a
        source with no declared org never lands under an unverified tenant. The
        on-demand ``/crawl/dataset`` route resolves ``org_ids`` from discovery
        before calling in, so a hand-supplied body is enriched, not blocked.
      • ``tenant_id`` not in ``org_ids`` → a definite mismatch.

    (Previously a missing ``org_ids`` was treated as "can't verify → allow"; that
    silent fail-open let an undeclared source be crawled under any tenant.)
    """
    raw = mcp.get("org_ids")
    if raw is None:
        raw = mcp.get("org_id")  # tolerate a scalar field name
    org_ids = raw if isinstance(raw, (list, tuple, set)) else ([raw] if raw else [])
    org_ids = [str(o) for o in org_ids if o]
    if not org_ids:
        msg = (
            f"source org undeclared for MCP {tool_id!r}: no org_ids on the "
            f"discovery tool record, so crawl tenant_id={tenant_id!r} cannot be "
            f"verified; refusing to file its datasets under an unverified tenant"
        )
        logger.error(msg)
        raise TenantMismatchError(msg)
    if str(tenant_id) not in org_ids:
        msg = (
            f"tenant mismatch: crawl tenant_id={tenant_id!r} is not in source "
            f"org_ids={org_ids!r} for MCP {tool_id!r}; refusing to file its "
            f"datasets under the wrong tenant"
        )
        logger.error(msg)
        raise TenantMismatchError(msg)


# ---------------------------------------------------------------------------
# System identity for the crawler
# ---------------------------------------------------------------------------


def mint_crawler_token(settings: Settings, org_id: str) -> str:
    """Mint a short-lived HS256 system JWT for the crawler, scoped to ONE org.

    The crawler is a service, not a human — but dept-MCPs run
    ``AUTHZ_ENFORCE=true`` and reject a missing/unverifiable ``X-User-JWT``.
    So we mint a short-lived token, signed with the SAME shared HS256 secret
    Citra-User-Service signs with (so every MCP verifies it), carrying
    ``org_admin`` for ``org_id`` — NOT ``super_admin``: every MCP deployment is
    single-tenant and we never cross orgs. ``org_admin`` makes the visibility
    PDP return every source IN THIS ORG (a complete per-tenant catalogue) via
    ``org_admin_within_org``. ``user`` is also included because the source's
    default ``roles_allowed=["user"]`` role gate is NOT bypassed for org_admin
    (only super_admin bypasses it), so the token needs ``user`` to pass it. The
    ``crawler_system`` marker attributes the read to the crawler in the MCP
    audit log. Read-only: only list/describe/sample/run_query, never writes.
    """
    if not settings.jwt_secret:
        raise RuntimeError(
            "JWT_SECRET is not configured; cannot mint the crawler token. "
            "It must equal the shared secret Citra-User-Service signs with."
        )
    if not org_id:
        raise RuntimeError(
            "org_id is required to mint the crawler token — single-tenant by "
            "design, the org/tenant must always be passed explicitly."
        )
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "user_id": "discovery-crawler-system",
        "sub": "discovery-crawler-system",
        "org_id": org_id,
        "tenant_id": org_id,
        "roles": ["user", "org_admin"],
        "crawler_system": True,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + 600,  # 10 min — one crawl pass; re-minted per header build
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Discovery: which MCPs do we crawl?
# ---------------------------------------------------------------------------


def _resolve_base_url(mcp: Dict[str, Any]) -> Optional[str]:
    """Resolve a discovery tool record to the MCP's base URL.

    discovery-service advertises each *source* as its own tool (one-source-
    one-tool, so the routing LLM can pick a data domain) — but every source on
    the same physical MCP shares one ``query_endpoint`` (".../query"). The
    crawler works at MCP granularity (``/datasets`` is server-wide), so we
    derive the base URL and dedupe by it in ``crawl_all``. Returns ``None`` when
    no URL field is present so the caller can report it loudly (never a silent
    drop). Prefers an explicit ``base_url``/``endpoint``/``url``; else strips
    the trailing ``/query`` off ``query_endpoint``.
    """
    base = mcp.get("base_url") or mcp.get("endpoint") or mcp.get("url")
    if not base:
        qe = mcp.get("query_endpoint") or ""
        if qe:
            base = qe.rsplit("/query", 1)[0] if qe.endswith("/query") else qe
    return base.rstrip("/") if base else None


async def list_registered_mcps(settings: Settings, auth_header: Optional[str]) -> List[Dict[str, Any]]:
    """Ask discovery-service for every active dept-mcp tool.

    The discovery-service `/tools/available` endpoint returns tools scoped to
    the caller's tenant. We forward the user's JWT so we only see what they're
    allowed to see — the catalogue we build is per-tenant by construction.
    """
    if not settings.discovery_url:
        # Misconfiguration, not an empty registry — fail loud.
        raise RuntimeError(
            "DISCOVERY_URL is not configured; cannot enumerate dept-MCPs to "
            "crawl. Set DISCOVERY_URL to the discovery-service base URL."
        )
    headers = {"Authorization": auth_header} if auth_header else {}
    url = f"{settings.discovery_url.rstrip('/')}/tools/available"
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        # FAIL LOUD: an unreachable discovery is an operational failure, NOT an
        # empty registry. Returning [] here silently yields an empty catalogue —
        # the exact bug that hid a docker-hostname misconfig (DISCOVERY_URL
        # pointing at an unresolvable hostname). Propagate so /crawl/run 5xxs.
        raise RuntimeError(f"discovery-service unreachable at {url}: {exc}") from exc
    if resp.status_code != 200:
        # Non-200 is a failure (auth/route/server error) — never a silent [].
        raise RuntimeError(
            f"discovery /tools/available returned {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json()
    # A 200 with an empty list is a LEGITIMATE 'no MCPs registered' → []. We
    # only short-circuit to [] on a successful, genuinely-empty response.
    # discovery returns a bare JSON list; older builds wrapped it as
    # {"tools": [...]} — accept both.
    if isinstance(data, list):
        return data
    return data.get("tools") or data.get("items") or []


# ---------------------------------------------------------------------------
# MCP HTTP helpers
# ---------------------------------------------------------------------------


def _mcp_headers(settings: Settings, auth_header: Optional[str], org_id: str) -> Dict[str, str]:
    h: Dict[str, str] = {}
    if settings.service_api_key:
        # Service-to-service call. Two credentials, because dept-MCPs gate on
        # BOTH:
        #   • Authorization: Bearer <service key>  — service-to-service trust
        #   • X-User-JWT: <identity>               — required under
        #     AUTHZ_ENFORCE=true; a missing token is rejected 401.
        # We mint a short-lived org_admin "crawler" identity scoped to THIS
        # crawl's org (single-tenant — never super_admin / cross-org), so the
        # visibility PDP returns the complete catalogue for this org and the
        # read is audited as the crawler. (Earlier this header was omitted
        # because some MCPs were RS256/JWKS-only and would reject an HS256
        # token; every MCP now shares the HS256 secret, so it verifies.)
        h["Authorization"] = f"Bearer {settings.service_api_key}"
        h["X-User-JWT"] = mint_crawler_token(settings, org_id)
    elif auth_header:
        # No service key (legacy/dev) — forward the user's JWT so the MCP can
        # authorise and scope visibility per-user.
        h["X-User-JWT"] = auth_header.removeprefix("Bearer ").strip()
    return h


async def _fetch_json(client: httpx.AsyncClient, url: str, headers: Dict[str, str]) -> Optional[Any]:
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.debug("GET %s → %s", url, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Existing-doc lookup — drives the enrichment cache
# ---------------------------------------------------------------------------


async def _load_existing(
    catalogue_col: AsyncIOMotorCollection,
    *,
    tenant_id: str,
    source_id: str,
    dataset_id: str,
) -> Optional[Dict[str, Any]]:
    return await catalogue_col.find_one(
        {"tenant_id": tenant_id, "source_id": source_id, "dataset_id": dataset_id}
    )


def _scoping_from_tools(mcps: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``source_id → {dept_id, public_within_org}`` from the DISCOVERY tool list,
    for RBAC stamping onto catalogue entries. Replaces the retired ``dept_sources``
    scoping read — each discovery tool carries its source's dept/org scope +
    ``public_within_org`` (one tool per source)."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in mcps or []:
        sid = m.get("source_id")
        if not sid:
            continue
        dept_ids = m.get("dept_ids") or []
        out[sid] = {
            "dept_id": dept_ids[0] if dept_ids else None,
            "public_within_org": bool(m.get("public_within_org")),
        }
    return out


# ---------------------------------------------------------------------------
# Crawl one MCP
# ---------------------------------------------------------------------------


async def crawl_mcp(
    mcp: Dict[str, Any],
    *,
    settings: Settings,
    catalogue_col: AsyncIOMotorCollection,
    scoping_by_source: Optional[Dict[str, Dict[str, Any]]] = None,
    auth_header: Optional[str],
    tenant_id: str,
) -> CrawlReport:
    """Crawl every dataset on a single MCP and upsert into the catalogue.

    ``scoping_by_source`` (``source_id → {dept_id, public_within_org}``, built
    from the discovery registry) stamps RBAC scope onto each catalogue entry.
    Name-override pinning (formerly read from ``dept_sources``) retired with that
    collection — a source can carry overrides on its ``/datasets`` response
    instead if reinstated later.
    """
    started = time.perf_counter()
    base_url = _resolve_base_url(mcp)
    tool_id = mcp.get("tool_id") or mcp.get("id") or mcp.get("source_id") or "unknown"
    report = CrawlReport(mcp_tool_id=tool_id)

    # Fail first, fail loud: never file a source under the wrong tenant.
    _assert_tenant_matches(mcp, tenant_id, tool_id)

    if not base_url:
        report.errors.append("MCP has no base_url")
        return report

    # tenant_id is this crawl's org/tenant — used to scope the minted crawler
    # identity (single-tenant by design).
    headers = _mcp_headers(settings, auth_header, tenant_id)
    timeout = settings.crawl_per_mcp_timeout_seconds

    # Name-override pinning retired with dept_sources; scope comes from discovery.
    overrides_by_source: Dict[str, Any] = {}
    scoping_by_source = scoping_by_source or {}

    # Collected for the vector index (recall stage) — embedded + upserted into
    # the catalogue Milvus collection after the Mongo writes. Fail-open.
    indexed_entries: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        listing = await _fetch_json(client, f"{base_url.rstrip('/')}/datasets", headers)
        if listing is None:
            report.errors.append("/datasets unreachable or returned non-200")
            return report
        datasets = listing.get("datasets") or []
        report.datasets_seen = len(datasets)

        for ds_ref in datasets:
            try:
                ds_id = ds_ref.get("id")
                if not ds_id:
                    continue

                schema = await _fetch_json(
                    client, f"{base_url.rstrip('/')}/datasets/{ds_id}", headers
                )
                if not schema:
                    report.errors.append(f"describe failed for {ds_id}")
                    continue

                samples_payload = await _fetch_json(
                    client,
                    f"{base_url.rstrip('/')}/datasets/{ds_id}/sample"
                    f"?n={settings.sample_rows_for_profile}",
                    headers,
                )
                sample_rows: List[Dict[str, Any]] = (
                    (samples_payload or {}).get("rows") or []
                )

                source_id = schema.get("source_id") or ds_ref.get("source_id") or tool_id

                existing = await _load_existing(
                    catalogue_col,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    dataset_id=ds_id,
                )

                overrides = overrides_by_source.get(source_id)

                entry, enriched = await _build_entry(
                    settings=settings,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    schema=schema,
                    sample_rows=sample_rows,
                    mcp_base_url=base_url,
                    mcp_tool_id=tool_id,
                    existing=existing,
                    overrides=overrides,
                )

                # Stamp RBAC dept scoping from the discovery registry (best-effort).
                scope = scoping_by_source.get(source_id) or {}
                entry.dept_id = scope.get("dept_id")
                entry.public_within_org = bool(scope.get("public_within_org"))

                await catalogue_col.update_one(
                    {
                        "tenant_id": entry.tenant_id,
                        "source_id": entry.source_id,
                        "dataset_id": entry.dataset_id,
                    },
                    {"$set": entry.model_dump()},
                    upsert=True,
                )
                report.datasets_written += 1
                # Recorded only on a successful write, and only consulted by the
                # prune when this report carries NO errors — so a dataset that
                # exists but failed to describe can never look "gone".
                report.seen_dataset_ids.setdefault(source_id, []).append(ds_id)
                if enriched:
                    report.datasets_enriched += 1
                indexed_entries.append(entry.model_dump())
            except Exception as exc:
                logger.warning("crawl error on %s/%s: %s", tool_id, ds_ref.get("id"), exc)
                report.errors.append(f"{ds_ref.get('id')}: {exc}")

    # Recall index (fail-open — never blocks the crawl).
    try:
        import catalogue_vectors
        await catalogue_vectors.index_entries(settings, indexed_entries)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CRAWL] vector indexing failed: %s", exc)

    report.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return report


# ---------------------------------------------------------------------------
# Entry builder — classifier + (cached) LLM enrichment
# ---------------------------------------------------------------------------


async def _build_entry(
    *,
    settings: Settings,
    tenant_id: str,
    source_id: str,
    schema: Dict[str, Any],
    sample_rows: List[Dict[str, Any]],
    mcp_base_url: Optional[str],
    mcp_tool_id: Optional[str],
    existing: Optional[Dict[str, Any]],
    overrides: Optional[Dict[str, Any]],
) -> Tuple[CatalogueEntry, bool]:
    """Returns (entry, was_enriched). Skips the LLM when fingerprint is unchanged."""

    table_physical_name = str(schema.get("physical_name") or schema.get("name") or schema.get("id") or "")
    columns_in: List[Dict[str, Any]] = list(schema.get("columns") or [])

    # ----- Step 1: classifier (PII + semantic_type) ------------------------
    classified_cols: List[Dict[str, Any]] = []
    has_pii = False
    for c in columns_in:
        physical = str(c.get("physical_name") or c.get("name") or "")
        sample_values = [r.get(physical) for r in sample_rows if physical in r]
        sem, pii_from_values = classify_column(physical, sample_values)
        is_pii = bool(c.get("pii") or pii_from_values)
        has_pii = has_pii or is_pii
        classified_cols.append({
            **c,
            "physical_name": physical,
            "semantic_type": c.get("semantic_type") or sem,
            "pii": is_pii,
        })

    fingerprint = compute_schema_fingerprint(table_physical_name, classified_cols)

    # ----- Step 2: manual overrides (human-authored DESCRIPTIONS only) -----
    table_override, col_overrides = apply_manual_overrides(
        overrides=overrides,
        table_physical_name=table_physical_name,
        columns_in=classified_cols,
    )

    # ----- Step 4: assemble columns — NAMES AS-IS FROM SOURCE, NO LLM ------
    # data-discovery pulls the schema verbatim from the source system, which
    # owns the column/table names. `name` ALWAYS equals the physical
    # (queryable) identifier — we never call an LLM to rename cryptic columns:
    # a renamed name silently breaks the runtime query ("column <renamed>
    # does not exist") and can break apps tenant-wide. Only the DESCRIPTION is
    # enriched, and only from a human-authored manual override or the source's
    # own metadata (c.get("description")) — never a model guess.
    def _infer_column_kind(name: str, col_type: str, declared: Optional[str]) -> Optional[str]:
        """The source's declared column_kind wins; else a CONSERVATIVE inference
        for string columns whose name marks them as an S3/file reference. Marks
        image/document columns so image_analyze / doc_extract / runtime display
        know they are not plain text."""
        if declared:
            return declared
        t = (col_type or "").lower()
        # Only infer for string-ish columns. A KNOWN non-string type (int/date/
        # bool/etc.) cannot be a file reference, so skip it — otherwise an integer
        # like `image_id` would be misread as image_url. Unknown / empty types
        # fall through to the name check (we have no type to rule them out).
        if t and not any(s in t for s in ("char", "text", "string", "varchar", "clob", "json")):
            return None
        n = (name or "").lower()
        # Require an image-ish noun AND that the name looks like a reference
        # (ends in a path/url suffix, or ends in the noun itself, e.g.
        # `evidence_photo_url`, `vehicle_photo`). This excludes plain-text columns
        # that merely contain the word (e.g. `photographer_name`, `image_caption`).
        if any(k in n for k in ("photo", "image", "img", "scan", "picture")) and (
            n.endswith(("_url", "_uri", "_path", "_key"))
            or n.endswith(("photo", "image", "img", "scan", "picture"))
        ):
            return "image_url"
        if n.endswith(("_doc", "_document", "_attachment", "_pdf", "_file")) or any(
            k in n for k in ("document_url", "attachment_url", "doc_url", "report_url", "_pdf_url")
        ):
            return "document_url"
        return None

    def _infer_mime_hint(
        name: str, declared: Optional[str], kind: Optional[str]
    ) -> Optional[str]:
        """Coarse content-type hint (declared wins). Only the unambiguous PDF case
        is inferred from the name; everything else stays None and the fetch-time
        content-type sniff decides. A hint, never authoritative."""
        if declared:
            return declared
        n = (name or "").lower()
        if "pdf" in n:
            return "application/pdf"
        return None

    was_enriched = False
    cols_out: List[CatalogueColumn] = []
    for c in classified_cols:
        physical = c["physical_name"]
        manual = col_overrides.get(physical)
        description = (manual.get("description") if manual else None) or c.get("description")
        _declared_col_name = str(c.get("name") or "")
        cols_out.append(CatalogueColumn(
            name=physical,
            physical_name=physical,
            # BA-authored display name from describe — carried additively (name
            # stays == physical because url_columns/role matching keys on it).
            display_name=(_declared_col_name if _declared_col_name and _declared_col_name != physical else None),
            type=str(c.get("type") or ""),
            semantic_type=c.get("semantic_type"),
            column_kind=(_ck := _infer_column_kind(physical, c.get("type"), c.get("column_kind"))),
            mime_hint=_infer_mime_hint(physical, c.get("mime_hint"), _ck),
            nullable=bool(c.get("nullable", True)),
            pii=bool(c.get("pii")),
            sensitivity=c.get("sensitivity"),
            distinct_values=c.get("distinct_values"),
            range=c.get("range"),
            description=description,
            is_primary_key=bool(c.get("is_primary_key")),
            is_foreign_key=bool(c.get("is_foreign_key")),
            foreign_ref=c.get("foreign_ref"),
            artifact_role=c.get("artifact_role"),      # fraud ontology — verbatim from source
            reuse_policy=c.get("reuse_policy"),
            mapping_source="manual" if manual else "physical",
            mapping_confidence=None,
        ))

    # ----- Step 5: assemble table-level fields (name as-is) ---------------
    table_name = table_physical_name
    # BA-authored dataset display name from describe — additive, never replaces
    # the physical `name` (planner/binding keys on physical).
    _declared_tbl_name = str(schema.get("name") or "")
    table_display_name = (
        _declared_tbl_name
        if _declared_tbl_name and _declared_tbl_name != table_physical_name
        else None
    )
    if table_override:
        table_desc = table_override.get("description") or schema.get("description")
        mapping_status = "overridden"
        entry_mapping_source = "manual"
    else:
        table_desc = schema.get("description")
        mapping_status = "n/a"
        entry_mapping_source = "physical"

    # ----- Step 7: write_actions passthrough ------------------------------
    write_actions = [
        CatalogueWriteAction(
            id=str(w.get("id") or ""),
            verb=str(w.get("verb") or ""),
            description=w.get("description"),
            input_schema=dict(w.get("input_schema") or {}),
            # Carry the MCP's write allow-list. The sibling execution details
            # (method/endpoint/sql_template/key_fields/idempotency_key_field)
            # are deliberately NOT carried — the MCP owns execution — but this
            # one is authz the builder must see to avoid publishing an app its
            # users can't run.
            roles_allowed_write=list(w.get("roles_allowed_write") or []),
        )
        for w in (schema.get("write_actions") or [])
        if w.get("id")
    ]

    return (
        CatalogueEntry(
            tenant_id=tenant_id,
            source_id=source_id,
            dataset_id=str(schema.get("id") or ""),
            name=table_name,
            physical_name=table_physical_name,
            display_name=table_display_name,
            kind=str(schema.get("kind") or "semantic"),
            description=table_desc,
            columns=cols_out,
            relationships=list(schema.get("relationships") or []),
            read_via=dict(schema.get("read_via") or {}),
            input_schema=dict(schema.get("input_schema") or {}),
            write_actions=write_actions,
            has_pii=has_pii,
            decision_history=schema.get("decision_history"),
            fraud_screening=schema.get("fraud_screening"),   # ontology → builder auto-wiring
            domain=schema.get("domain"),                     # ontology → locale packs + vertical defaults
            value_semantics=schema.get("value_semantics"),   # ontology → ROI value spine
            organization=schema.get("organization"),         # ontology → app display identity
            mandatory_when_used=schema.get("mandatory_when_used"),  # ontology → required-lookup default
            row_count_approx=schema.get("row_count_approx"),
            samples_redacted=schema.get("samples_redacted"),
            mcp_base_url=mcp_base_url,
            mcp_tool_id=mcp_tool_id,
            # OUR crawl time (re-crawl scheduling). The source's own data
            # freshness rides source_last_refreshed_at — overwriting the same
            # field name with utcnow() silently destroyed it.
            last_refreshed_at=datetime.utcnow(),
            source_last_refreshed_at=schema.get("last_refreshed_at"),
            last_crawl_status="ok",
            mapping_status=mapping_status,
            mapping_source=entry_mapping_source,
            schema_fingerprint=fingerprint,
        ),
        was_enriched,
    )




# ---------------------------------------------------------------------------
# Top-level crawl pass — parallel fan-out across MCPs
# ---------------------------------------------------------------------------


async def discover_semantic_sources(
    *,
    settings: Settings,
    catalogue_col: AsyncIOMotorCollection,
    semantic_tools: List[Dict[str, Any]],
    tenant_id: str,
) -> CrawlReport:
    """Platform-side discovery of ``kind=semantic`` sources (the RAG short-circuit).

    Semantic (RAG) sources are answered by the Citra-Service platform reader,
    NOT the dept-MCP — so the MCP doesn't enumerate them via ``/datasets`` and the
    catalogue can't learn them from the MCP crawl. They ARE advertised in the
    DISCOVERY registry (``source_type == "semantic"`` tools, carrying dept/org
    scope + ``public_within_org`` + ``rag_collection``) — both MCP-published and
    platform-native dept SOP libraries. So the catalogue learns them from the
    discovery tool list (the retired central ``dept_sources`` registry is no
    longer read). ``dataset_id`` is the bare ``source_id`` — identical to the id
    the MCP's own single-dataset fallback used, so a previously-bound dataset
    keeps resolving.

    Fail-loud per source (a bad tool is logged + reported), but never raises: one
    broken semantic source must not sink the structured crawl."""
    report = CrawlReport(mcp_tool_id="__semantic__")
    indexed: List[Dict[str, Any]] = []
    for tool in semantic_tools or []:
        source_id = tool.get("source_id")
        try:
            if not source_id:
                continue                   # malformed registration
            report.datasets_seen += 1
            dept_ids = tool.get("dept_ids") or []
            entry = CatalogueEntry(
                tenant_id=tenant_id,
                source_id=source_id,
                dataset_id=source_id,      # a semantic corpus == one dataset (matches the MCP fallback id)
                name=tool.get("name") or source_id,
                kind="semantic",
                description=tool.get("description"),
                columns=[],
                dept_id=dept_ids[0] if dept_ids else None,
                public_within_org=bool(tool.get("public_within_org")),
                mapping_status="n/a",      # no physical→semantic column mapping for a corpus
                last_crawl_status="ok",
            )
            await catalogue_col.update_one(
                {"tenant_id": tenant_id, "source_id": source_id, "dataset_id": source_id},
                {"$set": entry.model_dump()},
                upsert=True,
            )
            report.datasets_written += 1
            report.seen_dataset_ids.setdefault(source_id, []).append(source_id)
            indexed.append(entry.model_dump())
        except Exception as exc:  # noqa: BLE001 — one bad row must not sink the pass
            logger.warning("[SEMANTIC-DISCOVERY] failed on source=%s: %s", source_id, exc)
            report.errors.append(f"{source_id}: {exc}")

    # Recall index so the builder's catalogue vector-search surfaces semantic
    # sources too (fail-open — never blocks discovery).
    try:
        import catalogue_vectors
        await catalogue_vectors.index_entries(settings, indexed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SEMANTIC-DISCOVERY] vector indexing failed: %s", exc)

    logger.info("[SEMANTIC-DISCOVERY] tenant=%s seen=%d written=%d",
                tenant_id, report.datasets_seen, report.datasets_written)
    return report


async def _prune_orphaned_entries(
    *,
    settings: Settings,
    catalogue_col: AsyncIOMotorCollection,
    tenant_id: str,
    live_source_ids: Set[str],
    seen_by_source: Dict[str, Set[str]],
) -> int:
    """Delete catalogue rows the registry no longer backs. Returns rows deleted.

    The catalogue was upsert-only. A source dropped from sources.json (or a table
    dropped from a live source) kept its row forever and ``/catalogue`` kept
    SERVING it — so the builder could wire an app to a dataset the MCP no longer
    answers, which only fails later at ``/run``. discovery already deactivates the
    retired *tool*; this is that same reconcile for the *catalogue*.

    A row is orphaned when either:
      1. its source is no longer registered in discovery (the source retired), or
      2. its source crawled cleanly this pass and did not serve the dataset
         (a table dropped from a still-live source).

    Deliberately conservative, because a false prune deletes a LIVE dataset while
    a missed one is merely stale:
      * an empty registry never prunes — that's an outage, not a retirement;
      * (2) is skipped for any source whose crawl reported an error, since a pass
        that failed part-way never saw everything that source serves.
    """
    if not live_source_ids:
        # Belt and braces: crawl_all already returns early on an empty registry,
        # and list_registered_mcps raises rather than returning [] on failure. But
        # this is a destructive path — if a refactor ever lands us here with an
        # empty set, delete NOTHING and say so.
        logger.error(
            "[PRUNE] refusing to prune tenant=%s: discovery lists NO registered "
            "sources. That is an outage or a misconfiguration, not a retirement — "
            "pruning on it would delete the entire catalogue.", tenant_id,
        )
        return 0

    doomed: List[Dict[str, str]] = []
    cursor = catalogue_col.find(
        {"tenant_id": tenant_id}, {"source_id": 1, "dataset_id": 1}
    )
    async for row in cursor:
        sid, did = row.get("source_id"), row.get("dataset_id")
        if not sid or not did:
            continue
        if sid not in live_source_ids:
            reason = "source no longer registered in discovery"
        elif sid in seen_by_source and did not in seen_by_source[sid]:
            reason = "source no longer serves this dataset"
        else:
            continue
        doomed.append({"source_id": sid, "dataset_id": did, "reason": reason})

    if not doomed:
        return 0

    for d in doomed:
        # One line per row: the catalogue keeps no tombstone, so this log IS the
        # record of what was removed and why.
        logger.info("[PRUNE] tenant=%s removing %s (%s)",
                    tenant_id, d["dataset_id"], d["reason"])
    res = await catalogue_col.delete_many({
        "tenant_id": tenant_id,
        "$or": [
            {"source_id": d["source_id"], "dataset_id": d["dataset_id"]} for d in doomed
        ],
    })
    removed = int(res.deleted_count)
    logger.info("[PRUNE] tenant=%s removed %d orphaned catalogue row(s)", tenant_id, removed)

    # Mongo is the served copy and it's already correct; a failure here costs
    # recall, not correctness (see catalogue_vectors.delete_entries).
    try:
        import catalogue_vectors
        await catalogue_vectors.delete_entries(
            settings, [(tenant_id, d["source_id"], d["dataset_id"]) for d in doomed]
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[PRUNE] index cleanup failed (stale vectors remain): %s", exc)
    return removed


async def crawl_all(
    *,
    settings: Settings,
    catalogue_col: AsyncIOMotorCollection,
    auth_header: Optional[str],
    tenant_id: str,
) -> Tuple[List[CrawlReport], int, int]:
    """Crawl every registered MCP in parallel (bounded), discover semantic
    sources platform-side, then prune rows the registry no longer backs.

    Returns ``(reports, total_datasets_written, total_pruned)``."""
    mcps = await list_registered_mcps(settings, auth_header)
    # Semantic (RAG) sources advertise themselves in discovery as source_type
    # =="semantic" tools (MCP-published or platform-native dept libraries). Peel
    # them off the tool list — they're catalogued platform-side, not MCP-crawled.
    semantic_tools = [
        m for m in mcps
        if str(m.get("source_type") or "").strip().lower() == "semantic"
    ]
    # Per-source RBAC scope (dept_id + public_within_org) from the discovery tool
    # list — one tool per source, so this covers every source on a collapsed MCP.
    scoping_by_source = _scoping_from_tools(mcps)
    if not mcps:
        # No tools registered at all ⇒ nothing to crawl and no semantic sources.
        # Note we do NOT prune here: an empty registry is an outage or a
        # not-yet-registered bring-up far more often than it is "every source was
        # retired", and pruning on it would delete the tenant's whole catalogue.
        sem_only = await discover_semantic_sources(
            settings=settings, catalogue_col=catalogue_col,
            semantic_tools=semantic_tools, tenant_id=tenant_id,
        )
        return [sem_only], sem_only.datasets_written, 0

    # discovery advertises one tool PER SOURCE (so the routing LLM can pick a
    # data domain), but sources on the same physical MCP share one base URL,
    # and that MCP's /datasets is server-wide (returns ALL its sources). Crawl
    # each distinct MCP exactly ONCE — keyed by base URL — else we re-crawl,
    # re-describe, re-sample and re-EMBED the same datasets N times (N =
    # sources per MCP). Per-source dept_id/visibility is unaffected: it's
    # applied per-dataset from `scoping_by_source` (built from the discovery tool
    # list, keyed by each dataset's own source_id), not from the collapsed tool
    # record. A tool with no resolvable base URL is kept so crawl_mcp reports the
    # error loudly rather than being dropped.
    unique_by_base: Dict[str, Dict[str, Any]] = {}
    no_base: List[Dict[str, Any]] = []
    for m in mcps:
        # Semantic (RAG) sources are NOT crawled here — they carry no /datasets and
        # (post RAG short-circuit) advertise an EMPTY query_endpoint. They are
        # catalogued separately by discover_semantic_sources below. Skipping them
        # avoids a spurious "no base_url" error per semantic tool.
        if str(m.get("source_type") or "").strip().lower() == "semantic":
            continue
        base = _resolve_base_url(m)
        if not base:
            no_base.append(m)
        elif base not in unique_by_base:
            unique_by_base[base] = m
    crawl_targets = list(unique_by_base.values()) + no_base
    if len(crawl_targets) < len(mcps):
        logger.info(
            "crawl_all: %d registered source-tools collapsed to %d distinct MCP(s) "
            "(one-source-one-tool registration; /datasets is server-wide)",
            len(mcps), len(crawl_targets),
        )

    sem = asyncio.Semaphore(max(1, settings.crawl_max_parallel_mcps))

    async def _bounded(m: Dict[str, Any]) -> CrawlReport:
        async with sem:
            try:
                return await crawl_mcp(
                    m,
                    settings=settings,
                    catalogue_col=catalogue_col,
                    scoping_by_source=scoping_by_source,
                    auth_header=auth_header,
                    tenant_id=tenant_id,
                )
            except TenantMismatchError:
                # A tenant mismatch is a configuration/correctness error, not a
                # single-MCP data hiccup — do NOT swallow it into a per-MCP
                # report. Propagate so the whole crawl fails loud.
                raise
            except Exception as exc:
                tool_id = m.get("tool_id") or m.get("id") or "unknown"
                logger.warning("crawl_mcp(%s) raised: %s", tool_id, exc)
                rep = CrawlReport(mcp_tool_id=str(tool_id))
                rep.errors.append(str(exc))
                return rep

    reports: List[CrawlReport] = await asyncio.gather(*(_bounded(m) for m in crawl_targets))
    # Platform-side semantic-source discovery (RAG short-circuit): semantic
    # sources aren't MCP-crawled, so the catalogue learns them from the discovery
    # tool list (source_type=="semantic") here — once per crawl, org-wide.
    sem_report = await discover_semantic_sources(
        settings=settings, catalogue_col=catalogue_col,
        semantic_tools=semantic_tools, tenant_id=tenant_id,
    )
    reports.append(sem_report)
    total_written = sum(r.datasets_written for r in reports)

    # Retire rows the registry no longer backs. discovery is the authority on
    # which sources exist (it already deactivates a retired tool); the crawl
    # reports are the authority on which datasets each live source still serves.
    live_source_ids: Set[str] = {
        str(m["source_id"]) for m in mcps if m.get("source_id")
    }
    seen_by_source: Dict[str, Set[str]] = {}
    degraded: Set[str] = set()
    for rep in reports:
        if rep.errors:
            # This pass didn't see everything the source serves, so "not seen"
            # here does not mean "gone". Bank the sources it touched as degraded
            # rather than trusting a partial view of them.
            degraded.update(rep.seen_dataset_ids.keys())
            continue
        for sid, dids in rep.seen_dataset_ids.items():
            seen_by_source.setdefault(sid, set()).update(dids)
    # A source seen by BOTH a clean and a failed report (only possible if two
    # MCPs serve the same source_id) must not be pruned off the clean half's
    # partial view — drop it rather than delete rows the failed half still has.
    for sid in degraded:
        seen_by_source.pop(sid, None)
    total_pruned = await _prune_orphaned_entries(
        settings=settings,
        catalogue_col=catalogue_col,
        tenant_id=tenant_id,
        live_source_ids=live_source_ids,
        seen_by_source=seen_by_source,
    )
    return reports, total_written, total_pruned
