# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
SA-only ownership tests (Phase D rewrite).

After Phase D, every workflow / smart-app MUST belong to a service
account (or to a dept / org for organisation-level resources).
owner_type='user' is rejected at both the Pydantic model and the
create-endpoint validator.

What's covered:
  1. Pydantic model rejects owner_type='user'
  2. Default owner_type = 'service_account'
  3. AppSpec mirrors the same restriction
  4. inheritance_policy literal includes transfer_to_dept
  5. _visibility_filter clauses for super_admin / org_admin / SA member /
     dept_admin / unaffiliated user
  6. _check_workflow_action for every persona (SA admin, SA member,
     dept member, dept_admin, org member, org_admin, super_admin,
     unrelated user)
  7. _validate_create_ownership accept/reject paths
  8. transfer_workflow / admin_reassign restricted to SA|dept|org targets
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


# ── Model parsing ──────────────────────────────────────────────────────

def test_workflow_definition_defaults_to_service_account_owner():
    """WorkflowDefinition default owner_type must be 'service_account'."""
    from citra_workflow.models import WorkflowDefinition
    wf = WorkflowDefinition(
        name="default-sa",
        owner_id="svc:plant-ops@acme.citra.ai",
        org_id="acme",
    )
    assert wf.owner_type == "service_account"
    assert wf.lifecycle_stage == "team_managed"


def test_workflow_definition_rejects_user_owner_type():
    """The Literal enum must not accept 'user' anymore."""
    from citra_workflow.models import WorkflowDefinition
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        WorkflowDefinition(
            name="should-fail",
            owner_type="user",
            owner_id="alice@acme.com",
            org_id="acme",
        )


def test_workflow_definition_accepts_dept_and_org_owners():
    from citra_workflow.models import WorkflowDefinition
    wf_dept = WorkflowDefinition(
        name="dept-owned",
        owner_type="dept",
        owner_id="plant_ops",
        org_id="acme",
        dept_ids=["plant_ops"],
    )
    assert wf_dept.owner_type == "dept"

    wf_org = WorkflowDefinition(
        name="org-owned",
        owner_type="org",
        owner_id="acme",
        org_id="acme",
    )
    assert wf_org.owner_type == "org"


def test_workflow_visibility_targets_are_sa_dept_org_public():
    """Visibility levels match the SA-only model."""
    from citra_workflow.models import WorkflowVisibility
    from pydantic import ValidationError
    # All allowed values work
    for read in ("sa", "dept", "org", "public"):
        v = WorkflowVisibility(read=read)
        assert v.read == read
    # 'owner' / 'collaborators' are no longer valid
    with pytest.raises(ValidationError):
        WorkflowVisibility(read="owner")
    with pytest.raises(ValidationError):
        WorkflowVisibility(read="collaborators")


def test_inheritance_policy_includes_transfer_to_dept():
    from citra_workflow.models import WorkflowDefinition
    wf = WorkflowDefinition(
        name="dept-handoff",
        owner_id="svc:x@acme.citra.ai",
        org_id="acme",
        dept_ids=["plant_ops"],
        inheritance_policy="transfer_to_dept",
        inheritance_target="plant_ops",
    )
    assert wf.inheritance_policy == "transfer_to_dept"
    assert wf.inheritance_target == "plant_ops"


