# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""call_citra_semantic_search — interactive vs service-auth (RAG short-circuit).

Interactive callers send their end-user JWT as-is; an agent/trigger run with no
user identity mints a short-lived org-scoped `semantic_service` token (when an
`org_id` is available) so the read is authorized + dept-scoped server-side.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import proxy_clients  # noqa: E402
from proxy_clients import ProxyError, call_citra_semantic_search  # noqa: E402


class _FakeResp:
    status_code = 200
    text = ""

    def json(self):
        return {"source_id": "s", "dept_id": "ops", "count": 0, "chunks": []}


class _FakeClient:
    def __init__(self, cap):
        self.cap = cap

    async def post(self, url, json=None, headers=None, timeout=None):
        self.cap.update(url=url, json=json, headers=headers)
        return _FakeResp()


def _settings():
    import types
    return types.SimpleNamespace(citra_service_url="http://citra.test",
                                 mcp_call_timeout_seconds=5)


@pytest.mark.asyncio
async def test_interactive_uses_end_user_jwt_as_is(monkeypatch):
    cap = {}
    monkeypatch.setattr(proxy_clients, "get_http_client", lambda: _FakeClient(cap))
    minted = {"called": False}
    monkeypatch.setattr("citra_auth.mint_semantic_read_token",
                        lambda **kw: minted.__setitem__("called", True) or "SHOULD_NOT_MINT")
    await call_citra_semantic_search(
        settings=_settings(), user_jwt="real.user.jwt", source_id="s", query="q",
        org_id="acme",
    )
    assert cap["headers"]["Authorization"] == "Bearer real.user.jwt"
    assert minted["called"] is False           # a real user JWT is never re-minted


@pytest.mark.asyncio
async def test_service_run_mints_semantic_token(monkeypatch):
    cap = {}
    monkeypatch.setattr(proxy_clients, "get_http_client", lambda: _FakeClient(cap))
    monkeypatch.setattr("citra_auth.mint_semantic_read_token",
                        lambda **kw: f"MINTED:{kw['org_id']}:{kw.get('on_behalf_of_user_id')}")
    await call_citra_semantic_search(
        settings=_settings(), user_jwt=None, org_id="acme-power",
        on_behalf_of="agent-run:x", source_id="s", query="q",
    )
    assert cap["headers"]["Authorization"] == "Bearer MINTED:acme-power:agent-run:x"


@pytest.mark.asyncio
async def test_service_token_without_user_is_replaced_by_mint(monkeypatch):
    # a citra-app-runtime service token carries no user identity → mint instead
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"scope": "citra-app-runtime:x"}).encode()).decode()
    svc = f"h.{payload}.s"
    cap = {}
    monkeypatch.setattr(proxy_clients, "get_http_client", lambda: _FakeClient(cap))
    monkeypatch.setattr("citra_auth.mint_semantic_read_token", lambda **kw: "MINTED")
    await call_citra_semantic_search(
        settings=_settings(), user_jwt=svc, org_id="acme", source_id="s", query="q",
    )
    assert cap["headers"]["Authorization"] == "Bearer MINTED"


@pytest.mark.asyncio
async def test_no_identity_and_no_org_fails_loud(monkeypatch):
    monkeypatch.setattr(proxy_clients, "get_http_client",
                        lambda: _FakeClient({}))    # never reached
    # Catch broadly + assert on `.code`: another test file can re-import
    # proxy_clients, giving ProxyError a second class identity that
    # `pytest.raises(ProxyError)` wouldn't match — the behaviour (fail loud with
    # this code) is what matters.
    try:
        await call_citra_semantic_search(
            settings=_settings(), user_jwt=None, org_id=None, source_id="s", query="q",
        )
        raise AssertionError("expected a fail-loud error, none raised")
    except Exception as ex:  # noqa: BLE001 — asserting the fail-loud contract
        if isinstance(ex, AssertionError):
            raise
        assert getattr(ex, "code", None) == "semantic_no_identity"
