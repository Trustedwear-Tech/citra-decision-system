# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Backfill (§12) + eval gate (§13) — docs/clause-memory-graph-plan.md.

Pins the properties that make the migration and the gate trustworthy:
  * backfill is IDEMPOTENT — a re-run after a partial failure must not
    double-count officer support and defeat the promotion gate;
  * a legacy correction with no recoverable record gets NO facets rather than
    guessed ones (a global clause is over-broad but true);
  * an inferred reason_code is flagged, so it cannot count toward promotion;
  * the eval is LEAVE-ONE-OUT — a clause never scores on the case its own
    evidence produced, or the number is circular and always ~1.0;
  * the gate reports insufficient_data rather than a flattering small sample.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import clause_eval as ev
import clause_store as cs
from backfill_clause_memory import legacy_correction_id

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


# ── deterministic legacy ids ─────────────────────────────────────────────────
def test_legacy_id_is_stable_for_the_same_entry():
    e = {"reason": "needs a police report", "actor": "maria@x",
         "item_id": "run_1", "at": NOW}
    kw = dict(tenant_id="t", app_slug="app", modality="record",
              task_type="decision", entry=e, index=3)
    assert legacy_correction_id(**kw) == legacy_correction_id(**kw)


def test_legacy_id_differs_per_entry_and_position():
    e = {"reason": "x", "actor": "a", "item_id": "r", "at": NOW}
    base = dict(tenant_id="t", app_slug="app", modality="record",
                task_type="decision", entry=e)
    assert legacy_correction_id(**base, index=0) != legacy_correction_id(**base, index=1)
    other = {**e, "actor": "b"}
    assert (legacy_correction_id(**base, index=0)
            != legacy_correction_id(tenant_id="t", app_slug="app", modality="record",
                                    task_type="decision", entry=other, index=0))


# ── relevance / firing helpers ───────────────────────────────────────────────
def test_relevance_matches_on_reason_code_or_contested_field():
    corr = {"reason_code": "evidence_insufficient",
            "contested_fields": ["police_report_no"]}
    assert ev._relevant({"reason_code": "evidence_insufficient"}, corr)
    assert ev._relevant({"reason_code": "other",
                         "contested_fields": ["police_report_no"]}, corr)
    assert not ev._relevant({"reason_code": "amount_incorrect",
                             "contested_fields": ["amount"]}, corr)


def test_fires_is_subset_containment():
    case = ["loss_type:theft", "amount_band:big", "locale:us"]
    assert ev._fires({"scope_facets": ["loss_type:theft"]}, case)
    assert ev._fires({"scope_facets": []}, case)            # global
    assert not ev._fires({"scope_facets": ["loss_type:fire"]}, case)
    assert not ev._fires({"scope_facets": ["loss_type:theft", "vip:true"]}, case)


# ── the eval ─────────────────────────────────────────────────────────────────
class _Col:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *a, **kw):
        docs = self.docs

        class _C:
            def sort(self, *a, **kw):
                return self

            async def to_list(self, n):
                return docs[:n]

        return _C()


def _wire(monkeypatch, corrections, clauses):
    import corrections as cx

    monkeypatch.setattr(cx, "_col", lambda: _Col(corrections))
    monkeypatch.setattr(cs, "_col", lambda: _Col(clauses))


def _corr(i, code="evidence_insufficient", facets=("loss_type:theft",)):
    return {"correction_id": f"c{i}", "reason_code": code,
            "contested_fields": ["police_report_no"], "case_facets": list(facets)}


def _clause(cid, code="evidence_insufficient", scope=("loss_type:theft",), prov=()):
    return {"clause_id": cid, "reason_code": code, "scope_facets": list(scope),
            "scope_size": len(scope), "contested_fields": ["police_report_no"],
            "text": "Require a police report number.", "text_words": 5,
            "support_count": 3, "status": "active", "provenance": list(prov)}


