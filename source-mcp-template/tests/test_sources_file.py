# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""SOURCES_FILE loader — customer-side MCP reads its dept_sources registry from
a local JSON file instead of central Mongo (Wave 2 #7).

Pins:
  * _select_sources filters to this deployment (active + org + dept) and
    flattens, exactly like the Mongo path;
  * load_sources_from_file reads a JSON list OR {"sources": [...]} and swaps
    the registry;
  * a missing/malformed file FAILS LOUD (raises), never a silent empty registry;
  * load_sources dispatches to the file when SOURCES_FILE is set.

Pure/loader tests — no Mongo, no running MCP.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import router


def _src(source_id, *, org="acme", dept="operations", active=True, type_="structured"):
    # Default `structured`: the MCP is a structured-only agent (RAG short-circuit
    # drops semantic sources at load), so the loader-mechanics tests use sources
    # that actually reach the registry.
    # `description` + `connection` are required by registry_models (the doc marks
    # both required, and all 19 real sources carry them). The loader now validates
    # what it serves, so a fixture must look like a real source rather than the
    # minimum the old raw-dict loader happened to tolerate.
    src = {"source_id": source_id, "org_id": org, "dept_id": dept,
           "is_active": active, "type": type_, "name": source_id.title(),
           "description": f"{source_id} test source"}
    if type_ == "semantic":
        src["rag"] = {"milvus_collection": f"{source_id}_col"}
    else:
        src["connection"] = {"env_prefix": source_id.upper()}
    return src


# ── _select_sources (pure) ───────────────────────────────────────────────────
def test_select_filters_by_org_dept_and_active():
    docs = [
        _src("a", org="acme", dept="operations"),
        _src("b", org="acme", dept="finance"),        # wrong dept
        _src("c", org="beta", dept="operations"),      # wrong org
        _src("d", org="acme", dept="operations", active=False),  # inactive
    ]
    out = router._select_sources(docs, org_id="acme", dept_ids=["operations"])
    assert set(out) == {"a"}
    assert out["a"]["type"] == "structured" and out["a"]["dept_id"] == "operations"


def test_select_multi_dept_and_no_filter():
    docs = [_src("a", dept="operations"), _src("b", dept="maintenance")]
    both = router._select_sources(docs, org_id="acme", dept_ids=["operations", "maintenance"])
    assert set(both) == {"a", "b"}
    # empty dept filter = no dept restriction (org still applies)
    alld = router._select_sources(docs, org_id="acme", dept_ids=[])
    assert set(alld) == {"a", "b"}


# ── load_sources_from_file ───────────────────────────────────────────────────
def _patch_cfg(monkeypatch, path, org="acme", depts=("operations",), sources_json=""):
    cfg = SimpleNamespace(sources_file=str(path) if path else "", org_id=org,
                          dept_ids=list(depts), sources_json=sources_json)
    monkeypatch.setattr(router, "get_settings", lambda: cfg)
    router._sources.clear()


def test_load_from_file_list(tmp_path, monkeypatch):
    f = tmp_path / "sources.json"
    f.write_text(json.dumps([_src("a"), _src("b", dept="finance")]), encoding="utf-8")
    _patch_cfg(monkeypatch, f)
    out = router.load_sources_from_file()
    assert [s["source_id"] for s in out] == ["a"]          # finance filtered out
    assert set(router._sources) == {"a"}                   # registry swapped


def test_load_from_file_sources_wrapper(tmp_path, monkeypatch):
    f = tmp_path / "sources.json"
    f.write_text(json.dumps({"sources": [_src("a")]}), encoding="utf-8")
    _patch_cfg(monkeypatch, f)
    assert [s["source_id"] for s in router.load_sources_from_file()] == ["a"]


def test_missing_file_fails_loud(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, tmp_path / "nope.json")
    with pytest.raises(RuntimeError, match="could not be read"):
        router.load_sources_from_file()


def test_malformed_file_fails_loud(tmp_path, monkeypatch):
    f = tmp_path / "bad.json"
    f.write_text("not json {{{", encoding="utf-8")
    _patch_cfg(monkeypatch, f)
    with pytest.raises(RuntimeError, match="could not be read"):
        router.load_sources_from_file()


def test_wrong_shape_fails_loud(tmp_path, monkeypatch):
    f = tmp_path / "obj.json"
    f.write_text(json.dumps({"not_sources": 1}), encoding="utf-8")
    _patch_cfg(monkeypatch, f)
    with pytest.raises(RuntimeError, match="must be a JSON list"):
        router.load_sources_from_file()


# ── SOURCES_JSON (inline, file-free hosts) ───────────────────────────────────
def test_load_from_inline_json_list(monkeypatch):
    # No SOURCES_FILE — registry supplied inline via SOURCES_JSON env, exactly
    # what a pure env-var host (Cloud Run / Container Apps / Fargate) injects.
    _patch_cfg(monkeypatch, None,
               sources_json=json.dumps([_src("a"), _src("b", dept="finance")]))
    out = router.load_sources_from_file()
    assert [s["source_id"] for s in out] == ["a"]          # finance filtered out
    assert set(router._sources) == {"a"}


def test_load_from_inline_json_sources_wrapper(monkeypatch):
    _patch_cfg(monkeypatch, None, sources_json=json.dumps({"sources": [_src("a")]}))
    assert [s["source_id"] for s in router.load_sources_from_file()] == ["a"]


def test_file_wins_over_inline_json(tmp_path, monkeypatch):
    # Both set → SOURCES_FILE takes precedence (matches config docstring).
    f = tmp_path / "sources.json"
    f.write_text(json.dumps([_src("from_file")]), encoding="utf-8")
    _patch_cfg(monkeypatch, f, sources_json=json.dumps([_src("from_env")]))
    assert [s["source_id"] for s in router.load_sources_from_file()] == ["from_file"]


def test_malformed_inline_json_fails_loud(monkeypatch):
    _patch_cfg(monkeypatch, None, sources_json="not json {{{")
    with pytest.raises(RuntimeError, match="SOURCES_JSON could not be parsed"):
        router.load_sources_from_file()


def test_inline_json_wrong_shape_fails_loud(monkeypatch):
    _patch_cfg(monkeypatch, None, sources_json=json.dumps({"not_sources": 1}))
    with pytest.raises(RuntimeError, match="must be a JSON list"):
        router.load_sources_from_file()


# ── load_sources dispatcher ──────────────────────────────────────────────────
def test_dispatch_loads_from_file(tmp_path, monkeypatch):
    # load_sources() is file-only (the legacy Mongo mode was removed) — it reads
    # the configured SOURCES_FILE and returns its sources.
    import asyncio

    f = tmp_path / "sources.json"
    f.write_text(json.dumps([_src("a")]), encoding="utf-8")
    _patch_cfg(monkeypatch, f)

    out = asyncio.run(router.load_sources())
    assert [s["source_id"] for s in out] == ["a"]


# ── RAG short-circuit: semantic sources are dropped at load ──────────────────
def test_semantic_sources_excluded_at_load(tmp_path, monkeypatch):
    """Pure disconnect: the MCP is structured-only — a `type=semantic` source
    must NEVER enter the query registry (it's answered by Citra-Service). But it
    IS retained for discovery advertisement so chat can surface it."""
    f = tmp_path / "sources.json"
    f.write_text(json.dumps([
        _src("billing", type_="structured"),
        _src("policy_lib", type_="semantic"),      # RAG — dropped from /query
    ]), encoding="utf-8")
    _patch_cfg(monkeypatch, f)
    out = router.load_sources_from_file()
    ids = [s["source_id"] for s in out]
    assert ids == ["billing"]                       # semantic dropped from query set
    assert "policy_lib" not in router._sources      # never in the live query registry
    # …but retained so registration can advertise it to discovery (chat's SoT).
    sem = {s["source_id"] for s in router.get_semantic_sources()}
    assert sem == {"policy_lib"}


# ── Semantic sources are advertised to discovery with the RAG collection ─────
def test_semantic_registration_payload_has_rag_collection(monkeypatch):
    """A semantic source's discovery registration must carry source_type=semantic
    plus the derived Milvus collection (rag_collection) so the platform reader
    fetches it directly — never the MCP /query."""
    import registration

    cfg = SimpleNamespace(
        org_id="acme", dept_ids=["central_pmu"], mcp_api_key="k",
        mcp_public_base_url="http://mcp:8090", port=8090,
        resolved_instance_id="inst-1",
        collection_name_semantic=lambda sid, dept: f"demo_acme_{dept}_{sid}",
    )
    monkeypatch.setattr(registration, "get_settings", lambda: cfg)

    src = {"source_id": "policy_lib", "org_id": "acme", "dept_id": "central_pmu",
           "type": "semantic", "name": "Policy", "description": "SOPs"}
    payload = registration._build_registration_payload(src)

    assert payload["source_type"] == "semantic"
    assert payload["rag_collection"] == "demo_acme_central_pmu_policy_lib"

    # A structured source carries NO rag_collection.
    struct = {"source_id": "billing", "org_id": "acme", "dept_id": "billing_revenue",
              "type": "structured", "name": "Billing"}
    monkeypatch.setattr(registration, "get_settings",
                        lambda: SimpleNamespace(**{**cfg.__dict__, "dept_ids": ["billing_revenue"]}))
    assert "rag_collection" not in registration._build_registration_payload(struct)


def test_declared_rag_collection_wins_over_derived(monkeypatch):
    """A semantic source that DECLARES ``rag.milvus_collection`` opts into that
    exact collection (e.g. the SHARED ``mcp_dept_libraries``); the derived
    ``<prefix>_<dept>_<source>`` name is used only as a fallback. This is the
    single point of truth the platform reader reads and the ingest path writes."""
    import registration

    cfg = SimpleNamespace(
        org_id="acme", dept_ids=["central_pmu"], mcp_api_key="k",
        mcp_public_base_url="http://mcp:8090", port=8090,
        resolved_instance_id="inst-1",
        collection_name_semantic=lambda sid, dept: f"demo_acme_{dept}_{sid}",
    )
    monkeypatch.setattr(registration, "get_settings", lambda: cfg)

    src = {"source_id": "policy_lib", "org_id": "acme", "dept_id": "central_pmu",
           "type": "semantic", "name": "Policy", "description": "SOPs",
           "rag": {"milvus_collection": "mcp_dept_libraries"}}
    payload = registration._build_registration_payload(src)
    assert payload["rag_collection"] == "mcp_dept_libraries"     # declared wins

    # Empty/blank declaration falls back to the derived name.
    src_blank = {**src, "rag": {"milvus_collection": "  "}}
    assert registration._build_registration_payload(src_blank)["rag_collection"] \
        == "demo_acme_central_pmu_policy_lib"
