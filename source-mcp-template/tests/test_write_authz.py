# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Unit tests for the write-authz gate — auth.check_write_permission.

Covers integration-test cases C-16/C-17 (write-permission enforcement)
without needing a live MCP + minted role-specific JWTs.

All references go through the `auth` module at call time (not
`from auth import ...` bindings): another test module reloads `auth`,
which would otherwise leave us holding a stale AuthzError class identity
and break `pytest.raises`.
"""
import pytest

import auth


@pytest.fixture(autouse=True)
def _enforce_on():
    """Force AUTHZ_ENFORCE on per-test — the denial path routes through
    _deny, which honours it, and other test modules mutate the shared
    auth._ENFORCE global."""
    prev = auth._ENFORCE
    auth._ENFORCE = True
    yield
    auth._ENFORCE = prev


def test_super_admin_always_allowed():
    # super_admin bypasses even an allow-list that excludes everyone.
    auth.check_write_permission({"roles": ["super_admin"]},
                                roles_allowed_write=["nobody"], action_id="a")


def test_role_in_explicit_allowlist_passes():
    auth.check_write_permission({"roles": ["dept_admin"]},
                                roles_allowed_write=["dept_admin", "org_admin"],
                                action_id="release_batch")


def test_role_not_in_allowlist_denied():
    with pytest.raises(auth.AuthzError) as ei:
        auth.check_write_permission({"roles": ["user"]},
                                    roles_allowed_write=["dept_admin"],
                                    action_id="release_batch")
    assert ei.value.status_code == 403


def test_default_roles_block_plain_user():
    # No roles_allowed_write -> DEFAULT_WRITE_ROLES (dept_admin+); a plain
    # `user` must be denied.
    assert "user" not in auth.DEFAULT_WRITE_ROLES
    with pytest.raises(auth.AuthzError):
        auth.check_write_permission({"roles": ["user"]}, action_id="a")


def test_default_roles_allow_dept_admin():
    auth.check_write_permission({"roles": ["dept_admin"]}, action_id="a")


def test_missing_roles_claim_treated_as_user_and_denied():
    with pytest.raises(auth.AuthzError):
        auth.check_write_permission({}, roles_allowed_write=["dept_admin"], action_id="a")


def test_action_explicitly_allowing_user_passes():
    # update_dispatch_status-style action that opens the write to `user`.
    auth.check_write_permission({"roles": ["user"]},
                                roles_allowed_write=["user", "dept_admin"],
                                action_id="update_dispatch_status")
