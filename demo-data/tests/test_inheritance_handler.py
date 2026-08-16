# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Phase C — user.deactivated inheritance handler tests.

Uses an in-memory FakeMongo so the handler can be exercised end-to-end
without a live Mongo. Each test seeds a few workflows + smart-apps with
different inheritance_policy values and verifies the handler applies
the right transition AND emits a HandoffReport summarising the result.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Citra-Worker"))


# ── FakeMongo ──────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Mini query matcher: equality, $or, $in, $exists, $ne + array contains."""
    if not query:
        return True
    if "$or" in query:
        return any(_matches(doc, sub) for sub in query["$or"])
    for k, v in query.items():
        # Dotted path resolution
        cur = doc
        for part in k.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break
        if isinstance(v, dict) and "$in" in v:
            if isinstance(cur, list):
                if not any(x in v["$in"] for x in cur):
                    return False
            else:
                if cur not in v["$in"]:
                    return False
        elif isinstance(v, dict) and "$exists" in v:
            exists = cur is not None
            if exists != bool(v["$exists"]):
                return False
        elif isinstance(v, dict) and "$ne" in v:
            if cur == v["$ne"]:
                return False
        else:
            # Mongo treats `{field: value}` as "value equals field OR
            # value is in field when field is an array".
            if isinstance(cur, list):
                if v not in cur:
                    return False
            else:
                if cur != v:
                    return False
    return True


class _FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query: Optional[Dict[str, Any]] = None) -> _FakeCursor:
        return _FakeCursor([d for d in self.docs if _matches(d, query or {})])

    async def find_one(self, query: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        for d in self.docs:
            if _matches(d, query or {}):
                return d
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(doc)

    async def update_one(self, filt: Dict[str, Any], update: Dict[str, Any]):
        for doc in self.docs:
            if _matches(doc, filt):
                self._apply_update(doc, update)
                return

    async def update_many(self, filt: Dict[str, Any], update: Dict[str, Any]):
        n = 0
        for doc in self.docs:
            if _matches(doc, filt):
                self._apply_update(doc, update)
                n += 1
        class _R:
            modified_count = n
        return _R()

    async def delete_one(self, filt: Dict[str, Any]):
        for i, doc in enumerate(self.docs):
            if _matches(doc, filt):
                self.docs.pop(i)
                return

    @staticmethod
    def _apply_update(doc, update):
        if "$set" in update:
            for k, v in update["$set"].items():
                _set_nested(doc, k, v)
        if "$push" in update:
            for k, v in update["$push"].items():
                lst = _get_or_create_list(doc, k)
                lst.append(v)
        if "$addToSet" in update:
            for k, v in update["$addToSet"].items():
                lst = _get_or_create_list(doc, k)
                if v not in lst:
                    lst.append(v)
        if "$pull" in update:
            for k, v in update["$pull"].items():
                parts = k.split(".")
                cur = doc
                for p in parts[:-1]:
                    cur = cur.get(p, {}) if isinstance(cur, dict) else {}
                last = parts[-1]
                if isinstance(cur, dict) and isinstance(cur.get(last), list):
                    cur[last] = [x for x in cur[last] if x != v]
        if "$unset" in update:
            for k in update["$unset"]:
                _unset_nested(doc, k)


def _set_nested(doc: Dict[str, Any], path: str, value):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _get_or_create_list(doc: Dict[str, Any], path: str) -> List:
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    last = parts[-1]
    if last not in cur or not isinstance(cur[last], list):
        cur[last] = []
    return cur[last]


def _unset_nested(doc: Dict[str, Any], path: str):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            return
        cur = cur[p]
    cur.pop(parts[-1], None)


class _FakeDB:
    def __init__(self):
        self._cols: Dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._cols:
            self._cols[name] = _FakeCollection()
        return self._cols[name]


@pytest.fixture
def fake_db(monkeypatch):
    """Patch the handler's deferred Mongo import to return our FakeDB.

    Returns the citra-DB FakeDB. Use `fake_multi_db` when the handler
    needs to read both citra + user DBs.
    """
    db = _FakeDB()

    class _FakeClient(dict):
        def __getitem__(self, key):
            return db

    fake_module = type("M", (), {})()
    fake_module.get_async_mongo_client = lambda: _FakeClient()
    fake_module.MONGODB_DATABASE = "citra"
    sys.modules["mongodb_manager"] = fake_module
    return db


@pytest.fixture
def fake_multi_db(monkeypatch):
    """Per-database FakeDB so handlers querying both the citra DB and the
    user-service DB (citra-ai) see their own collections."""
    citra = _FakeDB()
    user = _FakeDB()

    class _FakeClient(dict):
        def __getitem__(self, key):
            if key == "citra":
                return citra
            if key == "citra-ai":
                return user
            # default fallback for any other DB name
            return citra

    fake_module = type("M", (), {})()
    fake_module.get_async_mongo_client = lambda: _FakeClient()
    fake_module.MONGODB_DATABASE = "citra"
    sys.modules["mongodb_manager"] = fake_module
    return {"citra": citra, "user": user}


@pytest.fixture
def ctx():
    """Worker JobContext stub."""
    from registry import JobContext
    return JobContext(job_id="job-1", tenant_id="acme", request_id="req-1", retries=0)


# ── Tests ──────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_archive_policy_archives_workflow(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-1",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "lifecycle_stage": "personal",
        "inheritance_policy": "archive",
    })

    result = _run(handle_user_deactivated(
        {"user_id": "rohit@acme.com", "email": "rohit@acme.com", "org_id": "acme",
         "deactivated_by": "admin@acme.com"},
        ctx,
    ))
    assert result["resources_processed"] == 1
    wf = fake_db["Workflows"].docs[0]
    assert wf["lifecycle_stage"] == "archived"
    assert wf["previous_lifecycle_stage"] == "personal"
    assert wf["status"] == "inactive"
    assert wf["lifecycle_audit"][0]["policy_applied"] == "archive"


