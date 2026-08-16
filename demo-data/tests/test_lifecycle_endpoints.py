# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Phase B — lifecycle transition endpoint logic tests.

These tests cover the helper functions and request-validation models for
the seven lifecycle transitions added in Phase B:
    share / transfer / claim-for-dept / escalate-to-org /
    archive / restore / inheritance-policy

End-to-end HTTP tests (with running FastAPI + Mongo) live separately. The
unit tests here exercise the policy logic in isolation by calling helpers
directly with stubbed request objects.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "citra-workflow"))
sys.path.insert(0, str(ROOT / "smart-app-service"))
sys.path.insert(0, str(ROOT / "Citra-Service"))


def _req(
    user_id="alice@acme.com", email=None, org_id="acme",
    dept_ids=("plant_ops",), roles=("user",),
    sa_admin=(), sa_member=(),
):
    return SimpleNamespace(state=SimpleNamespace(
        user_id=user_id, email=email or user_id, org_id=org_id,
        dept_ids=list(dept_ids), roles=list(roles),
        is_service_account=False,
        service_account_admin_of=list(sa_admin),
        service_account_member_of=list(sa_member),
    ))


def _wf(**kw):
    """Default SA-owned workflow fixture (Phase D model)."""
    return {
        "workflow_id": "wf-test",
        "is_active": True,
        "owner_type": "service_account",
        "owner_id": "svc:plant@acme.citra.ai",
        "org_id": "acme",
        "dept_ids": [],
        "lifecycle_stage": "team_managed",
        **kw,
    }


# ── _is_owner_or_admin ─────────────────────────────────────────────────

def test_sa_admin_is_owner_or_admin_for_sa_owned():
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf()
    claims = _jwt_claims(_req(sa_admin=("svc:plant@acme.citra.ai",)))
    assert _is_owner_or_admin(claims, wf) is True


def test_sa_member_is_NOT_owner_or_admin_for_sa_owned():
    """Members can read/run but cannot perform owner-level lifecycle ops."""
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf()
    claims = _jwt_claims(_req(sa_member=("svc:plant@acme.citra.ai",)))
    assert _is_owner_or_admin(claims, wf) is False


def test_unrelated_user_is_not_owner_or_admin():
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf()
    assert _is_owner_or_admin(_jwt_claims(_req(user_id="bob@acme.com")), wf) is False


def test_super_admin_is_owner_or_admin_even_in_other_org():
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf(org_id="other")
    claims = _jwt_claims(_req(roles=("super_admin",)))
    assert _is_owner_or_admin(claims, wf) is True


def test_org_admin_is_owner_or_admin_within_own_org():
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf(org_id="acme")
    claims = _jwt_claims(_req(user_id="boss@acme.com", roles=("org_admin",)))
    assert _is_owner_or_admin(claims, wf) is True


def test_org_admin_NOT_owner_or_admin_in_other_org():
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf(org_id="acme")
    claims = _jwt_claims(_req(
        user_id="boss@other.com", org_id="other", roles=("org_admin",),
    ))
    assert _is_owner_or_admin(claims, wf) is False


def test_dept_admin_is_owner_for_dept_owned_workflow():
    from citra_workflow.router import _is_owner_or_admin, _jwt_claims
    wf = _wf(owner_type="dept", owner_id="plant_ops")
    claims = _jwt_claims(_req(
        user_id="lead@acme.com",
        dept_ids=("plant_ops",),
        roles=("dept_admin",),
    ))
    assert _is_owner_or_admin(claims, wf) is True


# ── _next_stage_after_share ────────────────────────────────────────────

def test_share_promotes_draft_to_shared():
    from citra_workflow.router import _next_stage_after_share
    assert _next_stage_after_share("draft") == "shared"


def test_share_promotes_personal_to_shared():
    from citra_workflow.router import _next_stage_after_share
    assert _next_stage_after_share("personal") == "shared"


def test_share_does_not_demote_advanced_stages():
    from citra_workflow.router import _next_stage_after_share
    for stage in ("shared", "team_managed", "dept_managed", "org_managed", "archived"):
        assert _next_stage_after_share(stage) == stage


# ── _audit_entry ───────────────────────────────────────────────────────

def test_audit_entry_records_actor_and_action():
    from citra_workflow.router import _audit_entry, _jwt_claims
    claims = _jwt_claims(_req())
    e = _audit_entry(claims, "transfer", reason="reorg")
    assert e["action"] == "transfer"
    assert e["by"] == "alice@acme.com"
    assert e["reason"] == "reorg"
    assert "at" in e


