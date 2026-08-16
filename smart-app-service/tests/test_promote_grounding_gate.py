"""Promote-to-prod grounding gate (Phase 1).

A grounded app must have fresh few-shot memory before it goes live in prod —
otherwise it runs degraded (cold-start note, no precedent). Covers:
  * _grounding_freshness_state: never / fresh / stale / unparseable / disabled
  * promote gate matrix:
      - grounded + never-refreshed + no flags        → 409 grounding_refresh_required
      - grounded + promote_ungrounded=true           → 200, grounding_status="skipped"
      - grounded + refresh_grounding=true            → 200, run_id set, "refreshing"
      - grounded + already fresh + no flags          → 200, grounding_status="fresh"
      - NOT grounded                                  → 200, grounding_status=None
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import jwt
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._test_helpers import _MemCol  # type: ignore  # noqa: E402

JWT_SECRET = "smart-app-service-test-secret"
TENANT = "acme-power"
OWNER_SA = f"work-sa-{TENANT}-cmd-asha"

GROUNDING = {
    "source_id": "field_operations",
    "dataset_id": "field_operations.theft_cases",
    "source_id_field": "case_id",
    "input_fields": ["asset_id", "amount"],
    "decision_field": "recovery_status",
    "terminal_states": ["recovered", "written_off"],
}


def _mint_admin() -> str:
    """org_admin of the owning org + admin of the owner SA — may promote."""
    payload = {
        "sub": "cmd-asha", "user_id": "cmd-asha",
        "email": "cmd-asha@example.com",
        "tenant_id": TENANT, "org_id": TENANT,
        "roles": ["org_admin"],
        "service_account_admin_of": [OWNER_SA],
        "iat": int(time.time()), "exp": int(time.time()) + 600, "iss": "Citra-AI",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


class _MemDB:
    """Mongo-database stub: __getitem__(name) → a per-name _MemCol."""

    def __init__(self):
        self._cols = {}

    def __getitem__(self, name):
        return self._cols.setdefault(name, _MemCol())


class _FakeStore:
    """Grounding run store stub — only get_last matters for the gate."""

    def __init__(self, last_by_slug=None):
        self._last = dict(last_by_slug or {})

    def get_last(self, slug):
        return self._last.get(slug)

    def get(self, slug):
        return None

    def put(self, *a, **k):
        pass

    def put_last(self, *a, **k):
        pass


def _app_doc(slug: str, agent_id: str, *, grounded: bool) -> dict:
    return {
        "slug": slug, "app_id": f"app_{slug.replace('-', '_')}",
        "tenant_id": TENANT, "agent_id": agent_id,
        "grounded": grounded, "version": 1,
        "app_spec": {
            "spec_version": "v0", "slug": slug,
            "title": slug.replace("-", " ").title(),
            "tenant_id": TENANT, "org_id": TENANT,
            "audience": "owner",
            "owner_type": "service_account", "owner_id": OWNER_SA,
            "kind": "app", "agent_id": agent_id,
            "triggers": [],
        },
    }


def _agent_doc(agent_id: str, *, grounded: bool) -> dict:
    spec = {
        "spec_version": "v0", "agent_id": agent_id, "name": agent_id,
        "system_prompt": "test", "input_schema": {"type": "object"},
    }
    if grounded:
        spec["grounding"] = dict(GROUNDING)
    return {"agent_id": agent_id, "tenant_id": TENANT, "agent_spec": spec}


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    """Reloaded main + seeded test/prod collections + controllable freshness."""
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("JWT_ISSUER", "Citra-AI")
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv("SMART_APP_SERVICE_CALLBACK_URL", "https://smart.example/")
    monkeypatch.setenv("TEST_DISCOVERY_SERVICE_URL", "https://test-discovery.example/")

    import config as _config
    importlib.reload(_config)
    import main as _main
    importlib.reload(_main)
    import grounding_runs as _gr

    db = _MemDB()
    apps = _MemCol()   # prod apps
    agents = _MemCol()  # prod agents
    monkeypatch.setattr(_main, "_db", db, raising=False)
    monkeypatch.setattr(_main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(_main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(_main, "_spec_versions_col", _MemCol(), raising=False)

    # Seed the TEST collections (what promote reads as the source).
    test_apps = db["test_smartapp_apps"]
    test_agents = db["test_smartapp_agents"]
    test_apps.docs.append(_app_doc("grounded-app", "agent_grounded", grounded=True))
    test_agents.docs.append(_agent_doc("agent_grounded", grounded=True))
    test_apps.docs.append(_app_doc("plain-app", "agent_plain", grounded=False))
    test_agents.docs.append(_agent_doc("agent_plain", grounded=False))

    # Record enqueue calls; never touch Milvus/Redis in a unit test.
    calls = {"enqueue": []}

    def _fake_enqueue(*, settings, slug, agent_id, tenant_id, contract, user_jwt, requested_by=None):
        calls["enqueue"].append({"slug": slug, "agent_id": agent_id})
        return "gr_testrun123"

    monkeypatch.setattr(_main, "_enqueue_grounding_refresh", _fake_enqueue)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    def set_freshness(last_by_slug):
        monkeypatch.setattr(_gr, "get_grounding_run_store",
                            lambda: _FakeStore(last_by_slug))

    set_freshness({})  # default: never refreshed

    with TestClient(_main.app) as c:
        yield {"client": c, "main": _main, "gr": _gr,
               "apps": apps, "agents": agents, "calls": calls,
               "set_freshness": set_freshness}


def _promote(client, slug, **body):
    return client.post(
        f"/apps/{slug}/promote-to-prod",
        json=body,
        headers={"Authorization": f"Bearer {_mint_admin()}"},
    )


# ── _grounding_freshness_state ────────────────────────────────────────────
def test_freshness_never(env, monkeypatch):
    env["set_freshness"]({})
    assert env["main"]._grounding_freshness_state("grounded-app") == "never"


def test_freshness_fresh(env, monkeypatch):
    monkeypatch.setenv("GROUNDING_FULL_REFRESH_DAYS", "7")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    env["set_freshness"]({"grounded-app": {"last_refreshed_at": recent, "sample_count": 12}})
    assert env["main"]._grounding_freshness_state("grounded-app") == "fresh"


def test_freshness_stale(env, monkeypatch):
    monkeypatch.setenv("GROUNDING_FULL_REFRESH_DAYS", "7")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    env["set_freshness"]({"grounded-app": {"last_refreshed_at": old, "sample_count": 12}})
    assert env["main"]._grounding_freshness_state("grounded-app") == "stale"


def test_freshness_unparseable_is_stale(env):
    env["set_freshness"]({"grounded-app": {"last_refreshed_at": "not-a-date"}})
    assert env["main"]._grounding_freshness_state("grounded-app") == "stale"


def test_freshness_disabled_threshold_is_fresh(env, monkeypatch):
    monkeypatch.setenv("GROUNDING_FULL_REFRESH_DAYS", "0")
    old = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
    env["set_freshness"]({"grounded-app": {"last_refreshed_at": old}})
    assert env["main"]._grounding_freshness_state("grounded-app") == "fresh"


# ── promote gate matrix ───────────────────────────────────────────────────
def test_grounded_never_refreshed_blocks(env):
    env["set_freshness"]({})  # never
    r = _promote(env["client"], "grounded-app")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "grounding_refresh_required"
    assert detail["freshness"] == "never"
    # Nothing was promoted or enqueued.
    assert env["calls"]["enqueue"] == []


def test_grounded_stale_blocks(env, monkeypatch):
    monkeypatch.setenv("GROUNDING_FULL_REFRESH_DAYS", "7")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    env["set_freshness"]({"grounded-app": {"last_refreshed_at": old}})
    r = _promote(env["client"], "grounded-app")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["freshness"] == "stale"


def test_grounded_override_ships_without_grounding(env):
    env["set_freshness"]({})
    r = _promote(env["client"], "grounded-app", promote_ungrounded=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounding_status"] == "skipped"
    assert body["grounding_refresh_run_id"] is None
    assert env["calls"]["enqueue"] == []


def test_grounded_refresh_enqueues(env):
    env["set_freshness"]({})
    r = _promote(env["client"], "grounded-app", refresh_grounding=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounding_status"] == "refreshing"
    assert body["grounding_refresh_run_id"] == "gr_testrun123"
    # Enqueued against the PROD slug + PRESERVED agent_id.
    assert env["calls"]["enqueue"] == [{"slug": "grounded-app", "agent_id": "agent_grounded"}]


def test_grounded_fresh_passes_without_flags(env, monkeypatch):
    monkeypatch.setenv("GROUNDING_FULL_REFRESH_DAYS", "7")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    env["set_freshness"]({"grounded-app": {"last_refreshed_at": recent, "sample_count": 9}})
    r = _promote(env["client"], "grounded-app")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounding_status"] == "fresh"
    assert body["grounding_refresh_run_id"] is None
    assert env["calls"]["enqueue"] == []


def test_non_grounded_app_unaffected(env):
    env["set_freshness"]({})  # irrelevant — app has no grounding
    r = _promote(env["client"], "plain-app")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounding_status"] is None
    assert body["grounding_refresh_run_id"] is None
    assert env["calls"]["enqueue"] == []


def test_promote_carries_value_semantics_and_organization(env):
    """The prod poller + value-stats read the PROD app doc — promote must carry
    the publish-resolved money definitions + display identity, or value
    stamping silently dies on promoted apps (live-caught on the acme recovery
    tracker: publish computed them onto the test doc, promote dropped them)."""
    test_apps = env["main"]._db["test_smartapp_apps"]
    doc = next(d for d in test_apps.docs if d["slug"] == "plain-app")
    doc["value_semantics"] = {"field_operations.theft_cases": {
        "value_kind": "recovered", "definition_version": "feed00000001"}}
    doc["organization"] = {"name": "Acme Power & Utilities Co.",
                           "short_name": "Acme Power"}
    r = _promote(env["client"], "plain-app")
    assert r.status_code == 200, r.text
    prod = next(d for d in env["apps"].docs if d["slug"] == "plain-app")
    assert prod["value_semantics"]["field_operations.theft_cases"][
        "definition_version"] == "feed00000001"
    assert prod["organization"]["short_name"] == "Acme Power"