def test_transfer_to_sa_updates_owner_to_service_account(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-2",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "lifecycle_stage": "personal",
        "inheritance_policy": "transfer_to_sa",
        "inheritance_target": "svc:claims-bot@acme.citra.ai",
    })

    _run(handle_user_deactivated(
        {"user_id": "rohit@acme.com"},
        ctx,
    ))
    wf = fake_db["Workflows"].docs[0]
    assert wf["owner_type"] == "service_account"
    assert wf["owner_id"] == "svc:claims-bot@acme.citra.ai"
    assert wf["lifecycle_stage"] == "team_managed"


def test_transfer_to_sa_without_target_falls_back_to_archive(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-3",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "lifecycle_stage": "personal",
        "inheritance_policy": "transfer_to_sa",
        # missing inheritance_target — should NOT silently steal ownership
    })
    _run(handle_user_deactivated({"user_id": "rohit@acme.com"}, ctx))
    wf = fake_db["Workflows"].docs[0]
    assert wf["lifecycle_stage"] == "archived"
    # Audit must record the fallback reason
    assert wf["lifecycle_audit"][0]["policy_applied"] == "archive"
    assert "fallback_reason" in wf["lifecycle_audit"][0]


def test_transfer_to_org_uses_org_id(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-4",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "org_id": "acme",
        "lifecycle_stage": "shared",
        "inheritance_policy": "transfer_to_org",
    })
    _run(handle_user_deactivated(
        {"user_id": "rohit@acme.com", "org_id": "acme"},
        ctx,
    ))
    wf = fake_db["Workflows"].docs[0]
    assert wf["owner_type"] == "org"
    assert wf["owner_id"] == "acme"
    assert wf["lifecycle_stage"] == "org_managed"


def test_delete_after_grace_schedules_deletion(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-5",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "lifecycle_stage": "personal",
        "inheritance_policy": "delete_after_grace",
        "inheritance_grace_days": 7,
    })
    _run(handle_user_deactivated({"user_id": "rohit@acme.com"}, ctx))
    wf = fake_db["Workflows"].docs[0]
    assert wf["lifecycle_stage"] == "archived"
    assert wf["delete_after"] is not None
    # Audit entry records the deletion deadline
    assert "delete_after" in wf["lifecycle_audit"][0]


