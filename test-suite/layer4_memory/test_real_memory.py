# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Layer 4 — the REAL memory implementations, tested in isolation.

test_learning_fixtures.py pins the learning invariants against a reference
scorer. This file drives the ACTUAL smart-app-service memory modules behind
fakes — the same isolate-behind-proxies doctrine as every other layer:

  grounding_refresh.loop_decision_to_sample   the real few-shot builder:
      good+approved -> positive sample; officer override -> the correction is
      encoded contrastively; bad outcome -> ANTI-PATTERN ("AVOID:") sample;
      unsettled outcome -> NOT anti-patterned (no premature down-weighting).
  item_records                                 the multimodal item ledger:
      exact-hash precedent grounding (record binding for artifacts), BOTH
      officer classes captured, case outcome inherited by items.
  analysis_rubrics.append_correction           the rubric (SOP) layer:
      a reject reason becomes a durable correction; an EMPTY reason is a
      no-op — nothing shifts the rubric without a real signal.

Emits (signal, outcome_class) cells via _emit; conftest folds into memory.json.
Requires the smart-app-service modules to be importable (pydantic +
pydantic-settings); skips cleanly where they aren't.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SAS = Path(__file__).resolve().parents[2] / "smart-app-service"
sys.path.insert(0, str(SAS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _memcells import emit_memory_cell  # noqa: E402

gr = pytest.importorskip("grounding_refresh", reason="smart-app-service not importable")
ir = pytest.importorskip("item_records")
ar = pytest.importorskip("analysis_rubrics")
models = pytest.importorskip("models")


# ── grounding_refresh: the real few-shot builder ─────────────────────────────
CONTRACT = models.GroundingContract.model_validate({
    "source_id": "field_operations",
    "dataset_id": "inspections",
    "source_kind": "sql",
    "source_id_field": "asset",
    "input_fields": ["asset", "defect"],
    "output_fields": ["outcome"],
    "decision_field": "outcome",
})


def _rec(outcome_label=None, override=None, decision="Fail"):
    rec = {
        "decision_id": "d1",
        "context": {"asset": "A1", "defect": "crack"},
        "recommendation": {"decision": decision, "reasoning": "photo shows a crack",
                           "planned_writes": [{"payload": {"outcome": decision}}]},
        "record_keys": [{"payload": {"outcome": decision}}],
        "overrides": [],
        "mode": "human_approved",
    }
    if outcome_label:
        rec["outcome"] = {"label": outcome_label}
    if override:
        rec["overrides"] = [{"override": override}]
    return rec


def test_good_outcome_becomes_positive_few_shot():
    s = gr.loop_decision_to_sample(_rec(outcome_label="good"), CONTRACT)
    assert s and s["polarity"] == "good"
    assert not s["decision"].startswith("AVOID")
    assert s["input"] == {"asset": "A1", "defect": "crack"}
    emit_memory_cell("few_shot_grounding", "good")


def test_bad_outcome_becomes_anti_pattern():
    s = gr.loop_decision_to_sample(_rec(outcome_label="bad"), CONTRACT)
    assert s and s["polarity"] == "bad"
    assert s["decision"].startswith("AVOID:")
    assert "ANTI-PATTERN" in (s["reasoning"] or "")
    emit_memory_cell("few_shot_grounding", "bad")
    emit_memory_cell("outcome_downweight", "bad")


def test_unsettled_outcome_is_not_prematurely_downweighted():
    # truth is slow — a record with NO outcome yet must not be anti-patterned
    s = gr.loop_decision_to_sample(_rec(outcome_label=None), CONTRACT)
    assert s and s["polarity"] == "good" and not s["decision"].startswith("AVOID")
    emit_memory_cell("outcome_downweight", "neutral")


def test_officer_override_encoded_as_correction():
    s = gr.loop_decision_to_sample(
        _rec(outcome_label="good", override={"outcome": {"from": "Fail", "to": "Repair"}}),
        CONTRACT)
    assert s and s["from_override"] is True
    assert "CORRECTED" in s["reasoning"] and "'Fail' -> 'Repair'" in s["reasoning"]
    emit_memory_cell("record_binding_match", "good")


# ── item_records: exact-artifact precedent grounding ─────────────────────────
class _Cur:
    def __init__(self, docs): self._d = list(docs)
    def sort(self, k, direction=-1):
        self._d.sort(key=lambda d: d.get(k) or datetime.min.replace(tzinfo=timezone.utc),
                     reverse=(direction == -1))
        return self
    async def to_list(self, n): return self._d[:n]


class _FakeCol:
    name = "mem_fake"
    def __init__(self): self.docs = []
    async def create_index(self, *a, **kw): pass
    def find(self, q=None, _p=None):
        return _Cur([dict(d) for d in self.docs
                     if all(d.get(k) == v for k, v in (q or {}).items())])
    async def find_one(self, q=None, sort=None):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in (q or {}).items())]
        return dict(hits[-1]) if hits else None
    async def insert_one(self, doc):
        self.docs.append({"_id": f"id{len(self.docs)}", **doc})
    async def update_one(self, q, u, upsert=False):
        doc = None
        for d in self.docs:
            if all(d.get(k) == v for k, v in (q or {}).items()):
                doc = d
        if doc is None and upsert:
            doc = {"_id": f"id{len(self.docs)}", **(q or {}), **(u.get("$setOnInsert") or {})}
            self.docs.append(doc)
        if doc is None:
            return
        doc.update(u.get("$set") or {})
        for k, v in (u.get("$push") or {}).items():
            doc.setdefault(k, []).append(v)
    async def update_many(self, q, u):
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in (q or {}).items()):
                d.update(u.get("$set") or {}); n += 1
        class _R: modified_count = n
        return _R()


