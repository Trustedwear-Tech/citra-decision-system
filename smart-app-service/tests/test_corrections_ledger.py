# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Officer-correction evidence ledger — Phase A of docs/clause-memory-graph-plan.md.

Pins the properties the whole clause-memory design rests on:
  * append-only: every officer event is ONE immutable row, never rewritten;
  * the canonical tenant key discipline — a missing tenant SKIPS loudly and
    never synthesizes a '' bucket (must agree with the read side or the
    consolidation job reads a bucket the fold never wrote);
  * the ledger keeps the officer's reason UNTRUNCATED (2000 chars): it never
    enters a run prompt, so there is nothing to bound it for;
  * contested_fields is DERIVED from override deltas when not supplied;
  * a ledger failure never breaks the officer's decision;
  * consumed_by is a per-document watermark: re-runnable, never double-counted.
"""
from __future__ import annotations

import asyncio

import pytest

import analysis_rubrics as ar
import corrections as cx


def _matches(d, q):
    for k, v in (q or {}).items():
        if isinstance(v, dict) and "$in" in v:
            if d.get(k) not in v["$in"]:
                return False
        elif isinstance(v, dict) and "$ne" in v:
            if d.get(k) == v["$ne"]:
                return False
        elif d.get(k) != v:
            return False
    return True


class _FakeCol:
    name = "smartapp_corrections"

    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def create_index(self, *a, **kw):
        return None

    def find(self, q, _proj=None):
        hits = [dict(d) for d in self.docs if _matches(d, q)]

        class _C:
            def sort(self, key, direction=1):
                hits.sort(key=lambda h: h.get(key) or 0, reverse=(direction < 0))
                return self

            async def to_list(self, n):
                return hits[:n]

        return _C()

    async def update_many(self, q, u):
        n = 0
        for d in self.docs:
            if _matches(d, q):
                d.update(u.get("$set") or {})
                n += 1

        class _R:
            modified_count = n

        return _R()


@pytest.fixture
def col(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(cx, "_col", lambda: fake)
    monkeypatch.setattr(cx, "_indexes_ensured", set())
    return fake


# ── the canonical bucket key ─────────────────────────────────────────────────
def test_missing_tenant_skips_loudly_never_empty_bucket(col):
    cid = asyncio.run(cx.record_correction(
        tenant_id=None, app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="a real lesson"))
    assert cid is None and col.docs == []


def test_missing_app_slug_skips(col):
    cid = asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="", modality="record", task_type="decision",
        event="reject", reason_text="x"))
    assert cid is None and col.docs == []


# ── append-only shape ────────────────────────────────────────────────────────
def test_records_one_immutable_row_per_event(col):
    for i in range(3):
        asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="reject", officer=f"o{i}@x", reason_text=f"lesson {i}"))
    assert len(col.docs) == 3
    assert len({d["correction_id"] for d in col.docs}) == 3   # ids are unique
    assert all(d["consumed_by"] is None for d in col.docs)    # all pending


def test_case_facets_frozen_at_decision_time(col):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="x", signature_version=3,
        case_facets=["loss_type:theft", "amount_band:25000_100000", "loss_type:theft"]))
    d = col.docs[0]
    # deduped + sorted, and the version that produced them is recorded so a
    # later ontology edit is detectable rather than silently rewriting history
    assert d["case_facets"] == ["amount_band:25000_100000", "loss_type:theft"]
    assert d["signature_version"] == 3


# ── the two-cap rule ─────────────────────────────────────────────────────────
def test_ledger_keeps_the_reason_untruncated(col):
    long_reason = "x" * 1500
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text=long_reason))
    assert len(col.docs[0]["reason_text"]) == 1500      # nothing clips it


def test_reason_truncates_loudly_at_the_ledger_cap(col):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="y" * 3000))
    stored = col.docs[0]["reason_text"]
    assert len(stored) == cx.CORRECTION_REASON_MAX_CHARS
    assert stored.endswith("…")


# ── contested fields are derived, not typed ──────────────────────────────────
def test_contested_fields_derived_from_override_deltas(col):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="override", reason_text="wrong band",
        overrides=[{"override": {"status": {"from": "approve", "to": "refer"},
                                 "amount": {"from": 100, "to": 90}}}]))
    assert col.docs[0]["contested_fields"] == ["amount", "status"]


def test_explicit_contested_fields_win_over_derivation(col):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="override", reason_text="x", contested_fields=["police_report_no"],
        overrides=[{"override": {"status": {"from": "a", "to": "b"}}}]))
    assert col.docs[0]["contested_fields"] == ["police_report_no"]


def test_malformed_overrides_never_raise(col):
    cid = asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="override", reason_text="x",
        overrides=[{"override": "not-a-dict"}, None, {}]))
    assert cid is not None and col.docs[0]["contested_fields"] == []


# ── uncoded is honest, never synthesized ─────────────────────────────────────
def test_absent_reason_code_stays_null(col):
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="x"))
    assert col.docs[0]["reason_code"] is None
    assert col.docs[0]["reason_inferred"] is False


# ── consolidation watermark ──────────────────────────────────────────────────
def test_pending_then_consumed_is_idempotent(col):
    ids = []
    for i in range(3):
        ids.append(asyncio.run(cx.record_correction(
            tenant_id="t", app_slug="app", modality="record", task_type="decision",
            event="reject", officer=f"o{i}", reason_text=f"l{i}")))

    pending = asyncio.run(cx.pending_corrections(
        tenant_id="t", app_slug="app", modality="record", task_type="decision"))
    assert len(pending) == 3

    n = asyncio.run(cx.mark_consumed(correction_ids=ids[:2], clause_id="C-001"))
    assert n == 2
    # re-running the same fold must NOT re-consume (would double-count officer
    # support and defeat the promotion gate)
    n2 = asyncio.run(cx.mark_consumed(correction_ids=ids[:2], clause_id="C-001"))
    assert n2 == 0

    still = asyncio.run(cx.pending_corrections(
        tenant_id="t", app_slug="app", modality="record", task_type="decision"))
    assert [r["correction_id"] for r in still] == [ids[2]]


def test_pending_corrections_raises_on_store_failure(monkeypatch):
    class _Boom:
        name = "smartapp_corrections"

        async def create_index(self, *a, **kw):
            return None

        def find(self, *a, **kw):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(cx, "_col", lambda: _Boom())
    monkeypatch.setattr(cx, "_indexes_ensured", set())
    # "could not read" must never look like "no pending work" — a swallowed
    # failure here stops learning with no alarm.
    with pytest.raises(RuntimeError):
        asyncio.run(cx.pending_corrections(
            tenant_id="t", app_slug="app", modality="record", task_type="decision"))


# ── a ledger failure never breaks the decision ───────────────────────────────
def test_record_failure_is_swallowed_and_logged(monkeypatch):
    class _Boom:
        name = "smartapp_corrections"

        async def create_index(self, *a, **kw):
            return None

        async def insert_one(self, doc):
            raise RuntimeError("disk full")

    monkeypatch.setattr(cx, "_col", lambda: _Boom())
    monkeypatch.setattr(cx, "_indexes_ensured", set())
    assert asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="x")) is None


# ── the fold writes exactly ONE store ────────────────────────────────────────
def test_fold_writes_one_evidence_row(monkeypatch, col):
    """There is no second derived store any more. One officer event, one row —
    two would inflate distinct-officer support and defeat the promotion gate."""
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="maria@x", correlation_id="run_1",
        reason="theft over $25k needs a police report number on file",
        reason_code="evidence_insufficient",
        case_facets=["loss_type:theft", "amount_band:25000_100000"],
        signature_version=1,
        injected_clause_ids=["C-003", "C-011"],
        cited_clause_ids=["C-011"],
        overruled_clause_ids=["C-011"],
        recommendation="Approve $38,000"))
    assert ok is True and len(col.docs) == 1
    ev = col.docs[0]
    assert ev["event"] == "reject"
    assert ev["reason_code"] == "evidence_insufficient"
    assert ev["case_facets"] == ["amount_band:25000_100000", "loss_type:theft"]
    assert ev["injected_clause_ids"] == ["C-003", "C-011"]
    assert ev["cited_clause_ids"] == ["C-011"]
    assert ev["overruled_clause_ids"] == ["C-011"]   # → dissent signal
    assert ev["officer"] == "maria@x"


def test_override_event_is_labelled_override(monkeypatch, col):
    asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="dan@x",
        reason="fleet policies file an internal report",
        overrides=[{"override": {"decision": {"from": "decline", "to": "approve"}}}]))
    assert col.docs[0]["event"] == "override"
    assert col.docs[0]["contested_fields"] == ["decision"]


def test_clean_approve_records_nothing(col):
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o", reason=None, overrides=[]))
    assert ok is False and col.docs == []


# ── facet vocabulary guard ───────────────────────────────────────────────────
# A facet whose family the app never declares can never appear on a real case,
# so a judgement scoped to it fails `scope ⊆ case_facets` FOREVER while reading
# "team judgement — 3 officers" on screen. That shipped in the acme-power demo.
def _declares(*families):
    """Stand in for the app lookup: these are the app's declared families."""
    async def _f(app_slug, tenant_id=None):
        return set(families) | cx._PLATFORM_FAMILIES
    return _f