def test_insufficient_holdout_reports_rather_than_flatters(monkeypatch):
    _wire(monkeypatch, [_corr(i) for i in range(5)], [_clause("C-1")])
    res = asyncio.run(ev.evaluate_app(tenant_id="t", app_slug="app"))
    assert res["verdict"] == "insufficient_data"
    assert "coverage" not in res          # no number at all, not a rosy one


def test_leave_one_out_prevents_a_circular_score(monkeypatch):
    """Every clause was derived from ALL the holdout cases. If its own evidence
    is not excluded, coverage is trivially 1.0 and the gate is meaningless."""
    corrs = [_corr(i) for i in range(30)]
    all_ids = [c["correction_id"] for c in corrs]
    clause = _clause("C-1", prov=all_ids)

    res = asyncio.run(_run(monkeypatch, corrs, [clause]))
    # excluded on every case ⇒ nothing fires ⇒ coverage 0, and the gate HOLDS
    assert res["coverage"] == 0.0
    assert res["verdict"] == "hold"


def test_a_genuinely_general_clause_scores(monkeypatch):
    corrs = [_corr(i) for i in range(30)]
    # derived from only the first three cases → eligible for the other 27
    clause = _clause("C-1", prov=["c0", "c1", "c2"])
    res = asyncio.run(_run(monkeypatch, corrs, [clause]))
    assert res["covered"] == 27 and res["coverage"] == pytest.approx(0.9)
    assert res["verdict"] == "pass"


def test_wrong_scope_never_fires(monkeypatch):
    corrs = [_corr(i, facets=("loss_type:theft",)) for i in range(30)]
    clause = _clause("C-1", scope=("loss_type:windshield",))
    res = asyncio.run(_run(monkeypatch, corrs, [clause]))
    assert res["coverage"] == 0.0


def test_right_scope_wrong_lesson_does_not_count(monkeypatch):
    corrs = [_corr(i) for i in range(30)]
    clause = {**_clause("C-1", code="amount_incorrect"),
              "contested_fields": ["amount"]}
    res = asyncio.run(_run(monkeypatch, corrs, [clause]))
    assert res["coverage"] == 0.0        # it fires, but it is the wrong rule


def test_facetless_history_blocks_the_gate(monkeypatch):
    """Backfilled rows carry no facets, so nothing can be scoped or routed for
    them. Passing the gate on such a history would be meaningless."""
    corrs = [_corr(i, facets=()) for i in range(30)]
    res = asyncio.run(_run(monkeypatch, corrs, [_clause("C-1", scope=())]))
    assert res["facetless_cases"] == 30
    assert res["verdict"] == "hold"
    assert any("NO facets" in b for b in res["blockers"])


def test_no_clauses_is_an_explicit_blocker(monkeypatch):
    res = asyncio.run(_run(monkeypatch, [_corr(i) for i in range(30)], []))
    assert res["verdict"] == "hold"
    assert any("no active clauses" in b for b in res["blockers"])


def test_reports_the_real_prompt_cost(monkeypatch):
    """The blob spent ~1000 words on EVERY case regardless of relevance. The
    clause block spends only what actually matched — selection, not
    compression."""
    corrs = [_corr(i) for i in range(30)]
    res = asyncio.run(_run(monkeypatch, corrs, [_clause("C-1", prov=["c0"])]))
    assert res["mean_prompt_words"] < 100


def test_measures_statement_is_not_oversold(monkeypatch):
    res = asyncio.run(_run(monkeypatch, [_corr(i) for i in range(30)],
                           [_clause("C-1", prov=["c0"])]))
    m = res["measures"]
    assert "NOT that the model would have decided differently" in m
    assert "Do not report it as an accuracy number." in m


async def _run(monkeypatch, corrs, clauses):
    _wire(monkeypatch, corrs, clauses)
    return await ev.evaluate_app(tenant_id="t", app_slug="app")
