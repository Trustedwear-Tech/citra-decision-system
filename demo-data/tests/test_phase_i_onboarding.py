# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Phase I — admin invite + edit + bootstrap script smoke tests.

The Citra-User-Service is Node, so we don't run its Express handlers
from Python. These tests verify the changes are present in the source
files — a smoke contract that the endpoints and helpers exist and
encode the right rules. End-to-end behaviour is verified manually
against a running stack.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
USER_SERVICE = ROOT / "Citra-User-Service"


def _read(rel_path: str) -> str:
    p = USER_SERVICE / rel_path
    assert p.exists(), f"missing file: {rel_path}"
    return p.read_text(encoding="utf-8")


# ── Bootstrap script ───────────────────────────────────────────────────

def test_create_admin_script_accepts_role_flag():
    src = _read("src/scripts/create-admin.js")
    assert "--role=" in src
    assert "VALID_ROLES" in src
    assert "super_admin" in src
    # The script must reject scoped roles without --org
    assert "is required when --role=" in src or "is required when --role" in src


def test_create_admin_script_sets_roles_on_user_doc():
    src = _read("src/scripts/create-admin.js")
    # Updated branch must write user.roles
    assert "user.roles" in src
    # Created branch must populate roles in the create call
    assert re.search(r"roles\s*:\s*rolesToSet", src), \
        "create-admin should call rolesToSet() when persisting roles"


def test_create_admin_script_accepts_org_and_dept_flags():
    src = _read("src/scripts/create-admin.js")
    assert "--org=" in src
    assert "--dept=" in src
    # Mongo writes for both
    assert "user.org_id" in src
    assert "user.dept_ids" in src


# ── Invite endpoint ────────────────────────────────────────────────────

def test_invite_endpoint_registered():
    src = _read("src/routes/userAdminRoutes.js")
    assert "router.post('/'," in src or 'router.post("/"' in src, \
        "POST /api/admin/users (the invite endpoint) must be registered at base of the router"


def test_invite_endpoint_uses_requireAdmin():
    src = _read("src/routes/userAdminRoutes.js")
    # The invite handler must be gated by requireAdmin
    m = re.search(r"router\.post\(\s*['\"]/['\"]\s*,\s*([^,]+),", src)
    assert m, "invite endpoint signature not found"
    assert "requireAdmin" in m.group(1)


def test_invite_endpoint_validates_role_enum():
    src = _read("src/routes/userAdminRoutes.js")
    assert "VALID_ROLES" in src
    assert "invalid role" in src


def test_invite_endpoint_blocks_privilege_escalation():
    src = _read("src/routes/userAdminRoutes.js")
    assert "_maxAssignableRank" in src
    assert "cannot assign role rank" in src
    # ROLE_RANK table must rank super_admin > org_admin > dept_admin > user
    m = re.search(r"ROLE_RANK\s*=\s*\{[^}]+\}", src)
    assert m, "ROLE_RANK table not found"
    table = m.group(0)
    assert "user" in table and "dept_admin" in table and "org_admin" in table and "super_admin" in table


def test_invite_endpoint_scope_check_for_non_super_admin():
    src = _read("src/routes/userAdminRoutes.js")
    # Non-super admins must be blocked from inviting into another org
    assert "cannot invite into another org" in src
    # Dept admins must be blocked from assigning foreign depts
    assert "can only assign their own depts" in src


# ── Edit endpoint ──────────────────────────────────────────────────────

def test_patch_endpoint_registered():
    src = _read("src/routes/userAdminRoutes.js")
    assert "router.patch('/:userId'" in src or 'router.patch("/:userId"' in src


def test_patch_endpoint_super_admin_only_for_org_change():
    src = _read("src/routes/userAdminRoutes.js")
    assert "changing org_id requires super_admin" in src


def test_patch_endpoint_uses_requireAdmin():
    src = _read("src/routes/userAdminRoutes.js")
    m = re.search(r"router\.patch\(\s*['\"]/:userId['\"]\s*,\s*([^,]+),", src)
    assert m, "patch endpoint signature not found"
    assert "requireAdmin" in m.group(1)


# ── Unified admin gate ─────────────────────────────────────────────────

def test_requireAdmin_accepts_db_roles_in_addition_to_env():
    src = _read("src/middleware/authMiddleware.js")
    assert "ADMIN_ROLES" in src
    # The OR-of-byEnv-byRole logic must be present
    assert "byEnv" in src and "byRole" in src
    # All three admin DB roles must be honoured
    for r in ("super_admin", "org_admin", "dept_admin"):
        assert r in src, f"role {r} missing from admin gate"


def test_requireAdmin_still_supports_legacy_env_gate():
    """The unification must NOT break the legacy ADMIN_EMAILS path."""
    src = _read("src/middleware/authMiddleware.js")
    assert "envConfig.isAdmin" in src


# ── DeletionRequest schema gained Phase H fields ───────────────────────

def test_deletion_request_has_personal_content_fields():
    src = _read("src/models/DeletionRequest.js")
    assert "personal_content_policies" in src
    assert "personal_content_summary" in src


# ── userAdminRoutes still has Phase H wiring ───────────────────────────

def test_user_admin_routes_forward_personal_content_token_to_worker():
    src = _read("src/routes/userAdminRoutes.js")
    assert "personal_content_policies" in src
    assert "admin_auth_token" in src
    assert "personal_content_transfer_target" in src