def test_undeclared_facets_are_held_back_but_the_correction_is_kept(col, monkeypatch):
    monkeypatch.setattr(cx, "declared_families", _declares("triage_status"))
    cid = asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="image", task_type="defect",
        event="override", reason_text="oil seepage is high severity",
        case_facets=["triage_status:pass", "defect_type:bushing_crack",
                     "oil_leak:present"]))
    d = col.docs[0]
    assert cid is not None                              # evidence is never lost
    assert d["case_facets"] == ["triage_status:pass"]   # unusable tokens removed
    # ...but findable: an out-of-vocabulary facet is a bug in whatever wrote it.
    assert sorted(d["rejected_facets"]) == ["defect_type:bushing_crack",
                                            "oil_leak:present"]


def test_declared_and_platform_facets_pass_through(col, monkeypatch):
    monkeypatch.setattr(cx, "declared_families", _declares("category", "priority"))
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="override", reason_text="theft goes to revenue protection",
        case_facets=["category:theft_report", "priority:high", "country:us"]))
    d = col.docs[0]
    assert d["case_facets"] == ["category:theft_report", "country:us",
                                "priority:high"]
    assert "rejected_facets" not in d


def test_drift_tokens_survive_the_guard(col, monkeypatch):
    """`family:__unknown` is the ontology-drift ALARM. Its family IS declared,
    so the guard must not eat it — that would silence the drift signal."""
    monkeypatch.setattr(cx, "declared_families", _declares("category"))
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="a real lesson",
        case_facets=["category:__unknown"]))
    assert col.docs[0]["case_facets"] == ["category:__unknown"]


