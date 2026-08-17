# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
AuthZ integration tests — source-mcp-template
===========================================

Covers the enterprise-critical scenarios the review called out:

  A. Valid HS256 JWT + allowed role + matching dept  → 200
  B. Valid JWT + role NOT in source.visibility.roles_allowed  → 403
  C. Expired JWT (enforce=true)  → 401
  D. Missing JWT + enforce=true  → 401
  E. Missing JWT + enforce=false (dev mode)  → 200 (audit records allowed=True)

Implementation notes
--------------------
The MCP verifies X-User-JWT against Citra's shared HS256 secret (JWT_SECRET) —
the same key user-service / smart-app-service sign with. Tests sign with that
shared secret directly, so they stay hermetic and fast (no JWKS HTTP hop).
"""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace
from typing import Any, Dict

import jwt
import pytest


_SHARED_SECRET = "test-shared-hs256-secret"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def signing_secret():
    return _SHARED_SECRET


def _sign(payload: Dict[str, Any], secret: str) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_claims(**over) -> Dict[str, Any]:
    base = {
        "sub": "user-1",
        "user_id": "user-1",
        "org_id": "org-acme",
        "dept_ids": ["hr"],
        "roles": ["user"],
        "iss": "Citra-AI",
        "aud": "citra-dept-mcp",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    base.update(over)
    return base


@pytest.fixture
def auth_module(monkeypatch, signing_secret):
    """Reload source-mcp-template/auth.py with enforce=true + the shared secret."""
    monkeypatch.setenv("AUTHZ_ENFORCE", "true")
    monkeypatch.setenv("JWT_SECRET", signing_secret)

    import auth as auth_mod  # type: ignore
    importlib.reload(auth_mod)
    return auth_mod


@pytest.fixture
def dev_auth_module(monkeypatch):
    """Reload auth.py with enforce=false (dev), no shared secret configured."""
    monkeypatch.setenv("AUTHZ_ENFORCE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)

    import auth as auth_mod  # type: ignore
    importlib.reload(auth_mod)
    return auth_mod


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def test_A_valid_token_allowed(auth_module, signing_secret):
    """Valid JWT, role is allowed, dept matches → claims returned, visibility OK."""
    token = _sign(_make_claims(), signing_secret)
    claims = auth_module.verify_user_jwt(token)
    assert claims["user_id"] == "user-1"

    source = {
        "source_id": "hr_handbook",
        "dept_id": "hr",
        "org_id": "org-acme",
        "visibility": {"roles_allowed": ["user", "dept_admin"], "public_within_org": True},
    }
    auth_module.check_visibility(source, claims)  # must not raise


def test_B_role_denied(auth_module, signing_secret):
    """Valid JWT but user's role not in roles_allowed → AuthzError 403."""
    token = _sign(_make_claims(roles=["guest"]), signing_secret)
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "hr_handbook",
        "dept_id": "hr",
        "org_id": "org-acme",
        "visibility": {"roles_allowed": ["dept_admin"], "public_within_org": True},
    }
    with pytest.raises(auth_module.AuthzError) as ei:
        auth_module.check_visibility(source, claims)
    assert ei.value.status_code == 403
    assert "role denied" in ei.value.reason


def test_C_expired_token(auth_module, signing_secret):
    """Expired JWT → AuthzError 401."""
    expired = _make_claims(
        iat=int(time.time()) - 3600,
        exp=int(time.time()) - 600,  # well past leeway
    )
    token = _sign(expired, signing_secret)
    with pytest.raises(auth_module.AuthzError) as ei:
        auth_module.verify_user_jwt(token)
    assert ei.value.status_code == 401
    assert "expired" in ei.value.reason


def test_D_missing_token_enforce(auth_module):
    """No token + enforce=true → AuthzError 401."""
    with pytest.raises(auth_module.AuthzError) as ei:
        auth_module.verify_user_jwt(None)
    assert ei.value.status_code == 401


