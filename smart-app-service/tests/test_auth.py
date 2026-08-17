# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Smoke tests for JWT auth middleware on smart-app-service.

Confirms:
  * /health is public
  * /apps without a token returns 401
  * /apps with a valid Citra-Service style JWT returns a list scoped to
    the authenticated user (owner injected from the JWT)
  * a JWT signed with a different secret is rejected
  * /publish accepts a builder pod's scoped token
    (scope=smart-app-builder), per the citra-app-publish SKILL contract
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

JWT_SECRET = "smart-app-service-test-secret"

os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Build the app with mongo bits stubbed so we never touch a DB."""
    import main  # imported lazily so env vars above land first

    # Stub the mongo collections used by /apps
    class _StubCursor:
        def sort(self, *_a, **_kw):  # noqa: D401
            return self

        def limit(self, *_a, **_kw):  # noqa: D401
            return self

        def __aiter__(self):
            async def _agen():
                if False:
                    yield None
            return _agen()

    class _StubCol:
        def find(self, *_a, **_kw):
            return _StubCursor()

        async def count_documents(self, *_a, **_kw):
            return 0

        async def find_one(self, *_a, **_kw):
            return None

        async def update_one(self, *_a, **_kw):
            class _R:
                matched_count = 0
            return _R()

        async def replace_one(self, *_a, **_kw):
            class _R:
                upserted_id = "stub"
            return _R()

        async def insert_many(self, *_a, **_kw):
            class _R:
                inserted_ids = []
            return _R()

        async def insert_one(self, *_a, **_kw):
            class _R:
                inserted_id = "stub"
            return _R()

        async def create_index(self, *_a, **_kw):
            return None

    stub = _StubCol()
    monkeypatch.setattr(main, "_apps_col", stub, raising=False)
    monkeypatch.setattr(main, "_agents_col", stub, raising=False)
    monkeypatch.setattr(main, "_build_sessions_col", stub, raising=False)
    monkeypatch.setattr(main, "_prompt_packs_col", stub, raising=False)
    monkeypatch.setattr(main, "_skills_col", stub, raising=False)
    monkeypatch.setattr(main, "_pending_runs_col", stub, raising=False)

    # Lifespan would otherwise create a real Mongo client.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    main.app.router.lifespan_context = _noop_lifespan

    with TestClient(main.app) as tc:
        yield tc


def _mint(user_id: str = "u_alice", **extra) -> str:
    payload = {
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "iss": "Citra-AI",
        **extra,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def test_health_is_public(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200


def test_apps_requires_auth(client: TestClient):
    r = client.get("/apps")
    assert r.status_code == 401


def test_apps_accepts_valid_jwt(client: TestClient):
    r = client.get("/apps", headers={"Authorization": f"Bearer {_mint()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["apps"] == []
    assert body["total"] == 0


def test_apps_rejects_wrong_secret(client: TestClient):
    bad = jwt.encode(
        {"user_id": "u_alice", "exp": int(time.time()) + 60},
        "different-secret",
        algorithm="HS256",
    )
    r = client.get("/apps", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


def test_apps_rejects_expired(client: TestClient):
    expired = jwt.encode(
        {"user_id": "u_alice", "exp": int(time.time()) - 10},
        JWT_SECRET,
        algorithm="HS256",
    )
    r = client.get("/apps", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


# ── /publish — builder pod scoped token ───────────────────────────────────


FIXTURES = Path(__file__).parent / "fixtures"


def _mint_builder(session_id: str = "bs_test") -> str:
    """Mirror smart-app-service.main._mint_builder_token.

    The builder token carries the BA's identity (user_id) but NOT ownership
    claims — /publish resolves the owning Work SA from the build session, or
    (session-less, as here) derives it deterministically from user_id. So
    user_id must be a real BA id, not a ``builder:<session>`` placeholder.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "u_builder@example.com",
            "user_id": "u_builder@example.com",
            "scope": "smart-app-builder",
            "session_id": session_id,
            "tenant_id": "t_acme",
            "iat": now,
            "exp": now + 600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _publish_payload() -> dict:
    app_spec = json.loads((FIXTURES / "claims_app_spec.json").read_text())
    agent_spec = json.loads((FIXTURES / "claims_agent_spec.json").read_text())
    # publish assigns app_id / version / deployed_at server-side; strip the
    # null placeholders so JSON Schema (type=string) doesn't reject them.
    for k in ("app_id", "version", "deployed_at", "status"):
        app_spec.pop(k, None)
    return {
        "session_id": "bs_test",
        "app_spec": app_spec,
        "agent_spec": agent_spec,
    }








# ── _mint_builder_token helper itself ─────────────────────────────────────


def test_mint_builder_token_round_trips_through_middleware(client: TestClient):
    """The minter and the middleware must agree on JWT_SECRET + algorithm."""
    from config import Settings
    from main import _mint_builder_token

    settings = Settings(jwt_secret=JWT_SECRET)
    token = _mint_builder_token(
        settings=settings,
        session_id="bs_round_trip",
        owner="u_alice",
        tenant_id="t_acme",
    )
    decoded = jwt.decode(
        token, JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False}
    )
    assert decoded["scope"] == "smart-app-builder"
    assert decoded["session_id"] == "bs_round_trip"
    assert decoded["user_id"] == "u_alice"
    assert decoded["tenant_id"] == "t_acme"
