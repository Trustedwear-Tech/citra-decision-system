# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for POST /apps/{slug}/runtime/token.

The endpoint mints a short-lived HMAC bearer the runtime engine uses to
call /smart-app/internal/* on behalf of a published app. The minted
token's ``tools`` claim must be the intersection of the app's published
``tools_v2`` kinds with what this deployment can actually serve (e.g.
vision_ocr drops out when OCR_ENABLED=False).
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

JWT_SECRET = "runtime-token-test-secret"
SIGNING_KEY = "runtime-token-signing-key"

os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")


# ---------------------------------------------------------------------------
# Stub Mongo collections
# ---------------------------------------------------------------------------


class _MemCol:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])

    async def find_one(self, q=None, *_a, **_kw):
        q = q or {}
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    async def insert_one(self, doc, *_a, **_kw):
        self.docs.append(dict(doc))

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
            def sort(self, *_a, **_kw):
                return self

            def limit(self, *_a, **_kw):
                return self

            def __aiter__(self):
                async def _g():
                    if False:
                        yield None

                return _g()

        return _Cur()

    async def count_documents(self, *_a, **_kw):
        return 0


def _mint_user_jwt(user_id: str = "u_owner", tenant_id: str = "bajaj") -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "tenant_id": tenant_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


from contextlib import contextmanager


@contextmanager
def _make_client(monkeypatch, *, ocr_enabled: bool, tool_kinds: list[str]):
    """Build a TestClient with seeded apps_col + agents_col."""
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("SMART_APP_INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv(
        "SMART_APP_SERVICE_CALLBACK_URL", "https://smart.example/"
    )
    if ocr_enabled:
        monkeypatch.setenv("VISION_BASE_URL", "https://vision.test/v1")
        monkeypatch.setenv("VISION_API_KEY", "vk-test")
        monkeypatch.setenv("VISION_MODEL", "qwen/qwen3-vl-32b-instruct")
    else:
        # Force-empty so pydantic-settings does not pick up values from
        # the local .env file. ocr_enabled = bool(all three set).
        monkeypatch.setenv("VISION_BASE_URL", "")
        monkeypatch.setenv("VISION_API_KEY", "")
        monkeypatch.setenv("VISION_MODEL", "")

    # MCP / RAG / workflow gate config. Tests that include kind="mcp"
    # in tool_kinds rely on mcp_enabled being True, which needs a
    # service api key configured.
    monkeypatch.setenv("MCP_SERVICE_API_KEY", "svc-key")
    monkeypatch.setenv("DISCOVERY_SERVICE_URL", "http://discovery.test")
    monkeypatch.setenv("WORKFLOW_SERVICE_URL", "http://citra.test")

    import importlib
    import config as _config
    importlib.reload(_config)
    import internal_routes as _internal_routes
    importlib.reload(_internal_routes)
    import main as _main
    importlib.reload(_main)

    # Build minimal AgentSpec.tools_v2 docs from kinds.
    tool_docs: list[dict] = []
    for kind in tool_kinds:
        if kind == "validate_form":
            tool_docs.append(
                {
                    "kind": "validate_form",
                    "name": "validate_intake",
                    "schema_ref": "/forms/intake",
                }
            )
        elif kind == "vision_ocr":
            tool_docs.append(
                {
                    "kind": "vision_ocr",
                    "name": "vision_ocr",
                    "purpose": "Extract text",
                }
            )
        elif kind == "mcp":
            tool_docs.append(
                {
                    "kind": "mcp",
                    "name": "mcp_lookup",
                    "server": "demo",
                    "tool": "lookup",
                }
            )

    apps = _MemCol(
        [
            {
                "slug": "demo-app",
                "agent_id": "agent-1",
                "owner": "u_owner",
                "tenant_id": "bajaj",
                "app_spec": {"audience": "org"},
            }
        ]
    )
    agents = _MemCol(
        [
            {
                "agent_id": "agent-1",
                "tenant_id": "bajaj",
                "agent_spec": {"tools_v2": tool_docs},
            }
        ]
    )

    monkeypatch.setattr(_main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(_main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(_main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_pending_runs_col", _MemCol(), raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    from fastapi.testclient import TestClient
    client = TestClient(_main.app)
    try:
        yield client
    finally:
        client.close()
        # Roll back env, then reload modules so other tests don't
        # inherit our mutated config/main module state.
        monkeypatch.undo()
        importlib.reload(_config)
        importlib.reload(_internal_routes)
        importlib.reload(_main)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_runtime_token_response_shape(monkeypatch):
    with _make_client(
        monkeypatch,
        ocr_enabled=True,
        tool_kinds=["validate_form", "vision_ocr", "mcp"],
    ) as c:
        r = c.post(
            "/apps/demo-app/runtime/token",
            headers={"Authorization": f"Bearer {_mint_user_jwt()}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"secret", "proxy_base_url", "expires_at", "tools"}
        assert body["secret"] and "." in body["secret"]
        assert body["proxy_base_url"].endswith("/smart-app/internal")
        assert body["expires_at"] > int(time.time())
        assert body["tools"] == ["validate_form", "vision_ocr", "mcp"]

        # The minted bearer must verify with our signing key.
        from internal_bearer import verify_internal_bearer
        claims = verify_internal_bearer(
            signing_key=SIGNING_KEY, bearer=body["secret"]
        )
        assert claims.kind == "runtime"
        assert claims.subject == "app:demo-app"
        assert claims.tenant_id == "bajaj"
        assert claims.tools == ["validate_form", "vision_ocr", "mcp"]


def test_runtime_token_drops_vision_ocr_when_disabled(monkeypatch):
    with _make_client(
        monkeypatch,
        ocr_enabled=False,
        tool_kinds=["validate_form", "vision_ocr", "mcp"],
    ) as c:
        r = c.post(
            "/apps/demo-app/runtime/token",
            headers={"Authorization": f"Bearer {_mint_user_jwt()}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "vision_ocr" not in body["tools"]
        assert body["tools"] == ["validate_form", "mcp"]


def test_runtime_token_404_for_outsider(monkeypatch):
    with _make_client(
        monkeypatch,
        ocr_enabled=True,
        tool_kinds=["validate_form"],
    ) as c:
        r = c.post(
            "/apps/demo-app/runtime/token",
            headers={
                "Authorization": f"Bearer {_mint_user_jwt(user_id='u_other', tenant_id='other')}"
            },
        )
        assert r.status_code == 404


def test_runtime_token_404_for_unknown_slug(monkeypatch):
    with _make_client(
        monkeypatch,
        ocr_enabled=True,
        tool_kinds=["validate_form"],
    ) as c:
        r = c.post(
            "/apps/nope/runtime/token",
            headers={"Authorization": f"Bearer {_mint_user_jwt()}"},
        )
        assert r.status_code == 404