def test_E_missing_token_dev_mode(dev_auth_module):
    """No token + enforce=false → empty claims, no raise."""
    claims = dev_auth_module.verify_user_jwt(None)
    assert claims == {}

    # Visibility in dev mode also logs-and-allows when caller has no claims.
    source = {
        "source_id": "hr_handbook",
        "dept_id": "hr",
        "org_id": "org-acme",
        "visibility": {"roles_allowed": ["dept_admin"]},
    }
    # Should NOT raise in dev mode even though role would otherwise deny.
    dev_auth_module.check_visibility(source, {})


def test_H_cross_org_allowlist(auth_module, signing_secret):
    """User from a different org can access if listed in cross_org_ids."""
    token = _sign(_make_claims(org_id="org-partner", dept_ids=[]),
                  signing_secret)
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "shared_contracts",
        "dept_id": "legal",
        "org_id": "org-acme",
        "visibility": {
            "roles_allowed": ["user"],
            "public_within_org": False,
            "cross_org_ids": ["org-partner"],
        },
    }
    auth_module.check_visibility(source, claims)  # must not raise


def test_I_missing_roles_allowed_defaults_to_user(auth_module, signing_secret):
    """A source that OMITS roles_allowed must still enforce the role gate,
    defaulting to {"user"}. Regression guard for the bug where an absent
    roles_allowed silently skipped the role check.
    """
    # User with only an exotic role not including "user" must be denied
    # against a source that forgot to declare roles_allowed.
    token = _sign(_make_claims(roles=["guest"]), signing_secret)
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "misconfigured_source",
        "dept_id": "hr",
        "org_id": "org-acme",
        # NOTE: no roles_allowed declared — must default to ["user"] and deny.
        "visibility": {"public_within_org": True},
    }
    with pytest.raises(auth_module.AuthzError) as ei:
        auth_module.check_visibility(source, claims)
    assert ei.value.status_code == 403
    assert "role denied" in ei.value.reason


def test_J_missing_roles_allowed_still_allows_plain_user(auth_module, signing_secret):
    """Same source (no roles_allowed declared) — a plain `user` role still
    passes the role gate (because the default is {"user"}), and then the
    dept/org gate applies normally.
    """
    token = _sign(_make_claims(roles=["user"]), signing_secret)
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "misconfigured_source",
        "dept_id": "hr",
        "org_id": "org-acme",
        "visibility": {"public_within_org": True},
    }
    auth_module.check_visibility(source, claims)  # must not raise


# ---------------------------------------------------------------------------
# Phase C.1 — org_admin cross-dept bypass tests
# ---------------------------------------------------------------------------

def test_K_org_admin_can_read_other_dept_in_same_org(auth_module, signing_secret):
    """org_admin sees every dept's data within their own org.

    User is in `hr` dept but is org_admin → can query the `finance` source
    even though their dept_ids don't include `finance` and the source isn't
    public_within_org.
    """
    token = _sign(
        _make_claims(org_id="org-acme", dept_ids=["hr"], roles=["org_admin", "user"]),
        signing_secret,
    )
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "finance_ledger",
        "dept_id": "finance",
        "org_id": "org-acme",
        "visibility": {
            "roles_allowed": ["user", "org_admin"],
            "public_within_org": False,
        },
    }
    auth_module.check_visibility(source, claims)  # must not raise

    allowed, reason = auth_module.is_visible(source, claims)
    assert allowed is True
    assert reason == "org_admin_within_org"


def test_L_dept_admin_cannot_cross_into_other_dept(auth_module, signing_secret):
    """dept_admin is strictly scoped to their dept_ids.

    User is dept_admin for `hr` but tries to read `finance` data —
    must be denied. dept_admin is deliberately NOT a cross-dept role.
    """
    token = _sign(
        _make_claims(org_id="org-acme", dept_ids=["hr"], roles=["dept_admin", "user"]),
        signing_secret,
    )
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "finance_ledger",
        "dept_id": "finance",
        "org_id": "org-acme",
        "visibility": {
            "roles_allowed": ["user", "dept_admin"],
            "public_within_org": False,
        },
    }
    with pytest.raises(auth_module.AuthzError) as ei:
        auth_module.check_visibility(source, claims)
    assert ei.value.status_code == 403
    assert "org/dept denied" in ei.value.reason


