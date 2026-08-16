"""End-to-end tests for the audience model breaking change.

Exercises the full audience flow:
  * /publish with default audience="owner"
  * /publish with audience="dept:<X>" as dept_admin (allowed)
  * /publish with audience="org" as plain user (rejected)
  * GET /apps?scope=mine|shared|all visibility
  * GET /apps/{slug}/publish-options gating
  * POST /apps/{slug}/audience widen + narrow (higher-of rule)
  * POST /apps/{slug}/transfer preserves audience
  * Removed endpoints (/share, /claim-for-dept, /escalate-to-org) return 404
"""
from __future__ import annotations

import json
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
OWNER_SA = f"work-sa-{TENANT}-cmd-asha"


# ---------------------------------------------------------------------------
# Token mints — explicit claim shapes so each persona is testable in isolation
# ---------------------------------------------------------------------------


def _mint(
    user_id: str,
    *,
    roles: list[str] | None = None,
    dept_ids: list[str] | None = None,
    sa_admin_of: list[str] | None = None,
    sa_member_of: list[str] | None = None,
    org_id: str = TENANT,
    tenant_id: str = TENANT,
) -> str:
    work_sa = (sa_admin_of or [f"work-sa-{tenant_id}-{user_id}"])[0]
    payload = {
        "sub": user_id,
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "tenant_id": tenant_id,
        "org_id": org_id,
        "dept_ids": dept_ids or [],
        "roles": roles or [],
        "work_sa_id": work_sa,
        "service_account_admin_of": sa_admin_of or [work_sa],
        "service_account_member_of": sa_member_of or [],
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "iss": "Citra-AI",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _cmd_asha() -> str:
    """BSPHCL org_admin with all 5 dept_ids — the BSPHCL publish persona."""
    return _mint(
        "cmd-asha",
        roles=["org_admin"],
        dept_ids=["central_pmu", "billing_revenue", "ami_metering",
                  "distribution_ops", "vigilance_field"],
        sa_admin_of=[OWNER_SA],
    )


def _je_vigilance() -> str:
    """A JE in vigilance_field. No admin roles."""
    return _mint(
        "je-patna",
        roles=[],
        dept_ids=["vigilance_field"],
        sa_admin_of=["work-sa-bsphcl-je-patna"],
    )


def _dept_admin_vigilance() -> str:
    """A dept_admin of vigilance_field."""
    return _mint(
        "dept-admin-vig",
        roles=["dept_admin"],
        dept_ids=["vigilance_field"],
        sa_admin_of=["work-sa-bsphcl-dept-admin-vig"],
    )


def _billing_officer() -> str:
    """An officer in billing_revenue. No admin roles."""
    return _mint(
        "billing-officer",
        roles=[],
        dept_ids=["billing_revenue"],
        sa_admin_of=["work-sa-bsphcl-billing"],
    )


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------


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
    monkeypatch.setattr(_main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(_main, "_agents_col", agents, raising=False)
    monkeypatch.setattr(_main, "_spec_versions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_pending_runs_col", _MemCol(), raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(_main.app) as c:
        c._cols = {"apps": apps, "agents": agents}  # type: ignore[attr-defined]
        yield c


# ---------------------------------------------------------------------------
# Minimal published shape (valid AppSpec with one panel + one agent)
# ---------------------------------------------------------------------------


def _minimal_app_spec(
    slug: str,
    *,
    audience: str = "owner",
    dept_ids: list[str] | None = None,
    owner_sa: str = OWNER_SA,
) -> dict:
    return {
        "spec_version": "v0",
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "description": "Audience e2e test app",
        "tenant_id": TENANT,
        "audience": audience,
        "dept_ids": dept_ids or [],
        "owner_type": "service_account",
        "owner_id": owner_sa,
        "kind": "app",
        "agent_id": f"agent_{slug.replace('-', '_')}",
        "panels": [
            {
                "id": "p1",
                "type": "form",
                "title": "Test form",
                "schema_ref": "agent.input_schema",
            }
        ],
    }


def _minimal_agent_spec(slug: str) -> dict:
    return {
        "spec_version": "v0",
        "agent_id": f"agent_{slug.replace('-', '_')}",
        "name": f"agent for {slug}",
        "description": "audience e2e",
        "model_tier": "tier_b",
        "system_prompt": "You are a test agent.",
        "input_schema": {"type": "object"},
        "tools": [],
        "actions": [{"name": "noop"}],
    }


def _publish(client: TestClient, token: str, spec: dict) -> dict:
    body = {
        "session_id": f"bs_{spec['slug']}",
        "app_spec": spec,
        "agent_spec": _minimal_agent_spec(spec["slug"]),
    }
    return client.post(
        "/publish",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


# ===========================================================================
# Tests
# ===========================================================================


def test_publish_default_audience_is_owner(client: TestClient):
    spec = _minimal_app_spec("audience-default")
    spec.pop("audience", None)  # let server default it
    spec.pop("owner_id", None)  # let server resolve from JWT
    r = _publish(client, _cmd_asha(), spec)
    assert r.status_code == 200, r.text

    doc = client._cols["apps"].docs[0]
    assert doc["app_spec"]["audience"] == "owner"
    assert doc["app_spec"]["owner_type"] == "service_account"


def test_org_admin_can_publish_at_dept(client: TestClient):
    """cmd-asha is org_admin → can publish at any dept audience."""
    spec = _minimal_app_spec("dept-app", audience="dept:vigilance_field")
    r = _publish(client, _cmd_asha(), spec)
    assert r.status_code == 200, r.text


def test_org_admin_can_publish_at_org(client: TestClient):
    spec = _minimal_app_spec("org-app", audience="org")
    r = _publish(client, _cmd_asha(), spec)
    assert r.status_code == 200, r.text


def test_plain_user_publish_org_rejected(client: TestClient):
    """A vigilance JE has no org_admin role → publishing at audience=org is 403."""
    spec = _minimal_app_spec(
        "je-org-attempt",
        audience="org",
        owner_sa="work-sa-bsphcl-je-patna",
    )
    r = _publish(client, _je_vigilance(), spec)
    assert r.status_code == 403, r.text
    body = r.json()["detail"]
    assert body["code"] == "audience_publish_not_allowed"
    assert body["audience"] == "org"


def test_plain_user_publish_dept_rejected(client: TestClient):
    """A vigilance JE is NOT dept_admin → audience=dept:vigilance_field is 403."""
    spec = _minimal_app_spec(
        "je-dept-attempt",
        audience="dept:vigilance_field",
        owner_sa="work-sa-bsphcl-je-patna",
    )
    r = _publish(client, _je_vigilance(), spec)
    assert r.status_code == 403


def test_dept_admin_can_publish_at_own_dept(client: TestClient):
    spec = _minimal_app_spec(
        "vig-dept-app",
        audience="dept:vigilance_field",
        owner_sa="work-sa-bsphcl-dept-admin-vig",
    )
    r = _publish(client, _dept_admin_vigilance(), spec)
    assert r.status_code == 200, r.text


def test_dept_admin_cannot_publish_at_other_dept(client: TestClient):
    """vigilance dept_admin trying dept:billing_revenue → 403."""
    spec = _minimal_app_spec(
        "vig-other-dept",
        audience="dept:billing_revenue",
        owner_sa="work-sa-bsphcl-dept-admin-vig",
    )
    r = _publish(client, _dept_admin_vigilance(), spec)
    assert r.status_code == 403


def test_visibility_je_sees_only_vigilance_and_org(client: TestClient):
    """End-to-end visibility check for the BSPHCL JE persona."""
    # Seed 4 apps with different audiences
    cmd = _cmd_asha()
    for slug, aud in [
        ("vig-app",       "dept:vigilance_field"),
        ("billing-app",   "dept:billing_revenue"),
        ("ops-app",       "dept:distribution_ops"),
        ("org-app",       "org"),
    ]:
        r = _publish(client, cmd, _minimal_app_spec(slug, audience=aud))
        assert r.status_code == 200, r.text

    # JE in vigilance_field sees: vig-app (dept) + org-app (org). NOT billing/ops.
    r = client.get("/apps?scope=all", headers={"Authorization": f"Bearer {_je_vigilance()}"})
    assert r.status_code == 200, r.text
    visible = sorted(a["slug"] for a in r.json()["apps"])
    assert visible == ["org-app", "vig-app"]


def test_visibility_billing_sees_billing_and_org_only(client: TestClient):
    cmd = _cmd_asha()
    for slug, aud in [
        ("vig-app2",     "dept:vigilance_field"),
        ("billing-app2", "dept:billing_revenue"),
        ("org-app2",     "org"),
    ]:
        _publish(client, cmd, _minimal_app_spec(slug, audience=aud))

    r = client.get("/apps?scope=all", headers={"Authorization": f"Bearer {_billing_officer()}"})
    assert r.status_code == 200, r.text
    visible = sorted(a["slug"] for a in r.json()["apps"])
    assert visible == ["billing-app2", "org-app2"]


def test_visibility_cmd_sees_everything(client: TestClient):
    """cmd-asha is org_admin → sees all 4 demo-shape apps in tenant."""
    cmd = _cmd_asha()
    for slug, aud in [
        ("vig",  "dept:vigilance_field"),
        ("bil",  "dept:billing_revenue"),
        ("dis",  "dept:distribution_ops"),
        ("org-cmd",   "org"),
    ]:
        r = _publish(client, cmd, _minimal_app_spec(slug, audience=aud))
        assert r.status_code == 200, f"publish failed for {slug}: {r.text}"

    r = client.get("/apps?scope=all", headers={"Authorization": f"Bearer {cmd}"})
    assert r.status_code == 200, r.text
    visible = sorted(a["slug"] for a in r.json()["apps"])
    assert visible == ["bil", "dis", "org-cmd", "vig"]


def test_publish_options_je_sees_owner_allowed_dept_disabled(client: TestClient):
    spec = _minimal_app_spec("po-je", owner_sa="work-sa-bsphcl-je-patna")
    spec["audience"] = "owner"
    r = _publish(client, _je_vigilance(), spec)
    assert r.status_code == 200, r.text

    # JE asks for publish options
    r = client.get(
        "/apps/po-je/publish-options",
        headers={"Authorization": f"Bearer {_je_vigilance()}"},
    )
    assert r.status_code == 200, r.text
    opts = {o["value"]: o for o in r.json()["options"]}
    # 'owner' is allowed (they're owner-SA admin)
    assert opts["owner"]["allowed"] is True
    # 'dept:vigilance_field' is disabled with reason
    dept_opt = opts.get("dept:vigilance_field")
    assert dept_opt is not None
    assert dept_opt["allowed"] is False
    assert "dept_admin" in (dept_opt["reason"] or "")
    # 'org' is disabled with reason
    assert opts["org"]["allowed"] is False
    assert "org_admin" in (opts["org"]["reason"] or "")


def test_audience_change_owner_to_dept_as_dept_admin(client: TestClient):
    """dept_admin can widen owner → dept:X if they own the source SA."""
    spec = _minimal_app_spec(
        "widen-test",
        audience="owner",
        owner_sa="work-sa-bsphcl-dept-admin-vig",
    )
    r = _publish(client, _dept_admin_vigilance(), spec)
    assert r.status_code == 200, r.text

    r = client.post(
        "/apps/widen-test/audience",
        json={"audience": "dept:vigilance_field", "reason": "test widen"},
        headers={"Authorization": f"Bearer {_dept_admin_vigilance()}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audience"] == "dept:vigilance_field"
    assert body["previous_audience"] == "owner"


def test_audience_narrow_from_org_requires_org_admin(client: TestClient):
    """Once published org-wide, a non-org_admin cannot narrow it back."""
    cmd = _cmd_asha()
    spec = _minimal_app_spec("narrow-test", audience="org")
    r = _publish(client, cmd, spec)
    assert r.status_code == 200

    # Now have JE attempt to narrow it back — should fail because the
    # CURRENT level is "org" and they aren't org_admin.
    r = client.post(
        "/apps/narrow-test/audience",
        json={"audience": "owner"},
        headers={"Authorization": f"Bearer {_je_vigilance()}"},
    )
    # JE doesn't have edit rights anyway → 403 from _can_edit_app
    assert r.status_code == 403


def test_audience_change_higher_of_rule_blocks_narrow_below_current_role(client: TestClient):
    """org → owner narrow needs org_admin (the higher of current=org / target=owner)."""
    cmd = _cmd_asha()
    spec = _minimal_app_spec("rule-test", audience="org")
    _publish(client, cmd, spec)

    # cmd-asha narrowing back to owner — should succeed (she's org_admin)
    r = client.post(
        "/apps/rule-test/audience",
        json={"audience": "owner"},
        headers={"Authorization": f"Bearer {cmd}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["audience"] == "owner"


def test_transfer_preserves_audience(client: TestClient):
    cmd = _cmd_asha()
    spec = _minimal_app_spec("xfer-test", audience="dept:vigilance_field")
    _publish(client, cmd, spec)

    r = client.post(
        "/apps/xfer-test/transfer",
        json={
            "new_owner_type": "service_account",
            "new_owner_id": "work-sa-bsphcl-je-patna",
            "reason": "delivered to JE Patna",
        },
        headers={"Authorization": f"Bearer {cmd}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_owner"]["owner_id"] == "work-sa-bsphcl-je-patna"
    # Audience is preserved
    assert body["audience"] == "dept:vigilance_field"


def test_removed_endpoints_return_404(client: TestClient):
    cmd = _cmd_asha()
    _publish(client, cmd, _minimal_app_spec("gone-test"))
    h = {"Authorization": f"Bearer {cmd}"}

    # /share endpoints — removed in the breaking change
    assert client.get("/apps/gone-test/share", headers=h).status_code == 404
    assert client.post(
        "/apps/gone-test/share",
        json={"principal_type": "user", "principal_id": "x"},
        headers=h,
    ).status_code == 404

    # /claim-for-dept and /escalate-to-org — removed
    assert client.post(
        "/apps/gone-test/claim-for-dept",
        json={"dept_id": "vigilance_field"},
        headers=h,
    ).status_code == 404
    assert client.post(
        "/apps/gone-test/escalate-to-org",
        json={},
        headers=h,
    ).status_code == 404


def test_admin_endpoint_returns_audience(client: TestClient):
    """GET /admin/apps surfaces audience per row."""
    cmd = _cmd_asha()
    _publish(client, cmd, _minimal_app_spec("adm-test", audience="dept:vigilance_field"))

    r = client.get("/admin/apps", headers={"Authorization": f"Bearer {cmd}"})
    assert r.status_code == 200, r.text
    row = next(a for a in r.json()["apps"] if a["slug"] == "adm-test")
    assert row["audience"] == "dept:vigilance_field"
    assert row["owner_type"] == "service_account"


def test_admin_endpoint_dept_admin_sees_dept_owned_apps(client: TestClient):
    """dept_admin admin lens picks up apps owned by their dept (owner_type=dept)."""
    cmd = _cmd_asha()
    # Publish org-owned + dept-owned + foreign-dept-owned
    _publish(client, cmd, _minimal_app_spec("dept-owned", audience="dept:vigilance_field"))
    # Transfer to vigilance_field dept ownership
    client.post(
        "/apps/dept-owned/transfer",
        json={"new_owner_type": "dept", "new_owner_id": "vigilance_field"},
        headers={"Authorization": f"Bearer {cmd}"},
    )

    # The vigilance dept_admin should see it via /admin/apps
    r = client.get(
        "/admin/apps",
        headers={"Authorization": f"Bearer {_dept_admin_vigilance()}"},
    )
    assert r.status_code == 200, r.text
    slugs = {a["slug"] for a in r.json()["apps"]}
    assert "dept-owned" in slugs