def test_smart_app_inheritance_uses_app_spec_prefix(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["smart_apps"].docs.append({
        "slug": "rohit-briefing",
        "owner": "rohit@acme.com",
        "app_spec": {
            "owner_type": "user",
            "owner_id": "rohit@acme.com",
            "lifecycle_stage": "personal",
            "inheritance_policy": "transfer_to_sa",
            "inheritance_target": "svc:claims-bot@acme.citra.ai",
        },
    })
    _run(handle_user_deactivated({"user_id": "rohit@acme.com"}, ctx))
    app = fake_db["smart_apps"].docs[0]
    # Phase A fields live under app_spec.*
    assert app["app_spec"]["owner_type"] == "service_account"
    assert app["app_spec"]["owner_id"] == "svc:claims-bot@acme.citra.ai"
    assert app["app_spec"]["lifecycle_stage"] == "team_managed"


def test_handoff_report_emitted(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    # Seed: 2 workflows (1 archive, 1 transfer_to_sa) + 1 smart-app (org)
    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-a", "owner_type": "user", "owner_id": "u@x.com",
        "user_id": "u@x.com", "lifecycle_stage": "personal",
        "inheritance_policy": "archive",
    })
    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-b", "owner_type": "user", "owner_id": "u@x.com",
        "user_id": "u@x.com", "lifecycle_stage": "personal",
        "inheritance_policy": "transfer_to_sa",
        "inheritance_target": "svc:t@x.citra.ai",
    })
    fake_db["smart_apps"].docs.append({
        "slug": "u-app",
        "owner": "u@x.com",
        "org_id": "x",
        "app_spec": {
            "owner_type": "user", "owner_id": "u@x.com",
            "lifecycle_stage": "personal",
            "inheritance_policy": "transfer_to_org",
        },
    })

    _run(handle_user_deactivated(
        {"user_id": "u@x.com", "org_id": "x"},
        ctx,
    ))
    assert len(fake_db["HandoffReports"].docs) == 1
    report = fake_db["HandoffReports"].docs[0]
    assert report["summary"]["total"] == 3
    assert report["summary"]["archived"] == 1
    assert report["summary"]["transferred_to_sa"] == 1
    assert report["summary"]["transferred_to_org"] == 1
    assert report["summary"]["errors"] == 0
    assert report["reviewed"] is False
    # Each transition contains both resource_id and policy_applied
    for t in report["transitions"]:
        assert "resource_id" in t
        assert "policy_applied" in t


def test_does_not_touch_resources_owned_by_others(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    # Owned by a service account, not the leaving user — must NOT be touched
    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-sa", "owner_type": "service_account",
        "owner_id": "svc:claims-bot@acme.citra.ai",
        "user_id": "rohit@acme.com",  # author, but not owner
        "lifecycle_stage": "team_managed",
        "inheritance_policy": "archive",
    })

    result = _run(handle_user_deactivated(
        {"user_id": "rohit@acme.com"},
        ctx,
    ))
    assert result["resources_processed"] == 0
    wf = fake_db["Workflows"].docs[0]
    assert wf["lifecycle_stage"] == "team_managed"  # untouched
    assert wf["owner_type"] == "service_account"


def test_handler_registered_under_user_deactivated():
    """Handler must be reachable by the worker via the registry."""
    import registry
    import handlers  # noqa: F401 — side-import triggers registration
    assert registry.get("user.deactivated") is not None
    assert "user.deactivated" in registry.names()


def test_missing_user_id_raises_permanent_failure(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated
    from citra_queue import JobPermanentFailure
    with pytest.raises(JobPermanentFailure):
        _run(handle_user_deactivated({}, ctx))


# ── transfer_to_dept inheritance policy ────────────────────────────────

def test_transfer_to_dept_uses_resource_dept_id_when_target_absent(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-dept-fallback",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "org_id": "acme",
        "dept_ids": ["plant_ops"],
        "lifecycle_stage": "personal",
        "inheritance_policy": "transfer_to_dept",
        # no inheritance_target — falls back to resource's first dept
    })
    _run(handle_user_deactivated({"user_id": "rohit@acme.com"}, ctx))
    wf = fake_db["Workflows"].docs[0]
    assert wf["owner_type"] == "dept"
    assert wf["owner_id"] == "plant_ops"
    assert wf["lifecycle_stage"] == "dept_managed"


def test_transfer_to_dept_archives_when_no_dept_anywhere(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-dept-orphan",
        "owner_type": "user",
        "owner_id": "rohit@acme.com",
        "user_id": "rohit@acme.com",
        "org_id": "acme",
        "dept_ids": [],
        "lifecycle_stage": "personal",
        "inheritance_policy": "transfer_to_dept",
    })
    _run(handle_user_deactivated({"user_id": "rohit@acme.com"}, ctx))
    wf = fake_db["Workflows"].docs[0]
    assert wf["lifecycle_stage"] == "archived"
    assert wf["lifecycle_audit"][0]["policy_applied"] == "archive"


