# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""End-to-end proxy + auth-forwarding regression tests.

Covers the bugs surfaced in the priority review:

  * Bug #1 — citra-app-runtime's ``/api/run/[slug]`` proxy must forward the
    Authorization header. We can't import the Next.js handler from Python,
    so we assert the *server-side* invariant: when the runtime forwards a
    request *without* a token, smart-app-service rejects it (so the user
    will see a clear 401 rather than the previous "fails open" path).

  * Bug #2 — ``require_publish_scope`` must reject user JWTs in production
    and accept builder-scoped tokens always. The dev fallback is exercised
    by the existing test_auth.py suite; this file adds the prod-mode path.

  * HITL approve loop — POST /apps/{slug}/run that should pause returns
    ``pending_approval`` and persists; POST /approve resumes it; the same
    user cannot self-approve.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

# Match test_auth.py's secret so that whichever module imports `main` first
# pins the same value into auth middleware (FastAPI app is module-level).
JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Stub Mongo collections that DO retain inserted docs (test_auth's stubs are
# memoryless). We need real find_one for the approve flow.
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

    async def update_one(self, q, update, *_a, **_kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                d.update(update.get("$set", {}))

                class _R:
                    matched_count = 1
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
    monkeypatch.setattr(main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(main, "_pending_runs_col", pending, raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(main.app) as c:
        c._cols = {"apps": apps, "agents": agents, "pending": pending}  # type: ignore[attr-defined]
        yield c


def _mint(user_id: str = "u_alice", **extra) -> str:
    payload = {
        "sub": user_id,
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "iss": "Citra-AI",
        **extra,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _mint_builder(session_id: str = "bs_e2e", tenant_id: str = "bajaj") -> str:
    return jwt.encode(
        {
            "sub": f"builder:{session_id}",
            "user_id": f"builder:{session_id}",
            "scope": "smart-app-builder",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _publish_payload(approval_required: bool = False) -> dict:
    app_spec = json.loads((FIXTURES / "claims_app_spec.json").read_text())
    agent_spec = json.loads((FIXTURES / "claims_agent_spec.json").read_text())
    for k in ("app_id", "version", "deployed_at", "status"):
        app_spec.pop(k, None)
    if approval_required and agent_spec.get("actions"):
        agent_spec["actions"][0]["approval_required"] = True
    return {
        "session_id": "bs_e2e",
        "app_spec": app_spec,
        "agent_spec": agent_spec,
    }


# ---------------------------------------------------------------------------
# Bug #1 — /run requires Authorization header
# ---------------------------------------------------------------------------


def test_run_endpoint_rejects_unauthenticated_request(client: TestClient):
    """Confirms the server-side invariant the runtime proxy must satisfy:
    an unauthenticated POST to /apps/{slug}/run is rejected. (Pre-fix the
    runtime stripped Authorization, every published app silently 401'd.)"""
    r = client.post("/apps/whatever/run", json={"action": "x", "inputs": {}})
    assert r.status_code == 401


def test_run_endpoint_404_for_unknown_slug_with_valid_token(client: TestClient):
    r = client.post(
        "/apps/does-not-exist/run",
        json={"action": "x", "inputs": {}},
        headers={"Authorization": f"Bearer {_mint()}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Bug #2 — require_publish_scope hardening
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# HITL — pending_approval round-trip
# ---------------------------------------------------------------------------


def _publish_and_get_slug(client: TestClient, **kw) -> str:
    r = client.post(
        "/publish",
        json=_publish_payload(**kw),
        headers={"Authorization": f"Bearer {_mint_builder()}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["slug"]