def test_M_org_admin_cannot_cross_org_without_allowlist(auth_module, signing_secret):
    """org_admin power is scoped to their own org.

    org_admin of org-partner cannot read org-acme's data unless org-partner
    is in the source's cross_org_ids allowlist. This protects multi-tenant
    isolation when SaaS deployments share an MCP host.
    """
    token = _sign(
        _make_claims(
            org_id="org-partner",
            dept_ids=["legal"],
            roles=["org_admin", "user"],
        ),
        signing_secret,
    )
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "internal_contracts",
        "dept_id": "legal",
        "org_id": "org-acme",
        "visibility": {
            "roles_allowed": ["user", "org_admin"],
            "public_within_org": False,
            # cross_org_ids deliberately empty
        },
    }
    with pytest.raises(auth_module.AuthzError) as ei:
        auth_module.check_visibility(source, claims)
    assert ei.value.status_code == 403


def test_N_dept_member_reason_wins_over_org_admin(auth_module, signing_secret):
    """When a user is BOTH dept-member AND org_admin, the audit reason
    should be `dept_member` (the more specific match), not
    `org_admin_within_org`. Keeps audit logs informative.
    """
    token = _sign(
        _make_claims(org_id="org-acme", dept_ids=["finance"], roles=["org_admin", "user"]),
        signing_secret,
    )
    claims = auth_module.verify_user_jwt(token)

    source = {
        "source_id": "finance_ledger",
        "dept_id": "finance",
        "org_id": "org-acme",
        "visibility": {"roles_allowed": ["user", "org_admin"]},
    }
    allowed, reason = auth_module.is_visible(source, claims)
    assert allowed is True
    assert reason == "dept_member"


def test_O_impersonation_claims_in_audit(auth_module, signing_secret, monkeypatch):
    """When the JWT carries `act` + `impersonation_id`, log_audit captures
    them. Regression guard for the C.0b audit record additions.
    """
    import asyncio

    claims = {
        "user_id": "anita@acme-cement.citra.ai",
        "org_id": "acme-cement",
        "dept_ids": ["plant_ops"],
        "roles": ["user"],
        "act": "rohit@trustedweartech.com",
        "impersonation_id": "test-imp-123",
    }

    captured_records = _capture_audit(monkeypatch, auth_module)

    asyncio.run(auth_module.log_audit(
        source_id="quality_test_results",
        query="show me batch failures",
        claims=claims,
        result_count=12,
        allowed=True,
        reason="allowed",
    ))

    assert len(captured_records) == 1
    rec = captured_records[0]
    assert rec["act"] == "rohit@trustedweartech.com"
    assert rec["impersonation_id"] == "test-imp-123"
    assert rec["user_id"] == "anita@acme-cement.citra.ai"


# ---------------------------------------------------------------------------
# Audit provenance — who modified (user) + whose data (source dept)
# ---------------------------------------------------------------------------

def _capture_audit(monkeypatch, auth_mod):
    """Patch the audit HTTP sink + config so log_audit's records land in a local
    list. Returns it. Index creation is owned by smart-app-service now, so this
    fakes the ingest POST (``_post_audit_records``), not Mongo."""
    captured_records = []

    async def _fake_post(batch):
        captured_records.extend(batch)
        return True

    monkeypatch.setattr(auth_mod, "_post_audit_records", _fake_post)
    import sys
    fake_settings = SimpleNamespace(
        smart_app_service_url="http://smart-app-service",
        org_id="bsphcl",
        dept_ids=["commercial", "revenue"],
    )
    fake_config = SimpleNamespace(get_settings=lambda: fake_settings)
    monkeypatch.setitem(sys.modules, "config", fake_config)
    return captured_records


