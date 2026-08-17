# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for POST /apps/{slug}/tool/{tool_name}.

The endpoint lets the UI invoke a ``tools_v2`` tool directly (no LLM)
when the BA has wired a ``panel.tool_buttons[]`` entry for it. The
gating chain is:

  1. user JWT + tenant check
  2. panel exists on the AppSpec
  3. tool_name is in *that panel's* ``tool_buttons`` allowlist
  4. tool exists in agent_spec.tools_v2 and its kind is enabled
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

JWT_SECRET = "invoke-tool-test-secret"
SIGNING_KEY = "invoke-tool-signing-key"

os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")


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


def _seed_app_with_button(*, tool_button_name: str, tools_v2: list[dict]):
    """Build minimal app_doc + agent_doc so the route's loaders succeed."""
    app_spec = {
        "slug": "demo-app",
        "title": "Demo",
        "agent_id": "agent-1",
        "audience": "org",  # org-wide so any user in tenant=bajaj can run it
        "panels": [
            {
                "id": "claim_form",
                "type": "form",
                "schema_ref": "claim_form",
                "tool_buttons": [
                    {
                        "label": "Refresh",
                        "tool_name": tool_button_name,
                        "args": {"baked": "in"},
                    }
                ],
            }
        ],
    }
    apps = _MemCol(
        [
            {
                "slug": "demo-app",
                "app_id": "app-1",
                "agent_id": "agent-1",
                "owner": "u_owner",
                "tenant_id": "bajaj",
                "status": "published",
                "app_spec": app_spec,
            }
        ]
    )
    agents = _MemCol(
        [
            {
                "agent_id": "agent-1",
                "tenant_id": "bajaj",
                "agent_spec": {
                    "agent_id": "agent-1",
                    "name": "A",
                    "system_prompt": "Always call validate_form first.",
                    "tools_v2": tools_v2,
                },
            }
        ]
    )
    return apps, agents


from contextlib import contextmanager


@contextmanager
def _make_client(monkeypatch, apps, agents):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("SMART_APP_INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv(
        "SMART_APP_SERVICE_CALLBACK_URL", "https://smart.example/"
    )
    # OCR off, MCP / workflow off → only validate_form kind dispatches.
    monkeypatch.setenv("VISION_BASE_URL", "")
    monkeypatch.setenv("VISION_API_KEY", "")
    monkeypatch.setenv("VISION_MODEL", "")

    import importlib
    import config as _config
    importlib.reload(_config)
    import internal_routes as _internal_routes
    importlib.reload(_internal_routes)
    import main as _main
    importlib.reload(_main)

    monkeypatch.setattr(_main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(_main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(_main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_pending_runs_col", _MemCol(), raising=False)
    # Kill-switch (halt/pause) collection. get_control_col() raises
    # "Database not initialised" without it, and the runtime consults the
    # kill switch on every run — so any test that reaches execute_run dies
    # on infrastructure rather than on the behaviour it is asserting.
    # Empty = nothing halted, which is the normal state.
    monkeypatch.setattr(_main, "_control_col", _MemCol(), raising=False)

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
        monkeypatch.undo()
        importlib.reload(_config)
        importlib.reload(_internal_routes)
        importlib.reload(_main)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invoke_tool_validate_form_happy_path(monkeypatch):
    """Button-bound validate_form: dispatcher runs in-process, returns
    {ok, missing, invalid}. Caller args (form values) override the
    button's pre-baked args by key."""
    apps, agents = _seed_app_with_button(
        tool_button_name="vf",
        tools_v2=[
            {
                "kind": "validate_form",
                "name": "vf",
                "schema_ref": "claim_form",
            }
        ],
    )
    with _make_client(monkeypatch, apps, agents) as client:
        r = client.post(
            "/apps/demo-app/tool/vf",
            headers={"Authorization": f"Bearer {_mint_user_jwt()}"},
            json={
                "panel_id": "claim_form",
                "arguments": {"form": {"policy_number": "P123"}},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tool_name"] == "vf"
        assert body["panel_id"] == "claim_form"
        assert "ok" in body["result"]


def test_invoke_tool_rejects_unknown_panel(monkeypatch):
    apps, agents = _seed_app_with_button(
        tool_button_name="vf",
        tools_v2=[{"kind": "validate_form", "name": "vf", "schema_ref": "claim_form"}],
    )
    with _make_client(monkeypatch, apps, agents) as client:
        r = client.post(
            "/apps/demo-app/tool/vf",
            headers={"Authorization": f"Bearer {_mint_user_jwt()}"},
            json={"panel_id": "ghost", "arguments": {}},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "panel_not_found"


def test_invoke_tool_rejects_tool_not_in_panel_allowlist(monkeypatch):
    """Even if the tool exists in agent_spec.tools_v2, if the *panel's*
    tool_buttons list doesn't include it the call must be rejected.
    This is the defence against a leaked /apps/{slug}/tool/{name}
    URL escaping the panel scope."""
    apps, agents = _seed_app_with_button(
        tool_button_name="vf",
        tools_v2=[
            {"kind": "validate_form", "name": "vf", "schema_ref": "claim_form"},
            {"kind": "validate_form", "name": "other_tool", "schema_ref": "claim_form"},
        ],
    )
    with _make_client(monkeypatch, apps, agents) as client:
        r = client.post(
            "/apps/demo-app/tool/other_tool",
            headers={"Authorization": f"Bearer {_mint_user_jwt()}"},
            json={"panel_id": "claim_form", "arguments": {}},
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "tool_not_in_panel_allowlist"


def test_invoke_tool_rejects_unauthenticated(monkeypatch):
    apps, agents = _seed_app_with_button(
        tool_button_name="vf",
        tools_v2=[{"kind": "validate_form", "name": "vf", "schema_ref": "claim_form"}],
    )
    with _make_client(monkeypatch, apps, agents) as client:
        r = client.post(
            "/apps/demo-app/tool/vf",
            json={"panel_id": "claim_form", "arguments": {}},
        )
        assert r.status_code in (401, 403)
