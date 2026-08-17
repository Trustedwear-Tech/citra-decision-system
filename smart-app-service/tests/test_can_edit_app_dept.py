# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""A dept_admin must reach the app their own UI labels with their dept.

Observed on acme-bank prod: both lending apps carried `dept_ids: []` and
`audience: "dept:lending"`. The card rendered "Dept · lending" (from audience),
while `_can_edit_app` read only `dept_ids` — so Vikram, dept_admin of lending,
was refused the memory surface of the app his screen attributed to lending.
The gate disagreed with the label directly above it.

`_app_dept_ids` already unions both sources for halt scoping; these pin that
authorization reads the same union, and that the org check still bounds it.
"""
from __future__ import annotations

from main import _can_edit_app

DEPT_ADMIN = {"user_roles": ["dept_admin"], "user_dept_ids": ["lending"],
              "user_org_id": "acme-bank"}


def _app(dept_ids=None, audience=None, org="acme-bank"):
    return {"tenant_id": org,
            "app_spec": {"org_id": org, "owner_type": "service_account",
                         "owner_id": "svc:someone-else@acme-bank.citra.ai",
                         "dept_ids": list(dept_ids or []),
                         **({"audience": audience} if audience else {})}}


def _can(app, **over):
    kw = {**DEPT_ADMIN, **over}
    return _can_edit_app(app, "vikram@acme-bank-demo.citra.ai", "acme-bank", **kw)


def test_dept_from_audience_grants_the_dept_admin():
    """The acme-bank shape: empty dept_ids, dept declared by audience."""
    assert _can(_app(dept_ids=[], audience="dept:lending")) is True


def test_dept_ids_still_works_on_its_own():
    assert _can(_app(dept_ids=["lending"])) is True


def test_a_different_dept_is_still_refused():
    assert _can(_app(dept_ids=[], audience="dept:collections")) is False


def test_no_dept_anywhere_is_still_refused():
    """Absent a dept declaration there is nothing for dept_admin to match."""
    assert _can(_app(dept_ids=[])) is False


def test_audience_dept_does_not_cross_the_org_boundary():
    """Dept ids are non-unique human strings; a lending admin at acme-bank must
    not reach another tenant's lending app."""
    assert _can(_app(dept_ids=[], audience="dept:lending", org="other-bank")) is False


def test_non_dept_audiences_are_ignored():
    for aud in ("org", "owner", "team:svc:x@acme-bank.citra.ai"):
        assert _can(_app(dept_ids=[], audience=aud)) is False, aud


def test_plain_user_is_refused_even_with_a_matching_dept():
    assert _can(_app(dept_ids=[], audience="dept:lending"),
                user_roles=["user"]) is False


def test_org_admin_is_unaffected():
    assert _can(_app(dept_ids=[]), user_roles=["org_admin"]) is True
