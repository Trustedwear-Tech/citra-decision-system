"""Memory screen API — endpoint contract tests.

Pins the §Memory-screen access model on the four endpoints:
  * reads (clause list, provenance, ledger browse) = curators only
    (dept-scoped audience), regular users included;
  * writes (retire a clause, exclude-from-retrieval) = curators only
    (dept_admin/org_admin/super_admin/owner-SA admin) — a regular dept user
    gets 403;
  * the governed edit bumps the version and never mutates in place;
  * the exclusion flag stops precedent retrieval without touching the row;
  * bad input fails loud (422 empty summary, 404 unknown bucket/item).

Same in-memory harness as test_spec_versions: _MemCol for the app store,
purpose-built fakes monkeypatched under item_records._col /
clause_store._col / corrections._col.
"""
from __future__ import annotations

import asyncio
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
from tests.test_item_records import FakeCol  # type: ignore  # noqa: E402

JWT_SECRET = "smart-app-service-test-secret"
os.environ["JWT_SECRET"] = JWT_SECRET
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

TENANT = "acme-power"
OWNER_SA = f"work-sa-{TENANT}-ops"
SLUG = "memory-test-app"


def _mint(user_id: str = "officer", roles: list | None = None) -> str:
    return jwt.encode(
        {
            "sub": user_id, "user_id": user_id,
            "email": f"{user_id}@example.com",
            "tenant_id": TENANT, "org_id": TENANT,
            "dept_ids": ["operations"], "roles": roles or [],
            "service_account_admin_of": [], "service_account_member_of": [],
            "iat": int(time.time()), "exp": int(time.time()) + 600,
            "iss": "Citra-AI",
        },
        JWT_SECRET, algorithm="HS256",
    )


def _hdr(roles: list | None = None, user: str = "officer") -> dict:
    return {"Authorization": f"Bearer {_mint(user, roles)}"}


def _app_doc() -> dict:
    return {
        "slug": SLUG, "app_id": "app_mem_1", "tenant_id": TENANT,
        "status": "published",
        "app_spec": {
            "slug": SLUG, "title": "Memory Test App", "headless": True,
            "agent_id": "agent_mem", "tenant_id": TENANT, "org_id": TENANT,
            "audience": "org", "owner_type": "service_account",
            "owner_id": OWNER_SA, "dept_ids": ["operations"],
        },
    }


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

    apps = _MemCol([_app_doc()])
    decisions = _MemCol([{
        "decision_id": "d1", "app_id": "app_mem_1", "tenant_id": TENANT,
        "mode": "human_approved", "recommendation": "repair",
        "created_at": "2026-07-01T00:00:00+00:00",
    }])
    monkeypatch.setattr(_main, "_apps_col", apps, raising=False)
    monkeypatch.setattr(_main, "_decision_records_col", decisions, raising=False)
    monkeypatch.setattr(_main, "_agents_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_spec_versions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_build_sessions_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_prompt_packs_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_skills_col", _MemCol(), raising=False)
    monkeypatch.setattr(_main, "_pending_runs_col", _MemCol(), raising=False)

    # Memory stores: the endpoints reach them via the lazy _col() accessors.
    import clause_store as _cs
    import corrections as _cx
    import item_records as _ir

    ledger = FakeCol()

    class _ListCol:
        """Minimal find()-only stand-in for the clause / correction stores."""

        def __init__(self, name, docs):
            self.name = name
            self.docs = docs

        async def create_index(self, *a, **kw):
            return None

        def find(self, q, proj=None):
            docs = [dict(d) for d in self.docs]

            class _C:
                def sort(self, *a, **kw):
                    return self

                async def to_list(self, n):
                    return docs[:n]

            return _C()

        def aggregate(self, *a, **kw):
            class _C:
                async def to_list(self, n):
                    return []

            return _C()

    clauses = _ListCol("smartapp_clauses", [{
        "clause_id": "C-001", "tenant_id": TENANT, "app_slug": SLUG,
        "modality": "record", "task_type": "decision", "status": "active",
        "text": "Prefer repair when defects are contained.",
        "scope_facets": [], "scope_size": 0, "reason_code": "severity_wrong",
        "contested_fields": [], "provenance": ["corr-1"],
        "support_officers": ["a@x", "b@x", "c@x"], "support_count": 3,
        "dissent_count": 0, "fired_count": 4, "blamed_count": 0,
        "precision": None, "version": 1,
    }])
    corrections = _ListCol("smartapp_corrections", [{
        "correction_id": "corr-1", "tenant_id": TENANT, "app_slug": SLUG,
        "modality": "record", "task_type": "decision",
        "reason_text": "no fail on breather color alone", "officer": "a@x",
    }])
    monkeypatch.setattr(_ir, "_col", lambda: ledger)
    _ir._indexes_ensured.discard(FakeCol.name)
    monkeypatch.setattr(_cs, "_col", lambda: clauses)
    monkeypatch.setattr(_cs, "_indexes_ensured", set())
    monkeypatch.setattr(_cx, "_col", lambda: corrections)
    monkeypatch.setattr(_cx, "_indexes_ensured", set())

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    monkeypatch.setattr(_main.app.router, "lifespan_context", _noop_lifespan)

    with TestClient(_main.app) as c:
        c._ledger = ledger          # type: ignore[attr-defined]
        c._clauses = clauses        # type: ignore[attr-defined]
        yield c


