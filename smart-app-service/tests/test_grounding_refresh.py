# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Unit tests for the few-shot-from-history grounding refresh.

Covers the deterministic pipeline (package → select → GUARD) and the
publish-time Gate-B validator. The Milvus/dept-MCP/embedding I/O is exercised
in live runs, not here — these tests pin the logic where correctness matters
(PII scrub, dedupe, diversity selection, the refresh guard, and the
non-bypassable publish gate).
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grounding_refresh import (  # noqa: E402
    evaluate_guard,
    package_rows,
    select_samples,
)
from models import AgentSpec, GroundingContract  # noqa: E402
from publish_validators import validate_grounding_contract  # noqa: E402


def _contract(**over) -> GroundingContract:
    base = dict(
        source_id="field_operations",
        dataset_id="claims_db.closed_motor_claims",
        source_id_field="claim_id",
        input_fields=["amount", "vehicle_age", "insured"],
        output_fields=["decision", "payout"],
        decision_field="decision",
        reasoning_field="reasoning",
        target_count=4,
        per_decision_min=1,
        min_samples=3,
        min_canonical=2,
        shrink_floor=0.5,
        required_decision_classes=["approve", "reject"],
        min_decision_fill_rate=0.7,
        source_profile_baseline={"row_count_approx": 14820, "n_decision_classes": 3},
        evaluation_verdict="14.8k closed claims across 3 classes — strong signal, grounded.",
    )
    base.update(over)
    return GroundingContract(**base)


def _rows(n_per_class=4):
    rows = []
    cid = 0
    for decision in ("approve", "reject", "escalate"):
        for _ in range(n_per_class):
            cid += 1
            rows.append({
                "claim_id": f"C{cid}",
                "amount": 1000 * cid,
                "vehicle_age": cid % 10,
                "insured": "Rao",
                "decision": decision,
                "payout": 500 * cid,
                "reasoning": "per policy clause 6.2",
                "email": f"user{cid}@example.com",  # not in input_fields → never surfaces
            })
    return rows


# ── package_rows ──────────────────────────────────────────────────────────
def test_package_maps_inputs_outputs_and_dedupes():
    c = _contract()
    rows = _rows(2)  # 6 rows
    rows.append(dict(rows[0]))  # exact duplicate id → deduped
    out = package_rows(rows, c)
    assert len(out) == 6
    s = out[0]
    assert set(s["input"].keys()) == {"amount", "vehicle_age", "insured"}
    assert "decision" in s["output"] and "payout" in s["output"]
    assert s["decision"] in {"approve", "reject", "escalate"}
    # email was NOT selected as an input field, so it must not leak in.
    assert "email" not in s["input"]


def test_package_drops_rows_without_decision_or_id():
    c = _contract()
    rows = [
        {"claim_id": "C1", "amount": 10, "decision": "approve"},
        {"claim_id": "C2", "amount": 20, "decision": None},        # no decision
        {"claim_id": "C2b", "amount": 20, "decision": ""},          # empty decision
        {"amount": 30, "decision": "reject"},                      # no id
    ]
    out = package_rows(rows, c)
    assert [s["source_id"] for s in out] == ["C1"]


def test_package_keeps_only_terminal_states():
    # Only DECIDED rows ground; in-progress states are dropped.
    c = _contract(terminal_states=["recovered", "written_off"])
    rows = [
        {"claim_id": "C1", "amount": 10, "decision": "recovered"},
        {"claim_id": "C2", "amount": 20, "decision": "pending"},        # in-progress → dropped
        {"claim_id": "C3", "amount": 30, "decision": "written_off"},
        {"claim_id": "C4", "amount": 40, "decision": "under_recovery"}, # in-progress → dropped
    ]
    out = package_rows(rows, c)
    assert sorted(s["source_id"] for s in out) == ["C1", "C3"]
    assert {s["decision"] for s in out} == {"recovered", "written_off"}


def test_package_no_terminal_states_keeps_all_decided():
    c = _contract(terminal_states=[])  # no terminal filter → any non-empty decision
    rows = [{"claim_id": "C1", "amount": 1, "decision": "pending"},
            {"claim_id": "C2", "amount": 2, "decision": "recovered"}]
    out = package_rows(rows, c)
    assert sorted(s["source_id"] for s in out) == ["C1", "C2"]


def test_package_scrubs_pii_in_free_text_input():
    c = _contract(input_fields=["note", "amount"])
    rows = [{
        "claim_id": "C1", "amount": 10, "decision": "approve",
        "note": "call 9876543210 or mail a@b.com",
    }]
    out = package_rows(rows, c)
    note = out[0]["input"]["note"]
    assert "9876543210" not in note and "a@b.com" not in note
    assert "<phone>" in note and "<email>" in note


