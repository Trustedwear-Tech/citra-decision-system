"""Record-level decision feedback → clause memory.

The single-summary rubric this file used to cover is DELETED (no `summary`, no
`_resummarize`, no governed-edit surface, collection dropped). What survives is
the part that was never about the blob:

  * ONE canonical bucket key (the APP's own org) used by fold AND read — if
    these ever disagree the loop silently never closes;
  * a missing tenant SKIPS loudly, never a synthesized '' bucket;
  * fold shapes: reject reason; override delta (+why); clean approve = no-op;
  * malformed override events never raise (fully non-raising fold);
  * the run-prompt prefetch renders a clause block and never raises.

Blob-specific coverage (summary budgets, version bumps, edit history, the
corrections tail view) is gone with the blob it described.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import analysis_rubrics as ar
import corrections as cx
from runtime import _prefetch_decision_clauses


@pytest.fixture
def recorded(monkeypatch):
    """Capture what reaches the evidence ledger."""
    rows = []

    async def _rec(**kw):
        rows.append(kw)
        return "corr-1"

    monkeypatch.setattr(cx, "record_correction", _rec)
    return rows


# ── the canonical bucket key ─────────────────────────────────────────────────
def test_rubric_tenant_prefers_app_org():
    doc = {"app_spec": {"org_id": "acme-power", "tenant_id": "legacy"},
           "tenant_id": "docten"}
    assert ar.rubric_tenant_for_app(doc) == "acme-power"
    assert ar.rubric_tenant_for_app({"app_spec": {}, "tenant_id": "docten"}) == "docten"
    assert ar.rubric_tenant_for_app(SimpleNamespace(org_id=None, tenant_id="t2")) == "t2"
    assert ar.rubric_tenant_for_app({"app_spec": {}}) is None
    assert ar.rubric_tenant_for_app(None) is None


def test_missing_tenant_skips_loudly_never_empty_bucket(recorded):
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id=None, app_slug="app", reason="a real lesson"))
    assert ok is False and recorded == []


# ── fold shapes ──────────────────────────────────────────────────────────────
def test_reject_reason_folds(recorded):
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="officer@x", correlation_id="c1",
        reason="fail needs active-failure evidence, not just HIGH severity",
        reason_code="evidence_insufficient",
        recommendation="fail — isolate asset"))
    assert ok is True
    ev = recorded[0]
    assert ev["event"] == "reject"
    assert ev["modality"] == "record" and ev["task_type"] == "decision"
    assert ev["reason_text"].startswith("fail needs active-failure evidence")
    assert ev["recommendation"] == "fail — isolate asset"


def test_override_delta_folds_with_reason(recorded):
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o", correlation_id="c2",
        reason="only the breather is discolored — no active leak",
        overrides=[{"override": {"status": {"from": "fail", "to": "repair"}}}],
        recommendation="fail"))
    assert ok is True
    ev = recorded[0]
    assert ev["event"] == "override"
    # the contested field is DERIVED from the delta — never typed by the officer
    assert ev["contested_fields"] == ["status"]


def test_clean_approve_folds_nothing(recorded):
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o", reason=None, overrides=[]))
    assert ok is False and recorded == []


def test_malformed_override_never_raises(recorded):
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o", reason="still a lesson",
        overrides=[{"override": "not-a-dict"}, None, {}]))
    assert ok is True and recorded[0]["event"] == "reject"


def test_broken_store_never_raises(monkeypatch):
    """The officer's decision has already committed; a learning-store failure
    must never surface as a failed approve."""
    async def _boom(**kw):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(cx, "record_correction", _boom)
    assert asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", reason="x")) is False


# ── the run-prompt prefetch ──────────────────────────────────────────────────
def _app(**kw):
    base = dict(slug="app", org_id="acme-power", tenant_id="jwt-org",
                dataset_directory=[], case_signature=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_prefetch_uses_the_app_anchored_key(monkeypatch):
    seen = {}

    async def _select(**kw):
        seen.update(kw)
        return "BLOCK", ["C-001"]

    import clause_store as cs

    monkeypatch.setattr(cs, "select_clauses", _select)
    block, ids, facets, meta = asyncio.run(_prefetch_decision_clauses(_app(), {}))
    # the APP's org, never the JWT org — fold and read must agree
    assert seen["tenant_id"] == "acme-power"
    assert seen["modality"] == "record" and seen["task_type"] == "decision"
    assert block == "BLOCK" and ids == ["C-001"] and facets == []


def test_prefetch_skips_an_app_without_org(monkeypatch):
    import clause_store as cs

    async def _never(**kw):
        raise AssertionError("must not query without a tenant key")

    monkeypatch.setattr(cs, "select_clauses", _never)
    assert asyncio.run(_prefetch_decision_clauses(
        _app(org_id=None, tenant_id=None), {})) == ("", [], [], {})


def test_prefetch_never_raises(monkeypatch):
    import clause_store as cs

    async def _boom(**kw):
        raise RuntimeError("clause store down")

    monkeypatch.setattr(cs, "select_clauses", _boom)
    assert asyncio.run(_prefetch_decision_clauses(_app(), {})) == ("", [], [], {})


def test_prefetch_works_without_a_case_signature(monkeypatch):
    """No signature is not exclusion — the app still gets clauses, they just
    carry no facet scope. Declaring a signature buys SCOPING, not membership."""
    import clause_store as cs

    async def _select(**kw):
        assert kw["case_facets"] == []
        return "BLOCK", ["C-9"]

    monkeypatch.setattr(cs, "select_clauses", _select)
    block, ids, facets, meta = asyncio.run(_prefetch_decision_clauses(_app(), {"a": 1}))
    assert block == "BLOCK" and ids == ["C-9"] and facets == []