def _seed_ledger(client: TestClient, item_id: str = "INS-1-photo") -> None:
    import item_records as ir

    asyncio.run(ir.persist_item_findings(
        [{"item_id": item_id, "item_type": "defect-photo", "modality": "image",
          "subject": "defect close-up", "media_ref": f"ref-{item_id}",
          "content_sha256": "aaa", "fields": {}, "recommendation": "Fail",
          "confidence": 0.9, "rationale": "crack", "rubric_version": "v1"}],
        correlation_id="run-1", slug=SLUG, app_id="app_mem_1", tenant_id=TENANT))


# ── reads: dept-scoped, regular users included ───────────────────────────────
def test_clauses_read_is_curator_only(client: TestClient):
    """The clause list replaces the rubrics tab — same gating."""
    r = client.get(f"/apps/{SLUG}/memory/clauses", headers=_hdr())
    assert r.status_code == 403

    r = client.get(f"/apps/{SLUG}/memory/clauses",
                   headers=_hdr(roles=["org_admin"]))
    assert r.status_code == 200
    body = r.json()
    assert body["clauses"][0]["clause_id"] == "C-001"
    assert body["clauses"][0]["support_count"] == 3


def test_clause_provenance_returns_the_rejects_that_taught_it(client: TestClient):
    """The accountability the blob could never offer: 'why does it say that?'
    answered with the actual past cases, not an untraceable paragraph."""
    r = client.get(f"/apps/{SLUG}/memory/clauses/C-001/provenance",
                   headers=_hdr(roles=["org_admin"]))
    assert r.status_code == 200
    body = r.json()
    assert body["clause"]["clause_id"] == "C-001"
    assert body["corrections"][0]["reason_text"].startswith("no fail on breather")


def test_unknown_clause_is_404(client: TestClient):
    assert client.get(f"/apps/{SLUG}/memory/clauses/C-999/provenance",
                      headers=_hdr(roles=["org_admin"])).status_code == 404


def test_items_browse_is_curator_only(client: TestClient):
    _seed_ledger(client)
    assert client.get(f"/apps/{SLUG}/memory/items",
                      headers=_hdr()).status_code == 403
    r = client.get(f"/apps/{SLUG}/memory/items",
                   headers=_hdr(["dept_admin"], user="deptadmin"))
    assert r.status_code == 200
    body = r.json()
    assert [i["item_id"] for i in body["items"]] == ["INS-1-photo"]
    assert body["next_cursor"] is None  # short page = end, no cursor


def test_unknown_app_is_404(client: TestClient):
    assert client.get("/apps/nope/memory/clauses",
                      headers=_hdr(["org_admin"])).status_code == 404


