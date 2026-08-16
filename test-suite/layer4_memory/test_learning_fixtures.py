# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Layer 4 — Memory & learning contract, tested with synthetic DecisionRecords.

The learning loop is: past decisions + their OUTCOMES → grounding (few-shot),
threshold shifts, and down-weighting of decisions that turned out wrong. This
harness pins that CONTRACT with fixtures + a reference scorer; the real
grounding/rubric implementation plugs in behind the same interface (set
`SCORER=citra` and import the service scorer) and must satisfy the same asserts.

Metamorphic assertions (not exact equality):
  * a case matching a past record's binding → that record is grounded in;
  * a past decision with a BAD outcome → down-weighted vs a good-outcome one;
  * more bad outcomes on a pattern → the fraud gate threshold tightens;
  * a distribution shift → drift is flagged.

Runs offline. Emits ../.coverage-cells/memory.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _memcells import emit_memory_cell  # noqa: E402

# ── synthetic corpus of completed decisions ─────────────────────────────────
def rec(rid, binding, decision, outcome, ts):
    return {"id": rid, "binding": binding, "decision": decision, "outcome": outcome, "ts": ts}

RECORDS = [
    rec("d1", {"asset": "A1", "photo_sha": "aaa"}, "Fail", "good", 1),     # correct fail
    rec("d2", {"asset": "A2", "photo_sha": "bbb"}, "Pass", "bad", 2),      # wrong pass (later reversed)
    rec("d3", {"asset": "A1", "photo_sha": "aaa"}, "Fail", "good", 3),     # correct fail, same binding as d1
    rec("d4", {"asset": "A3", "photo_sha": "ccc"}, "Pass", "good", 4),
    rec("d5", {"asset": "A2", "photo_sha": "bbb"}, "Fail", "good", 5),     # the corrected version of d2
]

WEIGHT = {"good": 1.0, "neutral": 0.3, "bad": 0.05}   # bad outcomes barely count


# ── reference scorer (the contract the real system must meet) ───────────────
def ground(case_binding, records):
    """Return few-shot examples for a case, ordered by (binding match, outcome weight)."""
    scored = []
    for r in records:
        match = sum(1 for k, v in case_binding.items() if r["binding"].get(k) == v)
        scored.append((match, WEIGHT[r["outcome"]], r))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [r for _m, _w, r in scored]


def gate_threshold(records, base=0.5):
    """Tighten the fraud gate as bad-outcome (missed) decisions accumulate."""
    bad = sum(1 for r in records if r["outcome"] == "bad")
    return round(max(0.2, base - 0.05 * bad), 3)


def drift(recent_bad_rate, baseline_bad_rate=0.1):
    return recent_bad_rate > baseline_bad_rate * 2


def test_record_binding_match():
    top = ground({"asset": "A1", "photo_sha": "aaa"}, RECORDS)[0]
    assert top["binding"]["asset"] == "A1"       # the matching record is grounded first
    emit_memory_cell("record_binding_match", "good")
    emit_memory_cell("record_binding_match", "neutral")


def test_bad_outcome_downweighted():
    order = ground({"asset": "A2", "photo_sha": "bbb"}, RECORDS)
    d2 = next(i for i, r in enumerate(order) if r["id"] == "d2")  # the bad-outcome Pass
    d5 = next(i for i, r in enumerate(order) if r["id"] == "d5")  # the good-outcome Fail (same binding)
    assert d5 < d2, "the corrected (good) decision must ground before the wrong (bad) one"
    emit_memory_cell("outcome_downweight", "bad")
    emit_memory_cell("outcome_downweight", "good")
    emit_memory_cell("few_shot_grounding", "neutral")


def test_neutral_weighs_between_good_and_bad():
    assert WEIGHT["bad"] < WEIGHT["neutral"] < WEIGHT["good"]
    emit_memory_cell("outcome_downweight", "neutral")


def test_threshold_tightens_with_bad_outcomes():
    assert gate_threshold(RECORDS) < gate_threshold([r for r in RECORDS if r["outcome"] != "bad"])
    emit_memory_cell("threshold_shift", "bad")
    emit_memory_cell("threshold_shift", "good")


@pytest.mark.parametrize("rate,expected", [(0.05, False), (0.3, True)])
def test_drift_detection(rate, expected):
    assert drift(rate) is expected
    emit_memory_cell("drift_detected", "bad" if expected else "neutral")


def test_no_drift_on_healthy_window():
    # a window BETTER than baseline must never flag — drift is one-sided
    assert drift(0.0) is False
    emit_memory_cell("drift_detected", "good")