def test_app_spec_defaults_to_service_account_owner():
    """smart-app-service AppSpec mirror."""
    import importlib.util
    sas_models_path = ROOT / "smart-app-service" / "models.py"
    spec = importlib.util.spec_from_file_location("sas_models_phase_d", sas_models_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sas_models_phase_d"] = mod
    spec.loader.exec_module(mod)

    panel = {
        "type": "agent_chat",
        "id": "main_chat",
        "title": "Chat",
        "starter_prompts": ["Hi"],
    }
    app = mod.AppSpec(
        slug="plant-ops-briefing",
        title="Plant Ops Briefing",
        owner_id="svc:plant-ops@acme.citra.ai",
        org_id="acme",
        dept_ids=["plant_ops"],
        kind="app",
        agent_id="agent_xyz",
        panels=[panel],
    )
    assert app.owner_type == "service_account"
    assert app.visibility.read == "sa"


def test_app_spec_rejects_user_owner_type():
    import importlib.util
    from pydantic import ValidationError
    sas_models_path = ROOT / "smart-app-service" / "models.py"
    spec = importlib.util.spec_from_file_location("sas_models_phase_d_reject", sas_models_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sas_models_phase_d_reject"] = mod
    spec.loader.exec_module(mod)

    panel = {"type": "agent_chat", "id": "x", "title": "X", "starter_prompts": ["Hi"]}
    with pytest.raises(ValidationError):
        mod.AppSpec(
            slug="reject-user-owner",
            title="X",
            owner_type="user",
            owner_id="alice@acme.com",
            org_id="acme",
            kind="app",
            agent_id="a",
            panels=[panel],
        )


def test_app_spec_inheritance_policy_includes_transfer_to_dept():
    import importlib.util
    sas_models_path = ROOT / "smart-app-service" / "models.py"
    spec = importlib.util.spec_from_file_location("sas_models_inh", sas_models_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sas_models_inh"] = mod
    spec.loader.exec_module(mod)

    panel = {"type": "agent_chat", "id": "x", "title": "X", "starter_prompts": ["Hi"]}
    app = mod.AppSpec(
        slug="dept-handoff",
        title="X",
        owner_id="svc:x@acme.citra.ai",
        org_id="acme",
        dept_ids=["plant_ops"],
        kind="app",
        agent_id="a",
        panels=[panel],
        inheritance_policy="transfer_to_dept",
        inheritance_target="plant_ops",
    )
    assert app.inheritance_policy == "transfer_to_dept"


# ── Request fixture ────────────────────────────────────────────────────

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


# ── Visibility filter ──────────────────────────────────────────────────

def test_visibility_filter_super_admin_returns_all_active():
    from citra_workflow.router import _visibility_filter
    f = _visibility_filter(_req(roles=("super_admin",)))
    assert f == {"is_active": True}


def test_visibility_filter_org_admin_restricts_to_org():
    from citra_workflow.router import _visibility_filter
    f = _visibility_filter(_req(roles=("org_admin",)))
    assert f == {"is_active": True, "org_id": "acme"}


def test_visibility_filter_sa_membership_includes_sa_clause():
    from citra_workflow.router import _visibility_filter
    f = _visibility_filter(_req(
        sa_admin=("svc:plant-ops@acme.citra.ai",),
        sa_member=("svc:claims@acme.citra.ai",),
    ))
    assert f["is_active"] is True
    clauses = f["$or"]
    sa_clause = next(
        (c for c in clauses if c.get("owner_type") == "service_account"),
        None,
    )
    assert sa_clause is not None
    assert set(sa_clause["owner_id"]["$in"]) == {
        "svc:plant-ops@acme.citra.ai",
        "svc:claims@acme.citra.ai",
    }


def test_visibility_filter_no_user_owner_clauses():
    """SA-only model: filter must NOT emit owner_type='user' clauses."""
    from citra_workflow.router import _visibility_filter
    f = _visibility_filter(_req(sa_admin=("svc:x@acme.citra.ai",)))
    for c in f.get("$or", []):
        assert c.get("owner_type") != "user", \
            f"user-owner clause leaked into filter: {c}"


def test_visibility_filter_unaffiliated_user_gets_no_access():
    """User with no SA / dept / org returns a guard clause that matches nothing."""
    from citra_workflow.router import _visibility_filter
    f = _visibility_filter(_req(
        org_id="", dept_ids=(), sa_admin=(), sa_member=(),
    ))
    assert f.get("_no_access_") is True


def test_visibility_filter_dept_admin_expands_to_dept_workflows():
    from citra_workflow.router import _visibility_filter
    f = _visibility_filter(_req(roles=("dept_admin",), dept_ids=("plant_ops",)))
    clauses = f["$or"]
    dept_wide = [c for c in clauses
                 if c.get("dept_ids", {}).get("$in") == ["plant_ops"]
                 and "visibility.read" not in c]
    assert len(dept_wide) >= 1


# ── _check_workflow_action ─────────────────────────────────────────────

def _wf(**kw):
    return {
        "workflow_id": "wf-test",
        "is_active": True,
        "owner_type": "service_account",
        "owner_id": "svc:plant@acme.citra.ai",
        "org_id": "acme",
        "dept_ids": [],
        "visibility": {
            "read": "sa", "run": "sa", "edit": "sa",
            "org_admin_override": True,
        },
        **kw,
    }


def test_sa_admin_can_read_run_edit():
    from citra_workflow.router import _check_workflow_action
    wf = _wf()
    req = _req(sa_admin=("svc:plant@acme.citra.ai",))
    for action in ("read", "run", "edit"):
        assert _check_workflow_action(wf, req, action) is None, action


def test_sa_member_can_read_and_run_but_not_edit():
    from citra_workflow.router import _check_workflow_action
    wf = _wf()
    req = _req(user_id="bob@acme.com", sa_member=("svc:plant@acme.citra.ai",))
    assert _check_workflow_action(wf, req, "read") is None
    assert _check_workflow_action(wf, req, "run") is None
    edit = _check_workflow_action(wf, req, "edit")
    assert edit is not None and edit[0] == 403


def test_non_member_blocked_from_sa_workflow():
    from citra_workflow.router import _check_workflow_action
    wf = _wf()
    req = _req(user_id="stranger@other.com", org_id="other-org")
    for action in ("read", "run", "edit"):
        res = _check_workflow_action(wf, req, action)
        assert res is not None and res[0] == 403


def test_super_admin_bypasses_everything():
    from citra_workflow.router import _check_workflow_action
    wf = _wf()
    req = _req(user_id="root@citra.ai", org_id="any", roles=("super_admin",))
    for action in ("read", "run", "edit"):
        assert _check_workflow_action(wf, req, action) is None


def test_org_admin_in_own_org_overrides_sa_only():
    from citra_workflow.router import _check_workflow_action
    wf = _wf()
    req = _req(user_id="auditor@acme.com", roles=("org_admin",))
    assert _check_workflow_action(wf, req, "edit") is None


def test_dept_owned_workflow_visible_to_dept_member():
    from citra_workflow.router import _check_workflow_action
    wf = _wf(owner_type="dept", owner_id="plant_ops")
    req = _req(user_id="bob@acme.com", dept_ids=("plant_ops",))
    assert _check_workflow_action(wf, req, "read") is None
    assert _check_workflow_action(wf, req, "run") is None
    # Members cannot edit dept-owned without being dept_admin
    assert _check_workflow_action(wf, req, "edit") is not None


def test_dept_owned_workflow_editable_by_dept_admin():
    from citra_workflow.router import _check_workflow_action
    wf = _wf(owner_type="dept", owner_id="plant_ops")
    req = _req(user_id="lead@acme.com", dept_ids=("plant_ops",), roles=("dept_admin",))
    assert _check_workflow_action(wf, req, "edit") is None


def test_org_owned_workflow_visible_to_anyone_in_org():
    from citra_workflow.router import _check_workflow_action
    wf = _wf(owner_type="org", owner_id="acme")
    req = _req(user_id="anyone@acme.com", org_id="acme", dept_ids=())
    assert _check_workflow_action(wf, req, "read") is None


def test_org_owned_workflow_editable_only_by_org_admin():
    from citra_workflow.router import _check_workflow_action
    wf = _wf(owner_type="org", owner_id="acme")
    req_user = _req(user_id="anyone@acme.com", org_id="acme", dept_ids=())
    assert _check_workflow_action(wf, req_user, "edit") is not None
    req_admin = _req(user_id="boss@acme.com", roles=("org_admin",))
    assert _check_workflow_action(wf, req_admin, "edit") is None


def test_visibility_read_org_broadens_to_anyone_in_org():
    from citra_workflow.router import _check_workflow_action
    wf = _wf(visibility={"read": "org", "run": "sa", "edit": "sa", "org_admin_override": True})
    req = _req(user_id="bystander@acme.com", dept_ids=(), sa_admin=(), sa_member=())
    assert _check_workflow_action(wf, req, "read") is None


def test_visibility_run_service_account_only_blocks_admin_too():
    from citra_workflow.router import _check_workflow_action
    wf = _wf(visibility={
        "read": "sa", "run": "service_account_only", "edit": "sa",
        "org_admin_override": True,
    })
    req = _req(sa_admin=("svc:plant@acme.citra.ai",))
    res = _check_workflow_action(wf, req, "run")
    assert res is not None and res[0] == 403


# ── _validate_create_ownership ─────────────────────────────────────────

def _claims(roles=("user",), sa_admin=(), sa_member=(), dept_ids=("plant_ops",), org_id="acme"):
    return {
        "user_id": "alice@acme.com",
        "email": "alice@acme.com",
        "org_id": org_id,
        "dept_ids": list(dept_ids),
        "roles": list(roles),
        "service_account_admin_of": list(sa_admin),
        "service_account_member_of": list(sa_member),
    }


def _create_body(**kw):
    from citra_workflow.models import CreateWorkflowRequest
    base = dict(name="x", nodes=[], edges=[])
    base.update(kw)
    return CreateWorkflowRequest(**base)


def test_create_rejects_user_owner_type():
    """Pydantic catches user-owner at the request schema level."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _create_body(owner_type="user", owner_id="alice@acme.com")


def test_create_rejects_user_owner_at_validator_as_defense_in_depth():
    """If the literal enum were ever loosened, the validator must still reject."""
    from citra_workflow.router import _validate_create_ownership
    from fastapi import HTTPException
    # Bypass Pydantic — pass a SimpleNamespace with the loosened field
    fake_body = SimpleNamespace(
        owner_type="user",
        owner_id="alice@acme.com",
        dept_ids=None,
    )
    with pytest.raises(HTTPException) as exc:
        _validate_create_ownership(_claims(), fake_body)
    assert exc.value.status_code == 400


def test_create_requires_sa_membership():
    from citra_workflow.router import _validate_create_ownership
    from fastapi import HTTPException
    body = _create_body(owner_type="service_account", owner_id="svc:foreign@acme.citra.ai")
    with pytest.raises(HTTPException) as exc:
        _validate_create_ownership(_claims(), body)
    assert exc.value.status_code == 403


def test_create_allows_sa_admin_member():
    from citra_workflow.router import _validate_create_ownership
    body = _create_body(owner_type="service_account", owner_id="svc:plant@acme.citra.ai")
    out = _validate_create_ownership(_claims(sa_admin=("svc:plant@acme.citra.ai",)), body)
    assert out["owner_type"] == "service_account"
    assert out["owner_id"] == "svc:plant@acme.citra.ai"


def test_create_org_admin_bypasses_membership():
    from citra_workflow.router import _validate_create_ownership
    body = _create_body(owner_type="service_account", owner_id="svc:foreign@acme.citra.ai")
    out = _validate_create_ownership(_claims(roles=("org_admin",)), body)
    assert out["owner_id"] == "svc:foreign@acme.citra.ai"


def test_create_dept_requires_dept_membership():
    from citra_workflow.router import _validate_create_ownership
    from fastapi import HTTPException
    body = _create_body(owner_type="dept", owner_id="finance")
    with pytest.raises(HTTPException) as exc:
        _validate_create_ownership(_claims(dept_ids=("plant_ops",)), body)
    assert exc.value.status_code == 403


def test_create_org_owner_requires_org_admin_role():
    from citra_workflow.router import _validate_create_ownership
    from fastapi import HTTPException
    body = _create_body(owner_type="org", owner_id="acme")
    with pytest.raises(HTTPException) as exc:
        _validate_create_ownership(_claims(roles=("user",)), body)
    assert exc.value.status_code == 403


def test_create_org_owner_allowed_for_org_admin():
    from citra_workflow.router import _validate_create_ownership
    body = _create_body(owner_type="org", owner_id="acme")
    out = _validate_create_ownership(_claims(roles=("org_admin",)), body)
    assert out["owner_type"] == "org"


# ── TransferRequest restricts to SA / dept / org ───────────────────────

def test_transfer_request_rejects_user_owner_type():
    from citra_workflow.router import TransferRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TransferRequest(new_owner_type="user", new_owner_id="alice@acme.com")


def test_transfer_request_accepts_sa_dept_org():
    from citra_workflow.router import TransferRequest
    for t in ("service_account", "dept", "org"):
        r = TransferRequest(new_owner_type=t, new_owner_id="target")
        assert r.new_owner_type == t


def test_inheritance_policy_request_accepts_transfer_to_dept():
    from citra_workflow.router import InheritancePolicyRequest
    r = InheritancePolicyRequest(
        inheritance_policy="transfer_to_dept",
        inheritance_target="plant_ops",
    )
    assert r.inheritance_policy == "transfer_to_dept"


# ── /share endpoint is 410 Gone ────────────────────────────────────────

def test_share_endpoint_returns_410():
    """The collaborators model was dropped; /share now 410-redirects to SA membership."""
    from citra_workflow.router import share_workflow, ShareRequest
    from fastapi import HTTPException
    import asyncio

    async def call_it():
        req = SimpleNamespace(state=SimpleNamespace(
            user_id="alice@acme.com", email="alice@acme.com",
            org_id="acme", dept_ids=[], roles=["user"],
            is_service_account=False,
            service_account_admin_of=[], service_account_member_of=[],
        ))
        await share_workflow(req, "wf-1", ShareRequest())

    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop().run_until_complete(call_it())
    assert exc.value.status_code == 410
    assert "service account" in str(exc.value.detail).lower()
