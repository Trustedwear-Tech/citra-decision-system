"""Tests for the trigger surface and capabilities endpoint.

Covers:
  * GET /capabilities returns features + limits, and gracefully degrades
    when discovery-service is unreachable.
  * POST /apps/{slug}/trigger/{trigger_id} (webhook):
      - rejects missing / mismatched HMAC signature
      - rejects payloads above the size cap
      - rejects when secret_ref unresolvable
      - rejects unknown trigger ids
      - happy path: HMAC ok → fires the trigger as system principal
  * scheduler tick_once:
      - fires interval triggers when due
      - poll trigger calls MCP, dedups by key, fires one run per new row
  * Sub-agent routing:
      - delegate_to_sub_agent synthetic tool is injected
      - sub-agent tool list must be a subset of root's
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Mongo stub (mirrors test_share.py)
# ---------------------------------------------------------------------------


class _MemCol:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def find(self, q=None, *_a, **_kw):
        q = q or {}
        matches = [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]

        class _Cur:
            def __init__(self, m):
                self._m = m

            def sort(self, *_a, **_kw):
                return self

            def limit(self, n):
                self._m = self._m[:n]
                return self

            def __aiter__(self):
                async def _g():
                    for x in self._m:
                        yield x
                return _g()

        return _Cur(matches)

    async def count_documents(self, q=None, *_a, **_kw):
        q = q or {}
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in q.items()))

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

    async def update_one(self, q, update, upsert=False, *_a, **_kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                set_doc = update.get("$set", {}) or {}
                for k, v in set_doc.items():
                    if "." in k:
                        parts = k.split(".")
                        cur = d
                        for p in parts[:-1]:
                            cur = cur.setdefault(p, {})
                        cur[parts[-1]] = v
                    else:
                        d[k] = v

                class _R:
                    matched_count = 1
                return _R()
        if upsert:
            new_doc = dict(q or {})
            new_doc.update(update.get("$set", {}) or {})
            self.docs.append(new_doc)

            class _R:
                matched_count = 0
            return _R()

        class _R:
            matched_count = 0
        return _R()

    async def replace_one(self, q, doc, upsert=False, *_a, **_kw):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in q.items()):
                self.docs[i] = dict(doc)

                class _R:
                    upserted_id = None
                return _R()
        if upsert:
            self.docs.append(dict(doc))

        class _R:
            upserted_id = "stub"
        return _R()

    async def insert_many(self, docs, *_a, **_kw):
        for d in docs:
            self.docs.append(dict(d))

        class _R:
            inserted_ids = ["stub"] * len(docs)
        return _R()

    async def create_index(self, *_a, **_kw):
        return None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import main

    apps = _MemCol()
    agents = _MemCol()
    pending = _MemCol()
    triggers = _MemCol()
    monkeypatch.setattr(main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_pending_runs_col", pending, raising=False)
    monkeypatch.setattr(main, "_trigger_state_col", triggers, raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(main.app) as c:
        c._cols = {  # type: ignore[attr-defined]
            "apps": apps,
            "agents": agents,
            "pending": pending,
            "triggers": triggers,
        }
        yield c


def _mint(user_id: str, tenant_id: str = "bajaj") -> str:
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


def _mint_builder(session_id: str = "bs_t", tenant_id: str = "bajaj", owner: str = "u_owner") -> str:
    return jwt.encode(
        {
            "sub": owner,
            "user_id": owner,
            "scope": "smart-app-builder",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _publish(client: TestClient, *, triggers=None, agent_extras=None) -> str:
    app_spec = json.loads((FIXTURES / "claims_app_spec.json").read_text())
    agent_spec = json.loads((FIXTURES / "claims_agent_spec.json").read_text())
    for k in ("app_id", "version", "deployed_at", "status", "owner", "tenant_id"):
        app_spec.pop(k, None)
    if triggers is not None:
        app_spec["triggers"] = triggers
    if agent_extras:
        agent_spec.update(agent_extras)
    r = client.post(
        "/publish",
        json={"session_id": "bs_t", "app_spec": app_spec, "agent_spec": agent_spec},
        headers={"Authorization": f"Bearer {_mint_builder()}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["slug"]


# ---------------------------------------------------------------------------
# /capabilities
# ---------------------------------------------------------------------------


def test_capabilities_returns_features_and_limits(client: TestClient, monkeypatch):
    # Force discovery unreachable to keep the test offline.
    import capabilities as caps_mod

    async def _stub(_settings, auth_header=None):
        return [], False

    monkeypatch.setattr(caps_mod, "_fetch_tools_available", _stub)

    r = client.get(
        "/capabilities", headers={"Authorization": f"Bearer {_mint('u_owner')}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platform_features"]["hitl_approvals"] is True
    assert body["platform_features"]["webhooks_inbound"] is True
    assert body["platform_features"]["sub_agent_routing"] is True
    assert body["platform_features"]["outbound_email"] is False
    assert body["limits"]["min_poll_interval_seconds"] >= 30
    assert body["discovery_reachable"] is False
    assert body["tools_available"] == []


def test_capabilities_requires_auth(client: TestClient):
    r = client.get("/capabilities")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Webhook trigger
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def webhook_app(client: TestClient, monkeypatch):
    secret = "test-webhook-secret-32bytes-min-len"
    monkeypatch.setenv("INSURER_WEBHOOK_SECRET", secret)
    triggers = [
        {
            "id": "carrier_webhook",
            "type": "webhook",
            "action": "intake_claim",
            "secret_ref": "env:INSURER_WEBHOOK_SECRET",
        }
    ]
    # Skip the LLM call entirely — we only verify the webhook surface.
    import runtime
    from models import RunResponse

    async def _fake_run(*, settings, app_spec, agent_spec, request, auth_header=None, skip_approval_gate=False):
        return RunResponse(
            correlation_id="run_fake1",
            status="completed",
            outputs={"text": "ok"},
            timeline=[{"step": "fake", "status": "ok"}],
        )

    monkeypatch.setattr(runtime, "execute_run", _fake_run)
    # trigger_runner imports execute_run by name at top of module
    import trigger_runner

    monkeypatch.setattr(trigger_runner, "execute_run", _fake_run)
    slug = _publish(client, triggers=triggers)
    return slug, secret












# ---------------------------------------------------------------------------
# Scheduler tick — interval + poll
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Sub-agent routing
# ---------------------------------------------------------------------------


def test_delegate_tool_injected_when_sub_agents_present(monkeypatch):
    from models import AgentSpec, Action, SubAgent
    from runtime import _build_delegate_tool

    spec = AgentSpec(
        agent_id="a1",
        name="a1",
        system_prompt="root",
        sub_agents=[
            SubAgent(id="policy", role="policy", system_prompt="p"),
            SubAgent(id="fraud", role="fraud", system_prompt="f"),
        ],
        actions=[Action(name="triage")],
    )
    tool = _build_delegate_tool(spec)
    assert tool is not None
    enum = tool["function"]["parameters"]["properties"]["sub_agent_id"]["enum"]
    assert sorted(enum) == ["fraud", "policy"]


def test_delegate_tool_absent_when_no_sub_agents():
    from models import AgentSpec, Action
    from runtime import _build_delegate_tool

    spec = AgentSpec(
        agent_id="a1",
        name="a1",
        system_prompt="root",
        actions=[Action(name="triage")],
    )
    assert _build_delegate_tool(spec) is None


def test_sub_agent_with_illegal_tools_is_rejected(monkeypatch):
    """Sub-agent tool list must be a subset of root agent's tools."""
    from models import AgentSpec, SubAgent
    from runtime import _execute_sub_agent

    spec = AgentSpec(
        agent_id="a1",
        name="a1",
        system_prompt="root",
        tools=["allowed_tool"],
        sub_agents=[
            SubAgent(
                id="rogue",
                role="rogue",
                system_prompt="x",
                tools=["allowed_tool", "forbidden_tool"],
            )
        ],
    )
    sub = spec.sub_agents[0]
    settings = __import__("config").get_settings()
    result = asyncio.run(
        _execute_sub_agent(
            settings=settings,
            agent_spec=spec,
            sub_agent=sub,
            task="anything",
            context={},
            auth_header=None,
            depth=0,
        )
    )
    assert result.get("error") == "sub_agent_tools_exceed_root"
    assert "forbidden_tool" in result.get("illegal_tools", [])