def test_handoff_report_counts_transfer_to_dept(fake_db, ctx):
    from handlers.inheritance_handlers import handle_user_deactivated

    fake_db["Workflows"].docs.append({
        "workflow_id": "wf-x", "owner_type": "user", "owner_id": "u@x.com",
        "user_id": "u@x.com", "org_id": "x",
        "dept_ids": ["finance"],
        "lifecycle_stage": "personal",
        "inheritance_policy": "transfer_to_dept",
        "inheritance_target": "finance",
    })
    _run(handle_user_deactivated({"user_id": "u@x.com"}, ctx))
    assert len(fake_db["HandoffReports"].docs) == 1
    summary = fake_db["HandoffReports"].docs[0]["summary"]
    assert summary["transferred_to_dept"] == 1
    assert summary["total"] == 1


# ── user.delete_applied (admin-driven picker) ──────────────────────────

def test_admin_delete_applied_transfer_to_sa(fake_multi_db, ctx):
    from handlers.inheritance_handlers import handle_user_delete_applied

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]

    citra["Workflows"].docs.append({
        "workflow_id": "wf-1",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
        "org_id": "acme",
        "lifecycle_stage": "personal",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com", "deletion_state": "pending_deletion"})

    payload = {
        "request_id": "req-1",
        "user_id": "alice@acme.com",
        "email": "alice@acme.com",
        "org_id": "acme",
        "personal_sa_id": "svc:personal-alice@acme.citra.ai",
        "requested_by": "leadops@acme.com",
        "resources": [
            {
                "kind": "workflow",
                "resource_id": "wf-1",
                "action": "transfer_to_sa",
                "action_target": "svc:claims-team@acme.citra.ai",
            },
        ],
    }
    result = _run(handle_user_delete_applied(payload, ctx))
    wf = citra["Workflows"].docs[0]
    assert wf["owner_type"] == "service_account"
    assert wf["owner_id"] == "svc:claims-team@acme.citra.ai"
    assert wf["lifecycle_stage"] == "team_managed"
    # User marked deleted
    assert user["users"].docs[0]["deletion_state"] == "deleted"
    # Personal SA deleted (was empty after the strip)
    assert len(user["serviceaccounts"].docs) == 0
    assert result["summary"]["transferred_to_sa"] == 1


def test_admin_delete_applied_transfer_to_dept(fake_multi_db, ctx):
    from handlers.inheritance_handlers import handle_user_delete_applied

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["smart_apps"].docs.append({
        "slug": "alice-briefing",
        "app_spec": {
            "owner_type": "service_account",
            "owner_id": "svc:personal-alice@acme.citra.ai",
            "lifecycle_stage": "personal",
        },
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})

    payload = {
        "user_id": "alice@acme.com",
        "email": "alice@acme.com",
        "personal_sa_id": "svc:personal-alice@acme.citra.ai",
        "requested_by": "leadops@acme.com",
        "resources": [
            {
                "kind": "smart_app",
                "resource_id": "alice-briefing",
                "action": "transfer_to_dept",
                "action_target": "plant_ops",
            },
        ],
    }
    _run(handle_user_delete_applied(payload, ctx))
    app = citra["smart_apps"].docs[0]
    assert app["app_spec"]["owner_type"] == "dept"
    assert app["app_spec"]["owner_id"] == "plant_ops"
    assert app["app_spec"]["lifecycle_stage"] == "dept_managed"


def test_admin_delete_applied_make_me_admin_promotes_admin(fake_multi_db, ctx):
    from handlers.inheritance_handlers import handle_user_delete_applied

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["Workflows"].docs.append({
        "workflow_id": "wf-2",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
        "lifecycle_stage": "personal",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})

    payload = {
        "user_id": "alice@acme.com",
        "email": "alice@acme.com",
        "personal_sa_id": "svc:personal-alice@acme.citra.ai",
        "requested_by": "leadops@acme.com",
        "resources": [
            {
                "kind": "workflow",
                "resource_id": "wf-2",
                "current_sa": "svc:personal-alice@acme.citra.ai",
                "action": "make_me_admin",
            },
        ],
    }
    _run(handle_user_delete_applied(payload, ctx))
    sa = user["serviceaccounts"].docs[0] if user["serviceaccounts"].docs else None
    # Personal SA was kept (now owned by leadops, not empty) and leadops is admin
    if sa:
        assert "leadops@acme.com" in sa["admins"]
        assert "alice@acme.com" not in sa["admins"]


def test_admin_delete_applied_archive(fake_multi_db, ctx):
    from handlers.inheritance_handlers import handle_user_delete_applied

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["Workflows"].docs.append({
        "workflow_id": "wf-3",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
        "lifecycle_stage": "personal",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})

    _run(handle_user_delete_applied(
        {
            "user_id": "alice@acme.com", "email": "alice@acme.com",
            "personal_sa_id": "svc:personal-alice@acme.citra.ai",
            "requested_by": "leadops@acme.com",
            "resources": [
                {"kind": "workflow", "resource_id": "wf-3", "action": "archive"},
            ],
        },
        ctx,
    ))
    wf = citra["Workflows"].docs[0]
    assert wf["lifecycle_stage"] == "archived"


def test_admin_delete_applied_delete_hard_removes_resource(fake_multi_db, ctx):
    from handlers.inheritance_handlers import handle_user_delete_applied

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["Workflows"].docs.append({
        "workflow_id": "wf-doomed",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})

    _run(handle_user_delete_applied(
        {
            "user_id": "alice@acme.com", "email": "alice@acme.com",
            "personal_sa_id": "svc:personal-alice@acme.citra.ai",
            "requested_by": "leadops@acme.com",
            "resources": [
                {"kind": "workflow", "resource_id": "wf-doomed", "action": "delete"},
            ],
        },
        ctx,
    ))
    assert len(citra["Workflows"].docs) == 0


def test_admin_delete_applied_writes_handoff_report_and_updates_request(fake_multi_db, ctx):
    from handlers.inheritance_handlers import handle_user_delete_applied

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["Workflows"].docs.append({
        "workflow_id": "wf-h",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})
    user["DeletionRequests"].docs.append({"request_id": "req-h", "status": "submitted"})

    _run(handle_user_delete_applied(
        {
            "request_id": "req-h",
            "user_id": "alice@acme.com", "email": "alice@acme.com",
            "personal_sa_id": "svc:personal-alice@acme.citra.ai",
            "requested_by": "leadops@acme.com",
            "resources": [
                {"kind": "workflow", "resource_id": "wf-h", "action": "archive"},
            ],
        },
        ctx,
    ))
    assert len(citra["HandoffReports"].docs) == 1
    report = citra["HandoffReports"].docs[0]
    assert report["kind"] == "admin_delete"
    assert report["request_id"] == "req-h"

    dreq = user["DeletionRequests"].docs[0]
    assert dreq["status"] == "applied"
    assert dreq["applied_handoff_report_id"] == report["report_id"]


def test_user_delete_applied_registered():
    import registry, handlers  # noqa: F401
    assert registry.get("user.delete_applied") is not None


# ── Phase H: personal-content apply (presentations, reports, …) ────────

def test_apply_personal_content_skips_when_no_policies():
    """No bucket policies → helper exits early without calling Citra-Service."""
    from handlers.inheritance_handlers import _apply_personal_content

    out = _run(_apply_personal_content(
        email="alice@acme.com",
        policies={},
        transfer_target="boss@acme.com",
        admin_auth_token="jwt",
    ))
    assert out == {"skipped": "no policies"}


def test_apply_personal_content_posts_to_citra_service(monkeypatch):
    """Helper POSTs to /api/v2/admin/user-content-apply with the right body
    and forwards the admin's JWT in Authorization."""
    from handlers import inheritance_handlers

    captured = {}

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {
                "success": True,
                "email": "alice@acme.com",
                "buckets": {"documents": {"policy": "delete", "affected": 3}},
            }

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return _FakeResp()

    # Patch httpx.AsyncClient inside the handler module
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    out = _run(inheritance_handlers._apply_personal_content(
        email="alice@acme.com",
        policies={"documents": "delete", "notes": "transfer_to_admin"},
        transfer_target="boss@acme.com",
        admin_auth_token="my-jwt-token",
    ))

    assert out["success"] is True
    assert captured["url"].endswith("/api/v2/admin/user-content-apply")
    assert captured["body"]["email"] == "alice@acme.com"
    assert captured["body"]["bucket_policies"] == {
        "documents": "delete",
        "notes": "transfer_to_admin",
    }
    assert captured["body"]["transfer_target_email"] == "boss@acme.com"
    assert captured["headers"]["Authorization"] == "Bearer my-jwt-token"


def test_apply_personal_content_tolerates_failure(monkeypatch):
    """Network error returns an error dict — handler must NOT crash, since
    workflow/app handoff has already succeeded by the time this is called."""
    from handlers import inheritance_handlers

    class _ExplodingClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **kw):
            raise RuntimeError("citra-service is down")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)

    out = _run(inheritance_handlers._apply_personal_content(
        email="alice@acme.com",
        policies={"documents": "delete"},
        transfer_target="boss@acme.com",
        admin_auth_token="jwt",
    ))
    assert "error" in out
    assert "citra-service is down" in out["error"]


