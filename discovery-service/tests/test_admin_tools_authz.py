# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""GET /admin/tools authz — who gets in, and which rows they see.

This endpoint was super_admin-ONLY, which made it unusable for its only caller:
the citra-workflow ``/api/dept-sources/_discovery/tools`` proxy admits
org_admin/dept_admin, so those callers always drew a 403 and the admin UI's MCP
Fleet panel rendered "0 MCP instances registered" — an authz failure wearing the
costume of an empty fleet.

The gate now admits every admin role and lets ``_tool_visible_to`` decide the
ROWS, so an org_admin sees their own fleet and never another tenant's.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import main


def _tool(tool_id: str, org: str, dept: str, **over):
    doc = {
        "tool_id": tool_id,
        "name": tool_id,
        "org_ids": [org],
        "dept_ids": [dept],
        "source_id": tool_id,
        "active": True,
        "visibility": {"roles_allowed": ["user"], "cross_org_ids": [], "public_within_org": False},
        # Must survive the projection untouched — the fleet's ●healthy/●stale
        # badge reads last_heartbeat, so a shape change here blanks the column.
        "last_heartbeat": None,
    }
    doc.update(over)
    return doc


ACME_BILLING = _tool("acme-billing", "acme", "finance")
ACME_HR      = _tool("acme-hr", "acme", "hr")
OTHER_CORP   = _tool("other-payroll", "othercorp", "finance")

ALL_DOCS = [ACME_BILLING, ACME_HR, OTHER_CORP]


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield dict(d)  # copy: the endpoint mutates rows in place
        return gen()


class _FakeCol:
    def __init__(self, docs):
        self._docs = docs
        self.last_query = None

    def find(self, query, projection=None):
        self.last_query = query
        docs = self._docs
        if query.get("active") is True:
            docs = [d for d in docs if d.get("active")]
        return _FakeCursor(docs)


@pytest.fixture
def fake_col(monkeypatch):
    col = _FakeCol(ALL_DOCS)
    monkeypatch.setattr(main, "get_tools_col", lambda: col)
    return col


async def _call(claims, active_only=False):
    return await main.admin_list_tools(active_only=active_only, claims=claims)


def _ids(resp):
    return sorted(t["tool_id"] for t in resp["tools"])


# ── The gate ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_admin_is_admitted_and_sees_own_org(fake_col):
    """The regression: org_admin used to 403 here and the UI showed 0 instances."""
    resp = await _call({"roles": ["org_admin"], "org_id": "acme", "dept_ids": []})
    assert _ids(resp) == ["acme-billing", "acme-hr"]
    assert resp["total"] == 2


@pytest.mark.asyncio
async def test_org_admin_never_sees_another_tenant(fake_col):
    resp = await _call({"roles": ["org_admin"], "org_id": "acme", "dept_ids": []})
    assert "other-payroll" not in _ids(resp)


@pytest.mark.asyncio
async def test_super_admin_still_sees_every_org(fake_col):
    resp = await _call({"roles": ["super_admin"], "org_id": "acme", "dept_ids": []})
    assert _ids(resp) == ["acme-billing", "acme-hr", "other-payroll"]


@pytest.mark.asyncio
async def test_dept_admin_scoped_to_own_dept(fake_col):
    resp = await _call({"roles": ["dept_admin", "user"], "org_id": "acme", "dept_ids": ["finance"]})
    assert _ids(resp) == ["acme-billing"]


@pytest.mark.asyncio
async def test_plain_user_is_rejected(fake_col):
    with pytest.raises(HTTPException) as exc:
        await _call({"roles": ["user"], "org_id": "acme", "dept_ids": ["finance"]})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_no_roles_claim_is_rejected(fake_col):
    with pytest.raises(HTTPException) as exc:
        await _call({"org_id": "acme"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_phantom_admin_role_grants_nothing(fake_col):
    """"admin"/"superadmin" aren't in the platform enum. A token claiming them
    must NOT be treated as an admin — the old citra-workflow set tested for
    exactly these strings, which is why those branches were dead."""
    for phantom in ("admin", "superadmin"):
        with pytest.raises(HTTPException) as exc:
            await _call({"roles": [phantom], "org_id": "acme", "dept_ids": []})
        assert exc.value.status_code == 403


# ── Scoping is not widenable ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_trusted_service_scope_does_not_widen_a_human(fake_col):
    """Rule 0 in _tool_visible_to returns EVERY tool for a trusted service
    scope. This endpoint must not pass `scope` through, or an org_admin holding
    a runtime-scoped token would read the whole fleet cross-tenant."""
    resp = await _call({
        "roles": ["org_admin"], "org_id": "acme", "dept_ids": [],
        "scope": "citra-app-runtime",
    })
    assert "other-payroll" not in _ids(resp)


# ── Contract preserved ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_only_still_filters(fake_col):
    await _call({"roles": ["super_admin"], "org_id": "acme"}, active_only=True)
    assert fake_col.last_query == {"active": True}


@pytest.mark.asyncio
async def test_legacy_singular_dept_id_claim_still_scopes(fake_col):
    """dept_ids (array, current) and dept_id (string, legacy) both work."""
    resp = await _call({"roles": ["dept_admin", "user"], "org_id": "acme", "dept_id": "hr"})
    assert _ids(resp) == ["acme-hr"]


@pytest.mark.asyncio
async def test_heartbeat_field_survives_for_the_stale_badge(monkeypatch):
    from datetime import datetime, timezone
    beat = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    col = _FakeCol([_tool("acme-billing", "acme", "finance", last_heartbeat=beat)])
    monkeypatch.setattr(main, "get_tools_col", lambda: col)
    resp = await _call({"roles": ["super_admin"], "org_id": "acme"})
    assert resp["tools"][0]["last_heartbeat"] == beat.isoformat()