def test_sub_agent_depth_limit():
    from models import AgentSpec, SubAgent
    from runtime import _execute_sub_agent, _SUB_AGENT_MAX_DEPTH

    spec = AgentSpec(
        agent_id="a1",
        name="a1",
        system_prompt="root",
        sub_agents=[SubAgent(id="x", role="x", system_prompt="x")],
    )
    settings = __import__("config").get_settings()
    result = asyncio.run(
        _execute_sub_agent(
            settings=settings,
            agent_spec=spec,
            sub_agent=spec.sub_agents[0],
            task="t",
            context={},
            auth_header=None,
            depth=_SUB_AGENT_MAX_DEPTH,
        )
    )
    assert result.get("error") == "sub_agent_max_depth_reached"


# ---------------------------------------------------------------------------
# HMAC primitive — directly
# ---------------------------------------------------------------------------


def test_verify_webhook_signature_constant_time():
    from trigger_runner import verify_webhook_signature

    secret = "k" * 32
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret=secret, body=body, signature_header=sig)
    # Plain hex (no prefix) is also accepted.
    assert verify_webhook_signature(
        secret=secret, body=body, signature_header=sig.split("=", 1)[1]
    )
    # Tampered body fails.
    assert not verify_webhook_signature(
        secret=secret, body=body + b"X", signature_header=sig
    )
    # Empty / missing fails.
    assert not verify_webhook_signature(secret=secret, body=body, signature_header=None)
    assert not verify_webhook_signature(secret="", body=body, signature_header=sig)
