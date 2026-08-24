# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""GET /embed/{key}/spec and GET /apps/{slug}/embed/snippet.

These are the two surfaces a customer's integration actually touches:

  * `/embed/{key}/spec` is the FIRST call citra.js makes, from a page on the
    customer's origin. The key names which app and which ENVIRONMENT (its
    prefix decides, so a UAT page and a production page can address different
    environments at the same time); authorisation is still the officer's own
    JWT against the app's audience.

  * `/apps/{slug}/embed/snippet` is the Export action on the My Apps card. It
    must refuse loudly for an app with no embed page — handing back a snippet
    that renders an empty card in a customer's production screen is the failure
    worth designing out, because they blame the integration, not the app.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

JWT_SECRET = "embed-endpoint-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

TEST_KEY = "emb_test_1111111111111111"
LIVE_KEY = "emb_live_2222222222222222"


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

    async def count_documents(self, *_a, **_kw):
        return 0


def _jwt(user_id="u_owner", tenant_id="acme-bank") -> str:
    return jwt.encode(
        {
            "sub": user_id, "user_id": user_id, "email": f"{user_id}@ex.com",
            "tenant_id": tenant_id, "org_id": tenant_id,
            "iat": int(time.time()), "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET, algorithm="HS256",
    )


def _app_spec(*, embed=True, slug="loan-triage"):
    page = {
        "id": "card", "kind": "embed" if embed else "standard", "title": "Card",
        "panels": (
            [{"id": "d", "type": "detail", "data_source": "ds",
              "id_field": "application_id",
              # Publish requires a trigger on an embed page — a card that
              # cannot run the agent is a viewer.
              "actions": [{"label": "Review",
                           "agent_action": "review_application"}],
              "sections": [{"type": "fields"}]}]
            if embed else
            [{"id": "q", "type": "queue", "data_source": "ds", "columns": ["id"]}]
        ),
    }
    return {
        "spec_version": "v0", "slug": slug, "title": "Loan Triage",
        "agent_id": "agent-1", "audience": "org", "tenant_id": "acme-bank",
        "data_sources": [{"id": "ds", "type": "mcp", "ref": "s.t"}],
        "pages": [page],
    }


@contextmanager
def _client(monkeypatch, *, apps_docs):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv("APPS_BASE_URL", "https://apps.citra-ai.com")
    monkeypatch.setenv("MCP_SERVICE_API_KEY", "svc-key")
    monkeypatch.setenv("DISCOVERY_SERVICE_URL", "http://discovery.test")
    monkeypatch.setenv("WORKFLOW_SERVICE_URL", "http://citra.test")

    import importlib
    import config as _config
    importlib.reload(_config)
    import main as _main
    importlib.reload(_main)

    apps = _MemCol(apps_docs)
    agents = _MemCol([{
        "agent_id": "agent-1", "tenant_id": "acme-bank",
        "agent_spec": {
            "agent_id": "agent-1", "name": "Loan Triage Agent",
            "system_prompt": "Assess loan applications.", "tools_v2": [],
        },
    }])
    for name in ("_apps_col", "_agents_col"):
        monkeypatch.setattr(_main, name, apps if name == "_apps_col" else agents,
                            raising=False)
    for name in ("_build_sessions_col", "_prompt_packs_col", "_skills_col",
                 "_pending_runs_col"):
        monkeypatch.setattr(_main, name, _MemCol(), raising=False)

    # The endpoints route by environment; keep both stores pointing at the same
    # in-memory collection so a test can assert resolution without a real Mongo.
    monkeypatch.setattr(_main, "get_apps_col", lambda: apps, raising=False)
    monkeypatch.setattr(_main, "get_agents_col", lambda: agents, raising=False)

    # `_db` must be non-None: both resolve_app_environment and
    # _embed_key_environment treat a missing db as "no test plane" and collapse
    # to prod. Without this the environment assertions below would pass or fail
    # for the wrong reason.
    class _DB:
        def __getitem__(self, _name):
            return apps

    monkeypatch.setattr(_main, "_db", _DB(), raising=False)

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
        importlib.reload(_main)


def _doc(*, embed=True, key=LIVE_KEY, slug="loan-triage"):
    return {
        "app_id": "app_1", "slug": slug, "tenant_id": "acme-bank",
        "agent_id": "agent-1", "owner": "u_owner", "status": "published",
        "embed_key": key, "app_spec": _app_spec(embed=embed, slug=slug),
    }


# ── /embed/{key}/spec ───────────────────────────────────────────────────────

def test_spec_resolves_a_live_key(monkeypatch):
    with _client(monkeypatch, apps_docs=[_doc()]) as c:
        r = c.get(f"/embed/{LIVE_KEY}/spec",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slug"] == "loan-triage"
        assert body["environment"] == "prod"
        # The renderer needs to know WHICH page is the card.
        assert body["page_id"] == "card"


def test_spec_environment_comes_from_the_key_prefix(monkeypatch):
    with _client(monkeypatch, apps_docs=[_doc(key=TEST_KEY)]) as c:
        r = c.get(f"/embed/{TEST_KEY}/spec",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 200, r.text
        # Same document, same slug — only the key says which environment. This
        # is what lets a customer's UAT and production pages differ.
        assert r.json()["environment"] == "test"


@pytest.mark.parametrize("bad", ["loan-triage", "app_abc", "nonsense"])
def test_spec_rejects_anything_that_is_not_an_embed_key(monkeypatch, bad):
    """A slug must not work here — the environment is carried by the prefix, and
    without one there is nothing to resolve."""
    with _client(monkeypatch, apps_docs=[_doc()]) as c:
        r = c.get(f"/embed/{bad}/spec",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 404


def test_spec_unknown_key_is_404_not_403(monkeypatch):
    """The key lives in the host page's source, so it is public. Distinguishing
    'no such key' from 'not yours' would let anyone probe which keys exist."""
    with _client(monkeypatch, apps_docs=[_doc()]) as c:
        r = c.get(f"/embed/emb_live_9999999999999999/spec",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 404


def test_spec_requires_a_token(monkeypatch):
    with _client(monkeypatch, apps_docs=[_doc()]) as c:
        r = c.get(f"/embed/{LIVE_KEY}/spec")
        assert r.status_code in (401, 403), r.text


# ── /apps/{slug}/embed/snippet ──────────────────────────────────────────────

def test_snippet_is_copy_paste_ready(monkeypatch):
    with _client(monkeypatch, apps_docs=[_doc()]) as c:
        r = c.get("/apps/loan-triage/embed/snippet",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["embed_key"] == LIVE_KEY
        assert body["script_url"] == "https://apps.citra-ai.com/v1/citra.js"
        # The developer must not have to look anything up: the key and the
        # script URL are already in the text they paste.
        assert LIVE_KEY in body["snippet"]
        assert body["script_url"] in body["snippet"]
        assert "Citra.init" in body["snippet"]
        # Version is the RUNTIME's to fill — it serves the bundle, so only it
        # knows which one is live.
        assert body["version"] is None


def test_snippet_refuses_an_app_with_no_embed_page(monkeypatch):
    """The failure worth designing out: a snippet that renders an empty card in
    a customer's production screen, which they blame on the integration."""
    with _client(monkeypatch, apps_docs=[_doc(embed=False, key=None)]) as c:
        r = c.get("/apps/loan-triage/embed/snippet",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 409
        assert "no embed page" in r.json()["detail"]


def test_embed_key_header_survives_the_middleware(monkeypatch):
    """The environment fix must work THROUGH the HTTP stack, not just in a
    direct call.

    `_capture_embed_key` is a `@app.middleware("http")`, i.e. BaseHTTPMiddleware,
    which historically ran the endpoint in a separate task — contextvars set in
    such a middleware did NOT reach the handler. The unit tests in
    test_embed_env_isolation call `_bind_app_env` directly and would pass even
    if the header never arrived, so this asserts the wiring itself.

    `/apps/{slug}/embed/snippet` is the probe because it calls `_bind_app_env`
    and echoes the environment it resolved.
    """
    # The app is PROMOTED — findable by slug, so store resolution says "prod".
    # It carries a TEST key, so the header must flip the answer to "test". If
    # the two answers were the same this test would prove nothing, which is the
    # trap the first version of it fell into.
    with _client(monkeypatch, apps_docs=[_doc(key=TEST_KEY)]) as c:
        import main as _m

        real = _m.get_settings()

        class _TestPlaneOn:
            # conftest pins the test plane OFF for the unit suite; the embed
            # environment split only exists when it is available.
            def __init__(self, r):
                self._r = r

            def __getattr__(self, n):
                return getattr(self._r, n)

            @property
            def test_environment_available(self):
                return True

        monkeypatch.setattr(_m, "get_settings", lambda: _TestPlaneOn(real))

        auth = {"Authorization": f"Bearer {_jwt()}"}

        without = c.get("/apps/loan-triage/embed/snippet", headers=auth)
        assert without.status_code == 200, without.text
        assert without.json()["environment"] == "prod", "store resolution baseline"

        with_key = c.get(
            "/apps/loan-triage/embed/snippet",
            headers={**auth, "X-Citra-Embed-Key": TEST_KEY},
        )
        assert with_key.status_code == 200, with_key.text
        # THE ASSERTION THAT MATTERS: the header reached _bind_app_env through
        # the middleware and changed the environment.
        assert with_key.json()["environment"] == "test"

        # A key naming no app must be IGNORED — not honoured, not fatal.
        stranger = c.get(
            "/apps/loan-triage/embed/snippet",
            headers={**auth, "X-Citra-Embed-Key": "emb_test_0000000000000000"},
        )
        assert stranger.status_code == 200, stranger.text
        assert stranger.json()["environment"] == "prod"


def test_snippet_refuses_when_the_key_was_never_minted(monkeypatch):
    """Published before embed keys existed — tell them to republish rather than
    hand back a snippet with an empty key."""
    with _client(monkeypatch, apps_docs=[_doc(key=None)]) as c:
        r = c.get("/apps/loan-triage/embed/snippet",
                  headers={"Authorization": f"Bearer {_jwt()}"})
        assert r.status_code == 409
        assert "republish" in r.json()["detail"]


# ── the record contract ─────────────────────────────────────────────────────
#
# `recordId` is the ONE value the host supplies, and nothing used to tell the
# integrator which identifier it wants. A loan screen carries an application id,
# a customer id, an account number and a branch code; pass the wrong one and the
# card resolves nothing and renders empty, with no error naming the expectation.
# The contract was always in the spec (detail.id_field + the data source ref) —
# these assert it reaches the person doing the wiring.


def test_snippet_declares_which_id_to_pass():
    import main
    from models import AppSpec

    spec = AppSpec.model_validate(_app_spec(embed=True))
    contract = main._embed_record_contract(spec)
    assert contract == {"key_field": "application_id",
                        "dataset": "s.t"}, contract

    snippet = main._embed_snippet(
        embed_key=LIVE_KEY, script_url="https://x/v1/citra.js", contract=contract,
    )
    # On the line the developer edits — a contract documented elsewhere is one
    # they will not read.
    assert "application_id" in snippet
    assert "s.t" in snippet


def test_snippet_omits_the_contract_rather_than_guessing():
    """A WRONG contract is worse than an absent one: the developer would trust
    it. When id_field is missing there is nothing honest to say."""
    import main
    from models import AppSpec

    raw = _app_spec(embed=True)
    for pg in raw["pages"]:
        for p in pg.get("panels", []):
            p.pop("id_field", None)
    contract = main._embed_record_contract(AppSpec.model_validate(raw))
    assert contract is None
    snippet = main._embed_snippet(
        embed_key=LIVE_KEY, script_url="https://x/v1/citra.js", contract=None,
    )
    assert "recordId: yourApp.currentRecordId()," in snippet
    assert "must be the" not in snippet


def test_spec_refuses_a_key_whose_app_lost_its_embed_page(monkeypatch):
    """An embed key is PRESERVED across republishes, and `is_external_surface`
    covers headless too — so republishing the same slug as a headless Decision
    API keeps the key while removing the page it was minted for.

    Observed live: a build published over `loan-credit-decision` left
    embed_key set on a `headless=true, pages=[]` document. Serving that as a
    200 with `page_id=None` renders a BLANK card in the customer's production
    page, and they blame their integration rather than the app.

    /apps/{slug}/embed/snippet already refuses this; the surface that actually
    renders must too, or the guard only protects the developer who copies the
    snippet and not the one who already pasted it.
    """
    doc = _doc()
    doc["app_spec"]["headless"] = True
    doc["app_spec"].pop("pages", None)      # page gone, key remains
    with _client(monkeypatch, apps_docs=[doc]) as c:
        r = c.get(f"/embed/{LIVE_KEY}/spec",
                  headers={"Authorization": f"Bearer {_jwt()}"})
    assert r.status_code == 409, f"{r.status_code}: {r.text}"
    assert "no longer has an embed page" in r.text