# ── writes: curator-gated ────────────────────────────────────────────────────
def test_exclusion_stops_retrieval_keeps_row(client: TestClient):
    import item_records as ir

    _seed_ledger(client)
    r = client.post(f"/apps/{SLUG}/memory/items/INS-1-photo/exclusion",
                    headers=_hdr(["dept_admin"], user="deptadmin"),
                    json={"excluded": True})
    assert r.status_code == 200 and r.json()["rows"] == 1
    row = client._ledger.docs[0]
    assert row["retrieval_excluded"] is True
    assert row["retrieval_excluded_by"] == "deptadmin"
    assert row["recommendation"] == "Fail"      # record untouched
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id=TENANT, slug=SLUG, modality="image",
        task_type="defect-photo", content_sha256="aaa"))
    assert prec["exact"] == []                   # never grounds a prompt again

    # unknown item = loud 404, not silent success
    r = client.post(f"/apps/{SLUG}/memory/items/ghost/exclusion",
                    headers=_hdr(["dept_admin"]), json={"excluded": True})
    assert r.status_code == 404


# ── scheduled export trigger ─────────────────────────────────────────────────
def test_export_run_requires_curator(client: TestClient):
    r = client.post(f"/apps/{SLUG}/memory/export/run", headers=_hdr())
    assert r.status_code == 403


def test_export_run_fails_loud_without_bucket(client: TestClient, monkeypatch):
    # no MEMORY_EXPORT_BUCKET configured → 400, never a silent no-op
    monkeypatch.delenv("MEMORY_EXPORT_BUCKET", raising=False)
    r = client.post(f"/apps/{SLUG}/memory/export/run",
                    headers=_hdr(["org_admin"], user="admin"))
    assert r.status_code == 400
    assert "bucket" in (r.json().get("detail") or "").lower()


def test_export_run_rejects_bad_mode(client: TestClient):
    r = client.post(f"/apps/{SLUG}/memory/export/run?mode=nonsense",
                    headers=_hdr(["org_admin"], user="admin"))
    assert r.status_code == 422


# ── token usage (billing view) ───────────────────────────────────────────────
def test_usage_requires_org_admin(client: TestClient):
    # a regular user cannot see the org's token bill
    assert client.get("/usage", headers=_hdr()).status_code == 403
    # dept_admin is not enough either — billing is org-level
    assert client.get("/usage", headers=_hdr(["dept_admin"])).status_code == 403


