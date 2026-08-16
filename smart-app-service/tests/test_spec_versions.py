"""End-to-end tests for spec version history + rollback.

Exercises:
  * snapshot-on-publish: each republish snapshots the superseded spec
  * retention cap: at most ``max_spec_versions`` (default 3) snapshots per app
  * GET /apps/{slug}/versions: live version + snapshots, newest first
  * POST /apps/{slug}/versions/{v}/rollback: forward-only re-publish, content
    restored, version bumped, reversible
  * rollback DISABLES AI triggers (must not silently re-arm autonomous runs)
  * rollback to the live version / an unknown (trimmed) version fails loud
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
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._test_helpers import _MemCol  # type: ignore  # noqa: E402

JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

TENANT = "bsphcl"
OWNER_SA = f"work-sa-{TENANT}-versioner"


def _mint() -> str:
    payload = {
        "sub": "versioner",
        "user_id": "versioner",
        "email": "versioner@example.com",
        "tenant_id": TENANT,
        "org_id": TENANT,
        "dept_ids": [],
        "roles": [],
        "work_sa_id": OWNER_SA,
        "service_account_admin_of": [OWNER_SA],
        "service_account_member_of": [],
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "iss": "Citra-AI",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27999/test")
    monkeypatch.setenv("SMART_APP_SERVICE_CALLBACK_URL", "https://smart.example/")

    import importlib
    import config as _config
    importlib.reload(_config)
    import main as _main
    importlib.reload(_main)

    apps = _MemCol()
    agents = _MemCol()
    spec_versions = _MemCol()
    monkeypatch.setattr(_main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(_main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(_main, "_spec_versions_col", spec_versions, raising=False)
    monkeypatch.setattr(_main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_pending_runs_col", _MemCol(), raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(_main.app) as c:
        c._cols = {"apps": apps, "agents": agents, "spec_versions": spec_versions}  # type: ignore[attr-defined]
        yield c


SLUG = "version-test-app"


def _app_spec(*, description: str, triggers: list | None = None) -> dict:
    spec = {
        "spec_version": "v0",
        "slug": SLUG,
        "title": "Version Test App",
        "description": description,
        "tenant_id": TENANT,
        "audience": "owner",
        "owner_type": "service_account",
        "owner_id": OWNER_SA,
        "kind": "app",
        "agent_id": "agent_version_test",
        "panels": [
            {"id": "p1", "type": "form", "title": "Form", "schema_ref": "agent.input_schema"}
        ],
    }
    if triggers is not None:
        spec["triggers"] = triggers
    return spec


def _agent_spec() -> dict:
    return {
        "spec_version": "v0",
        "agent_id": "agent_version_test",
        "name": "version test agent",
        "description": "v",
        "model_tier": "tier_b",
        "system_prompt": "You are a test agent.",
        "input_schema": {"type": "object"},
        "tools": [],
        "actions": [{"name": "noop"}],
    }


def _publish(client: TestClient, *, description: str, triggers: list | None = None):
    body = {
        "session_id": f"bs_{SLUG}",
        "app_spec": _app_spec(description=description, triggers=triggers),
        "agent_spec": _agent_spec(),
    }
    return client.post("/publish", json=body, headers={"Authorization": f"Bearer {_mint()}"})


def _versions(client: TestClient):
    return client.get(f"/apps/{SLUG}/versions", headers={"Authorization": f"Bearer {_mint()}"})


def _rollback(client: TestClient, version: int):
    return client.post(
        f"/apps/{SLUG}/versions/{version}/rollback",
        json={"reason": "test"},
        headers={"Authorization": f"Bearer {_mint()}"},
    )


def test_history_caps_at_three_and_lists_newest_first(client: TestClient):
    # Publish 5 times → live v5; snapshots of v1..v4 created, trimmed to last 3.
    for i in range(1, 6):
        r = _publish(client, description=f"rev {i}")
        assert r.status_code == 200, r.text

    r = _versions(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"] == 5
    assert body["max_kept"] == 3

    snaps = [v for v in body["versions"] if not v["is_current"]]
    current = [v for v in body["versions"] if v["is_current"]]
    assert len(current) == 1 and current[0]["version"] == 5
    # Capped at 3 snapshots, and they are the most recent superseded ones (v2,v3,v4).
    assert [v["version"] for v in snaps] == [4, 3, 2]
    # Whole list is newest-first (current first).
    assert [v["version"] for v in body["versions"]] == [5, 4, 3, 2]


def test_rollback_restores_content_bumps_version_and_is_reversible(client: TestClient):
    assert _publish(client, description="alpha").status_code == 200   # v1
    assert _publish(client, description="beta").status_code == 200    # v2 (snapshots v1=alpha)
    assert _publish(client, description="gamma").status_code == 200   # v3 (snapshots v2=beta)

    # Roll back to v1 (alpha). Forward-only → new live v4 with alpha content.
    r = _rollback(client, 1)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["rolled_back_to"] == 1
    assert out["new_version"] == 4

    live = client._cols["apps"].docs[0]
    assert live["version"] == 4
    assert live["app_spec"]["description"] == "alpha"
    # Reversible: the pre-rollback live version (v3=gamma) was snapshotted.
    body = _versions(client).json()
    assert body["current_version"] == 4
    snap_versions = {v["version"]: v for v in body["versions"] if not v["is_current"]}
    assert 3 in snap_versions  # the gamma version we just left is recoverable
    # Lifecycle audit recorded the rollback.
    audit = live.get("lifecycle_audit") or []
    assert any(a.get("action") == "rollback" and a.get("to_version") == 1 for a in audit)


def test_rollback_disables_triggers(client: TestClient):
    trig = [{"id": "sched1", "type": "schedule.interval", "action": "noop", "every_seconds": 300}]
    assert _publish(client, description="with-trigger", triggers=trig).status_code == 200  # v1

    # Activate the trigger on the live v1 (publish always lands it OFF).
    pr = client.patch(
        f"/apps/{SLUG}/ai-triggers/sched1",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {_mint()}"},
    )
    assert pr.status_code == 200, pr.text

    # Republish → snapshots the live v1 (trigger now ENABLED) into history.
    assert _publish(client, description="rev2", triggers=trig).status_code == 200  # v2

    body = _versions(client).json()
    snap_v1 = next(v for v in body["versions"] if v["version"] == 1)
    assert snap_v1["active_trigger_count"] == 1  # captured while enabled

    # Roll back to v1 → trigger must come back DISABLED.
    rr = _rollback(client, 1)
    assert rr.status_code == 200, rr.text
    assert rr.json()["triggers_disabled"] is True

    after = _versions(client).json()
    cur = next(v for v in after["versions"] if v["is_current"])
    assert cur["trigger_count"] == 1
    assert cur["active_trigger_count"] == 0  # re-armed only by an explicit activation


def test_rollback_to_live_version_rejected(client: TestClient):
    assert _publish(client, description="alpha").status_code == 200  # v1
    r = _rollback(client, 1)  # v1 is live
    assert r.status_code == 400, r.text


def test_rollback_to_unknown_version_404(client: TestClient):
    assert _publish(client, description="alpha").status_code == 200  # v1
    r = _rollback(client, 99)
    assert r.status_code == 404, r.text