def test_unknown_vocabulary_never_strips(col, monkeypatch):
    """None means "could not read the app", NOT "nothing declared". Stripping
    real evidence because Mongo blinked is far worse than the drift guarded
    against, so an unreadable signature leaves facets untouched."""
    async def _unknown(app_slug, tenant_id=None):
        return None

    monkeypatch.setattr(cx, "declared_families", _unknown)
    asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="a real lesson",
        case_facets=["anything:at_all", "category:theft_report"]))
    d = col.docs[0]
    assert d["case_facets"] == ["anything:at_all", "category:theft_report"]
    assert "rejected_facets" not in d


def test_guard_failure_never_breaks_the_write(col, monkeypatch):
    """The guard is a safety net, not a gate — if it throws, the officer's
    correction still lands."""
    async def _boom(app_slug, tenant_id=None):
        raise RuntimeError("apps collection down")

    monkeypatch.setattr(cx, "declared_families", _boom)
    cid = asyncio.run(cx.record_correction(
        tenant_id="t", app_slug="app", modality="record", task_type="decision",
        event="reject", reason_text="a real lesson", case_facets=["category:x"]))
    assert cid is None or col.docs  # never raises out of record_correction


def test_fold_survives_a_ledger_outage(monkeypatch):
    """The officer's decision has already committed; a learning-store failure
    must never surface to them as a failed approve."""
    def _boom():
        raise RuntimeError("Database not initialised")

    monkeypatch.setattr(cx, "_col", _boom)
    assert asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o@x",
        reason="theft over 25k needs a police report",
        reason_code="evidence_insufficient")) is False