def test_usage_returns_summary_for_org_admin(client: TestClient, monkeypatch):
    import token_metering as _tm

    async def _fake_summary(**kw):
        assert kw["tenant_ids"] == [TENANT]     # scoped to the caller's org
        return {"totals": {"tokens_in": 300, "tokens_out": 75, "calls": 3},
                "by_model": [{"model": "glm-4", "tokens_in": 300,
                              "tokens_out": 75, "calls": 3}],
                "by_surface": [], "by_day": []}

    monkeypatch.setattr(_tm, "usage_summary", _fake_summary)
    r = client.get("/usage?days=7", headers=_hdr(["org_admin"], user="admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["org_id"] == TENANT and body["window_days"] == 7
    assert body["totals"]["tokens_in"] == 300


# ── env visibility: app-detail carries the environment ───────────────────────
def test_app_detail_exposes_environment(client: TestClient):
    r = client.get(f"/apps/{SLUG}", headers=_hdr())
    assert r.status_code == 200
    # field is wired end-to-end through AppDetailResponse; prod is the default
    # store in the unit harness (no test plane configured).
    assert r.json()["environment"] == "prod"


# ── manual export (admin action) ─────────────────────────────────────────────
def test_export_requires_curator(client: TestClient):
    r = client.get(f"/apps/{SLUG}/memory/export", headers=_hdr())
    assert r.status_code == 403


def test_export_returns_open_schema_document(client: TestClient):
    _seed_ledger(client)
    r = client.get(f"/apps/{SLUG}/memory/export",
                   headers=_hdr(["org_admin"], user="admin"))
    assert r.status_code == 200
    doc = r.json()
    assert doc["schema"] == "citra.memory.export/v1"
    assert doc["app"]["slug"] == SLUG and doc["exported_by"] == "admin"
    # all three collections present with matching counts
    assert doc["counts"]["decision_records"] == 1
    assert doc["counts"]["item_decision_records"] == 1
    assert doc["counts"]["smartapp_clauses"] == 1
    assert doc["counts"]["smartapp_corrections"] == 1
    assert doc["collections"]["decision_records"][0]["recommendation"] == "repair"
    assert doc["collections"]["item_decision_records"][0]["item_id"] == "INS-1-photo"
    assert doc["collections"]["smartapp_clauses"][0]["clause_id"] == "C-001"
    # the EVIDENCE ships with the rules — conclusions alone would be
    # unauditable, the exact opacity the blob had
    assert doc["collections"]["smartapp_corrections"][0]["correction_id"] == "corr-1"
    # a clean export is explicitly NOT partial
    assert doc["partial"] is False and doc["errors"] == {}
    # no Mongo _id leaks into the export
    for coll in doc["collections"].values():
        for row in coll:
            assert "_id" not in row


def test_export_marks_partial_on_collection_failure(client: TestClient, monkeypatch):
    # if one collection fetch fails, the export must flag partial + name the
    # failure — never a whole-looking JSON that silently dropped a collection.
    _seed_ledger(client)
    import item_records as _ir

    async def _boom(**kw):
        raise RuntimeError("ledger store down")

    monkeypatch.setattr(_ir, "export_ledger", _boom)
    r = client.get(f"/apps/{SLUG}/memory/export",
                   headers=_hdr(["org_admin"], user="admin"))
    assert r.status_code == 200                      # still 200, not a 500
    doc = r.json()
    assert doc["partial"] is True
    assert "item_decision_records" in doc["errors"]
    assert doc["counts"]["item_decision_records"] == 0   # honest empty
    assert doc["counts"]["decision_records"] == 1        # others unaffected


# ── org memory impact (App Memory card subtitle, plan §19.2) ─────────────────
def test_memory_impact_requires_an_admin_role(client: TestClient):
    assert client.get("/org/memory-impact", headers=_hdr()).status_code == 403


def test_memory_impact_reports_the_asset_and_suppresses_a_thin_lift(client: TestClient):
    """The lift must be SUPPRESSED, not fudged, when either cohort is
    under-powered. This is the card someone screenshots — an unearned number on
    it is worse than a blank."""
    r = client.get("/org/memory-impact", headers=_hdr(roles=["org_admin"]))
    assert r.status_code == 200
    d = r.json()
    assert d["clauses_active"] == 1          # the seeded active clause
    assert d["corrections"] == 1
    # No decision records in this fixture ⇒ both cohorts empty ⇒ no number.
    assert d["lift"] is None
    assert "need" in (d["lift_note"] or "")


def test_memory_impact_counts_only_what_is_blocked_on_a_human(
    client: TestClient, monkeypatch,
):
    """The App Memory badge. sop_conflict and dissented are the only states
    waiting on a person — both are inert until somebody rules on them, and
    neither surfaces anywhere outside the Memory screen. QUARANTINED must NOT
    count: an admin already decided that, so nobody is waiting."""
    import clause_store as cs

    docs = [
        {"status": "active", "app_slug": SLUG},
        {"status": "sop_conflict", "app_slug": SLUG},
        {"status": "dissented", "app_slug": "other-app"},
        {"status": "quarantined", "app_slug": "held-app"},
        {"status": "retired", "app_slug": "held-app"},
    ]

    class _Clauses:
        def find(self, q, proj=None):
            class _Cursor:
                async def to_list(self, n):
                    return [dict(d) for d in docs]

            return _Cursor()

    monkeypatch.setattr(cs, "_col", lambda: _Clauses())
    d = client.get("/org/memory-impact", headers=_hdr(roles=["org_admin"])).json()
    assert d["needs_attention"] == 2
    assert d["sop_conflict"] == 1
    assert d["dissented"] == 1
    # Tells the admin WHERE to look; the quarantined/retired app is not listed.
    assert d["attention_apps"] == [SLUG, "other-app"]


def test_memory_impact_survives_a_broken_store(client: TestClient, monkeypatch):
    """Card enrichment must never 500 the admin screen."""
    import clause_store as cs

    def _boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(cs, "_col", _boom)
    r = client.get("/org/memory-impact", headers=_hdr(roles=["org_admin"]))
    assert r.status_code == 200
    d = r.json()
    assert d["clauses_active"] == 0    # zero, not an exception
    # ...but an unread store is NOT an all-clear: the badge must stay dark
    # rather than claim nothing needs attention.
    assert d["needs_attention"] is None