def test_P_audit_stamps_source_dept_and_user_identity(auth_module, monkeypatch):
    """An execute_action audit record must carry BOTH sides of provenance:
    the acting user (user_id/email/name + user_dept_ids) and the owning
    dept of the data that was written (source_dept_id from the source doc),
    plus the MCP deployment identity (mcp_org_id/mcp_dept_ids).
    """
    import asyncio

    records = _capture_audit(monkeypatch, auth_module)
    claims = {
        "user_id": "officer-7",
        "email": "officer7@bsphcl.in",
        "name": "Officer Seven",
        "org_id": "bsphcl",
        "dept_ids": ["commercial"],
        "roles": ["user"],
    }
    source = {
        "source_id": "billing_db",
        "dept_id": "revenue",
        "name": "Billing Postgres",
        "org_id": "bsphcl",
    }

    asyncio.run(auth_module.log_audit(
        source_id="billing_db",
        query="execute_action update_consumer_address on consumers",
        claims=claims,
        result_count=1,
        allowed=True,
        reason="ok",
        op="execute_action",
        source=source,
    ))

    assert len(records) == 1
    rec = records[0]
    # Who
    assert rec["user_id"] == "officer-7"
    assert rec["user_email"] == "officer7@bsphcl.in"
    assert rec["user_name"] == "Officer Seven"
    assert rec["user_dept_ids"] == ["commercial"]
    assert rec["dept_ids"] == ["commercial"]  # deprecated alias preserved
    # Where
    assert rec["source_dept_id"] == "revenue"
    assert rec["source_name"] == "Billing Postgres"
    assert rec["mcp_org_id"] == "bsphcl"
    assert rec["mcp_dept_ids"] == ["commercial", "revenue"]


def test_Q_audit_source_owner_dept_id_fallback(auth_module, monkeypatch):
    """A source doc using `owner_dept_id` (app-owned tables) instead of
    `dept_id` must still resolve — same accessor as check_visibility.
    """
    import asyncio

    records = _capture_audit(monkeypatch, auth_module)
    source = {"source_id": "app_overlay", "owner_dept_id": "commercial"}

    asyncio.run(auth_module.log_audit(
        source_id="app_overlay",
        query="execute_action add_note on notes",
        claims={"user_id": "u1", "dept_ids": ["commercial"]},
        result_count=1,
        allowed=True,
        op="execute_action",
        source=source,
    ))

    assert records[0]["source_dept_id"] == "commercial"


def test_R_audit_write_without_dept_is_none_and_warns(auth_module, monkeypatch, caplog):
    """A WRITE audited with no resolvable source dept records None (no
    invented fallback) and logs a loud warning so the misconfigured source
    doc gets noticed.
    """
    import asyncio
    import logging

    records = _capture_audit(monkeypatch, auth_module)
    source = {"source_id": "mystery_source"}  # no dept_id / owner_dept_id

    with caplog.at_level(logging.WARNING):
        asyncio.run(auth_module.log_audit(
            source_id="mystery_source",
            query="execute_action update_x on y",
            claims={"user_id": "u1"},
            result_count=0,
            allowed=True,
            op="execute_action",
            source=source,
        ))

    assert records[0]["source_dept_id"] is None
    assert any("no resolvable dept" in m for m in caplog.messages)


def test_S_audit_record_is_json_safe(auth_module, monkeypatch):
    """The audit record ships to smart-app-service as JSON, so every field must
    be JSON-serializable: ``ts`` is a float epoch and there is NO ``ts_date``
    datetime (the writer stamps its own BSON-date ``received_at`` and owns any
    TTL). A non-serializable field would silently drop the whole batch.
    """
    import asyncio
    import json

    records = _capture_audit(monkeypatch, auth_module)
    asyncio.run(auth_module.log_audit(
        source_id="s1", query="q", claims={"user_id": "u1"},
        result_count=0, allowed=True, source={"dept_id": "d1"},
    ))
    rec = records[0]
    assert isinstance(rec["ts"], float) and "ts_date" not in rec
    json.dumps(rec)  # must not raise — the record is wire-safe
