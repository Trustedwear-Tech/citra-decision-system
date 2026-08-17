# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for /smart-app/internal/{mcp,rag,workflow/run,workflow/runs/{id}}.

Validates:
- bearer scope enforcement (kind-level via ``tools`` claim)
- bearer binding enforcement (instance-level via ``bindings`` claim)
- ``X-User-JWT`` header requirement
- happy path with mocked upstream (discovery + dept-MCP + workflow engine)
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal_bearer import mint_internal_bearer  # noqa: E402

SIGNING_KEY = "proxy-routes-test-key"


class _StubCol:
    async def find_one(self, *_a, **_kw):
        return None

    async def insert_one(self, *_a, **_kw):
        class _R:
            inserted_id = "stub"
        return _R()

    async def update_one(self, *_a, **_kw):
        class _R:
            matched_count = 0
            modified_count = 0
            upserted_id = None
        return _R()

    async def create_index(self, *_a, **_kw):
        return None

    def find(self, *_a, **_kw):
        class _Cur:
            def sort(self, *_a, **_kw): return self
            def limit(self, *_a, **_kw): return self
            def __aiter__(self):
                async def _g():
                    if False:
                        yield None
                return _g()
        return _Cur()

    async def count_documents(self, *_a, **_kw):
        return 0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("SMART_APP_INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv("MCP_SERVICE_API_KEY", "svc-mcp-key")
    monkeypatch.setenv("DISCOVERY_SERVICE_URL", "http://discovery.test")
    monkeypatch.setenv("WORKFLOW_SERVICE_URL", "http://citra.test")

    import importlib
    import config as _config
    importlib.reload(_config)
    import discovery_cache as _disco
    importlib.reload(_disco)
    import proxy_clients as _pc
    importlib.reload(_pc)
    import internal_routes as _ir
    importlib.reload(_ir)
    import main as _main
    importlib.reload(_main)

    _disco.clear_cache()

    for attr in ("_apps_col", "_agents_col", "_build_sessions_col",
                 "_prompt_packs_col", "_skills_col", "_pending_runs_col"):
        if hasattr(_main, attr):
            monkeypatch.setattr(_main, attr, _StubCol(), raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    from fastapi.testclient import TestClient
    try:
        with TestClient(_main.app) as c:
            yield c
    finally:
        monkeypatch.undo()
        importlib.reload(_config)
        importlib.reload(_disco)
        importlib.reload(_pc)
        importlib.reload(_ir)
        importlib.reload(_main)


def _bearer(*, tools, bindings=None, kind="runtime", subject="app:demo"):
    return mint_internal_bearer(
        signing_key=SIGNING_KEY,
        kind=kind,
        subject=subject,
        tenant_id="t1",
        tools=tools,
        ttl_seconds=300,
        bindings=bindings or {},
    )


# ---------------------------------------------------------------------------
# /mcp
# ---------------------------------------------------------------------------


def test_mcp_requires_bearer(client):
    r = client.post("/smart-app/internal/mcp", json={"source_id": "s1"})
    assert r.status_code == 401


def test_mcp_rejects_bearer_without_kind_scope(client):
    b = _bearer(tools=["vision_ocr"])
    r = client.post(
        "/smart-app/internal/mcp",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1", "tool_name": "t1"},
    )
    assert r.status_code == 403
    assert "mcp" in r.text.lower()


def test_mcp_rejects_bearer_without_binding(client):
    """Bearer declares mcp kind but no binding for (s1, t1)."""
    b = _bearer(
        tools=["mcp"],
        bindings={"mcp": [["other-source", "other-tool"]]},
    )
    r = client.post(
        "/smart-app/internal/mcp",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1", "tool_name": "t1"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["error"] == "tool_binding_not_authorised"
    assert body["detail"]["source_id"] == "s1"


def test_mcp_requires_user_jwt_header(client):
    b = _bearer(tools=["mcp"], bindings={"mcp": [["s1", "t1"]]})
    r = client.post(
        "/smart-app/internal/mcp",
        headers={"Authorization": f"Bearer {b}"},
        json={"source_id": "s1", "tool_name": "t1", "query": "hi"},
    )
    assert r.status_code == 400
    assert "x-user-jwt" in r.text.lower()


def test_mcp_happy_path(client, monkeypatch):
    """Stub discovery + dept-MCP and confirm forwarding works."""
    b = _bearer(tools=["mcp"], bindings={"mcp": [["s1", "t1"]]})

    captured = {"calls": []}

    async def _fake_get(self, url, **kwargs):
        captured["calls"].append(("GET", url, kwargs))
        return httpx.Response(
            200,
            json=[
                {
                    "name": "Source 1",
                    "description": "x",
                    "source_id": "s1",
                    "query_endpoint": "http://dept1.test/query",
                    # NOTE: real discovery NEVER returns an api_key — it stores
                    # only api_key_hash. discovery_cache pins ResolvedSource
                    # .api_key to an explicit None for exactly that reason, so a
                    # stub that supplies one models a response the registry
                    # cannot produce. Left here (ignored) to document that.
                    "api_key": "dept1-key",
                    "source_type": "semantic",
                    "query_timeout_seconds": 30,
                }
            ],
            request=httpx.Request("GET", url),
        )

    async def _fake_post(self, url, **kwargs):
        captured["calls"].append(("POST", url, kwargs))
        return httpx.Response(
            200,
            json={"results": [{"text": "hello", "score": 0.9}], "total": 1},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    r = client.post(
        "/smart-app/internal/mcp",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "user-jwt-here"},
        json={"source_id": "s1", "tool_name": "t1", "query": "find x", "max_results": 5},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1

    # Discovery was called with the user JWT
    get_call = next(c for c in captured["calls"] if c[0] == "GET")
    assert "discovery.test/tools/available" in get_call[1]
    assert get_call[2]["headers"]["Authorization"] == "Bearer user-jwt-here"

    # dept-MCP got the resolved endpoint, dept api key, and X-User-JWT
    post_call = next(c for c in captured["calls"] if c[0] == "POST")
    assert post_call[1] == "http://dept1.test/query"
    # The SERVICE key, not a per-source one. Discovery cannot hand back a
    # per-dept key (hash only), so call_dept_mcp_query falls through to
    # settings.mcp_service_api_key — that fallback IS the intended auth, not a
    # degradation. Asserting "dept1-key" was asserting an unreachable path.
    assert post_call[2]["headers"]["Authorization"] == "Bearer svc-mcp-key"
    assert post_call[2]["headers"]["X-User-JWT"] == "user-jwt-here"
    assert post_call[2]["json"]["query"] == "find x"
    assert post_call[2]["json"]["max_results"] == 5
    assert post_call[2]["json"]["source_id"] == "s1"
    assert post_call[2]["json"]["tool_name"] == "t1"


def test_mcp_binding_with_wildcard_tool_name(client, monkeypatch):
    """Binding entry [source, None] matches any tool_name from that source."""
    b = _bearer(tools=["mcp"], bindings={"mcp": [["s1", None]]})

    async def _fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json=[{
                "name": "s",
                "description": "",
                "source_id": "s1",
                "query_endpoint": "http://d.test/query",
                "api_key": "k",
            }],
            request=httpx.Request("GET", url),
        )

    async def _fake_post(self, url, **kwargs):
        return httpx.Response(200, json={"ok": True},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    r = client.post(
        "/smart-app/internal/mcp",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1", "tool_name": "anything", "query": "x"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /rag
# ---------------------------------------------------------------------------


def test_rag_rejects_bearer_without_kind(client):
    b = _bearer(tools=["mcp"], bindings={"rag": ["s1"]})
    r = client.post(
        "/smart-app/internal/rag",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1", "query": "hi"},
    )
    assert r.status_code == 403


def test_rag_rejects_unbound_source(client):
    b = _bearer(tools=["rag"], bindings={"rag": ["other"]})
    r = client.post(
        "/smart-app/internal/rag",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1", "query": "hi"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_binding_not_authorised"


def test_rag_validates_query_required(client):
    b = _bearer(tools=["rag"], bindings={"rag": ["s1"]})
    r = client.post(
        "/smart-app/internal/rag",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1"},
    )
    assert r.status_code == 400


def test_rag_happy_path_routes_to_citra_not_mcp(client, monkeypatch):
    """RAG short-circuit: /internal/rag answers via Citra-Service /semantic/search
    (semantic corpus), NEVER the dept-MCP."""
    b = _bearer(tools=["rag"], bindings={"rag": ["s1"]})

    captured = {}

    async def _fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(
            200,
            json={"source_id": "s1", "dept_id": "ops", "count": 1,
                  "chunks": [{"text": "policy", "score": 0.5, "metadata": {}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    r = client.post(
        "/smart-app/internal/rag",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
        json={"source_id": "s1", "query": "find x", "top_k": 12},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert captured["url"].endswith("/semantic/search")   # Citra-Service, not the MCP
    assert captured["json"]["source_id"] == "s1"
    assert captured["json"]["query"] == "find x"
    assert captured["json"]["top_k"] == 12


# ---------------------------------------------------------------------------
# /catalogue (builder-only)
# ---------------------------------------------------------------------------


def test_catalogue_rejects_runtime_bearer(client):
    """Runtime bearers are tightly scoped to specific bindings; they must
    not be able to enumerate the tenant."""
    b = _bearer(kind="runtime", subject="app:demo", tools=["mcp"])
    r = client.get(
        "/smart-app/internal/catalogue",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "catalogue_builder_only"


def test_catalogue_requires_user_jwt(client):
    b = _bearer(kind="builder", subject="build:s1", tools=["mcp"])
    r = client.get(
        "/smart-app/internal/catalogue",
        headers={"Authorization": f"Bearer {b}"},
    )
    assert r.status_code == 400


def test_catalogue_happy_path(client, monkeypatch):
    """Catalogue forwards the user JWT to discovery and splits MCP vs RAG
    sources by source_type."""
    b = _bearer(kind="builder", subject="build:s1", tools=["mcp"])

    async def _fake_get(self, url, **kwargs):
        if "discovery.test/tools/available" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "source_id": "policies",
                        "name": "Policies KB",
                        "description": "Insurance policy documents",
                        "source_type": "semantic",
                        "tags": ["insurance"],
                    },
                    {
                        "source_id": "claims-db",
                        "name": "Claims DB",
                        "description": "Claims tool",
                        "source_type": "structured",
                    },
                ],
                request=httpx.Request("GET", url),
            )
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    r = client.get(
        "/smart-app/internal/catalogue",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "ba-user-jwt"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "workflows" not in body
    assert [s["source_id"] for s in body["rag_sources"]] == ["policies"]
    assert [s["source_id"] for s in body["mcp_sources"]] == ["claims-db"]
    assert body["errors"] == {"sources": None}


def test_catalogue_partial_failure_still_returns_other_lists(client, monkeypatch):
    """If discovery is down, surface the discovery error code rather than
    failing the whole call."""
    b = _bearer(kind="builder", subject="build:s1", tools=["mcp"])

    async def _fake_get(self, url, **kwargs):
        if "discovery.test/tools/available" in url:
            return httpx.Response(
                500, text="boom", request=httpx.Request("GET", url),
            )
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    r = client.get(
        "/smart-app/internal/catalogue",
        headers={"Authorization": f"Bearer {b}", "X-User-JWT": "u"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mcp_sources"] == []
    assert body["rag_sources"] == []
    assert body["errors"]["sources"] == "discovery_rejected"