# ── Request model validation ───────────────────────────────────────────

def test_share_request_accepts_add_remove_and_visibility():
    from citra_workflow.router import ShareRequest
    r = ShareRequest(
        add=[{"user_id": "bob@acme.com", "role": "editor"}],
        remove=["carol@acme.com"],
        set_visibility={"read": "collaborators"},
    )
    assert len(r.add) == 1
    assert r.remove == ["carol@acme.com"]
    assert r.set_visibility == {"read": "collaborators"}


def test_transfer_request_rejects_invalid_owner_type():
    from citra_workflow.router import TransferRequest
    with pytest.raises(Exception):
        TransferRequest(new_owner_type="nonsense", new_owner_id="x")


def test_transfer_request_accepts_each_valid_owner_type():
    from citra_workflow.router import TransferRequest
    # 'user' is no longer a valid target under SA-only ownership
    for t in ("service_account", "dept", "org"):
        r = TransferRequest(new_owner_type=t, new_owner_id="target")
        assert r.new_owner_type == t


def test_inheritance_policy_request_rejects_invalid_policy():
    from citra_workflow.router import InheritancePolicyRequest
    with pytest.raises(Exception):
        InheritancePolicyRequest(inheritance_policy="nope")


def test_inheritance_policy_request_accepts_each_valid_policy():
    from citra_workflow.router import InheritancePolicyRequest
    for p in (
        "archive", "transfer_to_sa", "transfer_to_dept",
        "transfer_to_org", "delete_after_grace",
    ):
        r = InheritancePolicyRequest(inheritance_policy=p)
        assert r.inheritance_policy == p


def test_claim_for_dept_request_requires_dept_id():
    from citra_workflow.router import ClaimForDeptRequest
    r = ClaimForDeptRequest(dept_id="plant_ops")
    assert r.dept_id == "plant_ops"


# ── Smart-app-service mirror ───────────────────────────────────────────

def test_smart_app_service_lifecycle_helpers_exist():
    """Confirm Phase B helpers are exported from main.py."""
    import importlib.util
    sas_main = ROOT / "smart-app-service" / "main.py"
    # main.py imports a lot; we don't fully import it (it boots FastAPI).
    # Instead we just confirm the function names appear in source.
    src = sas_main.read_text(encoding="utf-8")
    for name in (
        "patch_collaborators",
        "transfer_app",
        "claim_app_for_dept",
        "escalate_app_to_org",
        "lifecycle_archive_app",
        "lifecycle_restore_app",
        "set_app_inheritance_policy",
        "_is_lifecycle_admin",
        "_lifecycle_audit_entry",
    ):
        assert f"def {name}" in src or f"async def {name}" in src, \
            f"missing {name} in smart-app-service/main.py"


def test_smart_app_service_endpoints_registered():
    """Confirm the new POST endpoints are wired to FastAPI."""
    sas_main = ROOT / "smart-app-service" / "main.py"
    src = sas_main.read_text(encoding="utf-8")
    expected = [
        '"/apps/{slug}/collaborators"',
        '"/apps/{slug}/transfer"',
        '"/apps/{slug}/claim-for-dept"',
        '"/apps/{slug}/escalate-to-org"',
        '"/apps/{slug}/lifecycle/archive"',
        '"/apps/{slug}/lifecycle/restore"',
        '"/apps/{slug}/inheritance-policy"',
    ]
    for path in expected:
        assert path in src, f"endpoint {path} not registered"


# ── citra-workflow endpoint registration ───────────────────────────────

def test_citra_workflow_lifecycle_endpoints_registered():
    """All 7 lifecycle endpoints exist on the citra-workflow router."""
    from citra_workflow.router import router
    paths = {r.path for r in router.routes if hasattr(r, "path")}
    for p in (
        "/api/workflows/{workflow_id}/share",
        "/api/workflows/{workflow_id}/transfer",
        "/api/workflows/{workflow_id}/claim-for-dept",
        "/api/workflows/{workflow_id}/escalate-to-org",
        "/api/workflows/{workflow_id}/archive",
        "/api/workflows/{workflow_id}/restore",
        "/api/workflows/{workflow_id}/inheritance-policy",
    ):
        assert p in paths, f"missing route {p}"