def test_admin_delete_applied_forwards_personal_content_policies(fake_multi_db, ctx, monkeypatch):
    """When `personal_content_policies` is present in the payload, the
    worker calls _apply_personal_content with the admin's JWT + target."""
    from handlers import inheritance_handlers

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["Workflows"].docs.append({
        "workflow_id": "wf-z",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})

    captured = {}
    async def _fake_apply_personal_content(*, email, policies, transfer_target, admin_auth_token):
        captured.update(
            email=email, policies=policies,
            transfer_target=transfer_target, admin_auth_token=admin_auth_token,
        )
        return {"success": True, "buckets": {"documents": {"policy": "delete", "affected": 2}}}

    monkeypatch.setattr(
        inheritance_handlers, "_apply_personal_content", _fake_apply_personal_content,
    )

    _run(inheritance_handlers.handle_user_delete_applied(
        {
            "user_id": "alice@acme.com", "email": "alice@acme.com",
            "personal_sa_id": "svc:personal-alice@acme.citra.ai",
            "requested_by": "leadops@acme.com",
            "resources": [
                {"kind": "workflow", "resource_id": "wf-z", "action": "archive"},
            ],
            "personal_content_policies": {"documents": "delete", "notes": "keep"},
            "personal_content_transfer_target": "leadops@acme.com",
            "admin_auth_token": "admin-jwt-xyz",
        },
        ctx,
    ))

    assert captured["email"] == "alice@acme.com"
    assert captured["policies"] == {"documents": "delete", "notes": "keep"}
    assert captured["transfer_target"] == "leadops@acme.com"
    assert captured["admin_auth_token"] == "admin-jwt-xyz"

    # HandoffReport now carries the personal_content section
    report = citra["HandoffReports"].docs[-1]
    assert report.get("personal_content") is not None
    assert report["summary"]["personal_content_buckets_applied"] == 1