def test_exact_artifact_precedent_grounds_reject(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(ir, "_col", lambda: fake)
    ir._indexes_ensured.add(_FakeCol.name)
    f = {"item_id": "other", "item_type": "defect-photo", "modality": "image",
         "content_sha256": "aaa", "media_ref": "m", "fields": {},
         "recommendation": "Fail", "confidence": 0.9, "rationale": "crack",
         "subject": "defect photo", "rubric_version": "v1"}
    asyncio.run(ir.persist_item_findings([f], correlation_id="r1", slug="app",
                                         app_id="a", tenant_id="t"))
    asyncio.run(ir.record_item_disposition(
        tenant_id="t", slug="app", item_id="other", modality="image",
        task_type="defect-photo", decision="reject", reason="stock image reused",
        actor="o"))
    prec = asyncio.run(ir.fetch_item_precedents(
        tenant_id="t", slug="app", modality="image", task_type="defect-photo",
        content_sha256="aaa"))
    block = ir.precedents_to_prompt(prec, current_item_id="me")
    assert "EXACT ARTIFACT" in block and "stock image reused" in block
    emit_memory_cell("record_binding_match", "bad")


def test_items_inherit_bad_case_outcome(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(ir, "_col", lambda: fake)
    ir._indexes_ensured.add(_FakeCol.name)
    f = {"item_id": "i1", "item_type": "t", "modality": "image", "fields": {}}
    asyncio.run(ir.persist_item_findings([f], correlation_id="r1", slug="s",
                                         app_id="a", tenant_id="t"))
    assert asyncio.run(ir.stamp_items_outcome("r1", {"label": "bad"})) == 1
    assert fake.docs[0]["outcome"]["label"] == "bad"
    emit_memory_cell("record_binding_match", "neutral")


# ── analysis_rubrics: the record-level DECISION rubric ───────────────────────
def test_decision_override_folds_into_record_rubric(monkeypatch):
    # the record analogue of the item rubric: an officer override (delta + why)
    # becomes a standing decision criterion for the whole app.
    fake = _FakeCol()
    monkeypatch.setattr(ar, "_col", lambda: fake)

    async def _no_sum(**kw):
        return None

    monkeypatch.setattr(ar, "_resummarize", _no_sum)
    ok = asyncio.run(ar.fold_decision_feedback(
        tenant_id="t", app_slug="app", actor="o", correlation_id="c1",
        reason="contained defect — repair window suffices",
        overrides=[{"override": {"status": {"from": "fail", "to": "repair"}}}],
        recommendation="fail"))
    assert ok is True
    doc = fake.docs[0]
    assert doc["modality"] == "record" and doc["task_type"] == "decision"
    assert "corrected status" in doc["corrections"][0]["reason"]
    emit_memory_cell("threshold_shift", "bad")   # record-level criteria shift, real impl


# ── analysis_rubrics: the SOP layer only moves on a real signal ──────────────
def test_reject_reason_becomes_durable_correction(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(ar, "_col", lambda: fake)

    async def _no_summarize(**kw):  # the LLM fold is out of scope here
        return None

    monkeypatch.setattr(ar, "_resummarize", _no_summarize)

    # append_correction $pushes — extend the fake for that one shape
    async def _update_one(q, u, upsert=False):
        doc = fake.docs[0] if fake.docs else None
        if doc is None and upsert:
            doc = {**(u.get("$setOnInsert") or {})}; fake.docs.append(doc)
        doc.update(u.get("$set") or {})
        for k, v in (u.get("$push") or {}).items():
            doc.setdefault(k, []).append(v)

    monkeypatch.setattr(fake, "update_one", _update_one)
    asyncio.run(ar.append_correction(
        tenant_id="t", app_slug="app", modality="image", task_type="defect-photo",
        reason="reject blurred nameplates", actor="o", item_id="i1",
        subject="nameplate photo"))
    corr = fake.docs[0]["corrections"]
    assert corr and corr[0]["reason"] == "reject blurred nameplates"
    assert corr[0]["subject"] == "nameplate photo"
    emit_memory_cell("threshold_shift", "bad")


def test_empty_reason_never_shifts_the_rubric(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(ar, "_col", lambda: fake)
    asyncio.run(ar.append_correction(
        tenant_id="t", app_slug="app", modality="image", task_type="tt",
        reason="   ", actor="o", item_id="i1"))
    assert fake.docs == []          # no signal, no shift
    emit_memory_cell("threshold_shift", "neutral")