# ── select_samples ──────────────────────────────────────────────────────────
def test_select_honours_target_count_and_per_class_min():
    c = _contract(target_count=4, per_decision_min=1)
    samples = package_rows(_rows(4), c)  # 12 samples, 3 classes
    canonical, pool = select_samples(samples, c)
    assert len(canonical) == 4
    # every class represented (per_decision_min=1, 3 classes ≤ target 4)
    classes = {s["decision"] for s in canonical}
    assert {"approve", "reject", "escalate"} <= classes
    # canonical flags set on the full pool
    flagged = [s for s in pool if s.get("is_canonical")]
    assert len(flagged) == 4


def test_select_is_deterministic():
    c = _contract()
    samples = package_rows(_rows(3), c)
    a, _ = select_samples([dict(s) for s in samples], c)
    b, _ = select_samples([dict(s) for s in samples], c)
    assert [s["source_id"] for s in a] == [s["source_id"] for s in b]


# ── evaluate_guard (Gate B) ─────────────────────────────────────────────────
def test_guard_passes_healthy_refresh():
    c = _contract()
    samples = package_rows(_rows(4), c)
    canonical, pool = select_samples(samples, c)
    g = evaluate_guard(pool, canonical, rows_pulled=12, live_count=10, contract=c)
    assert g.ok, g.failures


def test_guard_rejects_below_min_samples():
    c = _contract(min_samples=100)
    samples = package_rows(_rows(4), c)
    canonical, pool = select_samples(samples, c)
    g = evaluate_guard(pool, canonical, rows_pulled=12, live_count=0, contract=c)
    assert not g.ok and any("min_samples" in f for f in g.failures)


def test_guard_rejects_missing_required_class():
    c = _contract(required_decision_classes=["approve", "reject", "settle"])
    samples = package_rows(_rows(4), c)
    canonical, pool = select_samples(samples, c)
    g = evaluate_guard(pool, canonical, rows_pulled=12, live_count=0, contract=c)
    assert not g.ok and any("settle" in f for f in g.failures)


def test_guard_rejects_shrink_past_floor():
    c = _contract(shrink_floor=0.9, min_samples=1, min_canonical=1)
    samples = package_rows(_rows(2), c)  # 6 samples
    canonical, pool = select_samples(samples, c)
    # live was 100; 6 < 0.9*100 → shrink rejection
    g = evaluate_guard(pool, canonical, rows_pulled=6, live_count=100, contract=c)
    assert not g.ok and any("shrink" in f for f in g.failures)


def test_guard_rejects_low_decision_fill_rate():
    c = _contract(min_decision_fill_rate=0.95, min_samples=1, min_canonical=1)
    samples = package_rows(_rows(2), c)  # 6 packaged
    canonical, pool = select_samples(samples, c)
    # 6 packaged out of 100 pulled → fill 0.06 < 0.95
    g = evaluate_guard(pool, canonical, rows_pulled=100, live_count=0, contract=c)
    assert not g.ok and any("fill rate" in f for f in g.failures)


# ── Gate B publish validator (G-01) ──────────────────────────────────────────
def _agent_with_neighbor(grounding: dict | None, collection="Historical_Refresh", agent_id="a1"):
    spec = {
        "agent_id": agent_id,
        "name": "Grounded Agent",
        "system_prompt": "you triage.",
        "tools_v2": [
            {"kind": "neighbor_samples", "name": "history", "collection": collection},
        ],
    }
    if grounding is not None:
        spec["grounding"] = grounding
    return AgentSpec.model_validate(spec)


def _good_grounding() -> dict:
    return _contract().model_dump()


def test_validator_passes_with_complete_contract():
    agent = _agent_with_neighbor(_good_grounding())
    assert validate_grounding_contract(agent) == []


def test_validator_blocks_when_grounding_missing():
    agent = _agent_with_neighbor(None)
    errs = validate_grounding_contract(agent)
    assert errs and errs[0]["rule_id"] == "G-01"
    assert "grounding" in errs[0]["location"]


def test_validator_blocks_missing_gate_a_evidence():
    g = _good_grounding()
    g["evaluation_verdict"] = None
    g["source_profile_baseline"] = {}
    errs = validate_grounding_contract(_agent_with_neighbor(g))
    locs = {e["location"] for e in errs}
    assert any("evaluation_verdict" in l for l in locs)
    assert any("source_profile_baseline" in l for l in locs)


def test_validator_blocks_collection_mismatch():
    agent = _agent_with_neighbor(_good_grounding(), collection="samples_WRONG", agent_id="a1")
    errs = validate_grounding_contract(agent)
    assert any("collection" in e["location"] for e in errs)


def test_validator_noop_without_neighbor_tool():
    spec = AgentSpec.model_validate({
        "agent_id": "a2", "name": "Plain", "system_prompt": "hi", "tools_v2": [],
    })
    assert validate_grounding_contract(spec) == []