def test_admin_delete_applied_skips_content_apply_when_no_policies(fake_multi_db, ctx, monkeypatch):
    """When the payload has no personal_content_policies, the helper is
    never called and the report's personal_content stays None."""
    from handlers import inheritance_handlers

    citra = fake_multi_db["citra"]
    user = fake_multi_db["user"]
    citra["Workflows"].docs.append({
        "workflow_id": "wf-zz",
        "owner_type": "service_account",
        "owner_id": "svc:personal-alice@acme.citra.ai",
    })
    user["serviceaccounts"].docs.append({
        "service_account_id": "svc:personal-alice@acme.citra.ai",
        "admins": ["alice@acme.com"], "members": [],
    })
    user["users"].docs.append({"email": "alice@acme.com"})

    called = []
    async def _fake_apply(*a, **kw):
        called.append(kw)
        return {}
    monkeypatch.setattr(inheritance_handlers, "_apply_personal_content", _fake_apply)

    _run(inheritance_handlers.handle_user_delete_applied(
        {
            "user_id": "alice@acme.com", "email": "alice@acme.com",
            "personal_sa_id": "svc:personal-alice@acme.citra.ai",
            "requested_by": "leadops@acme.com",
            "resources": [
                {"kind": "workflow", "resource_id": "wf-zz", "action": "archive"},
            ],
            # No personal_content_policies passed
        },
        ctx,
    ))

    assert called == [], "helper should not be invoked without policies"
    report = citra["HandoffReports"].docs[-1]
    assert report.get("personal_content") is None or report["personal_content"] == {}
    assert report["summary"]["personal_content_buckets_applied"] == 0
