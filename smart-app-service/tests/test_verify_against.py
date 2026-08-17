# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Generic cross-dataset verification (plan F4) — the E4 shape reused.

A value extracted from a PINNED document verifies by key against a declared
target dataset. Not-found is fact-grade (the lookup RAN); a full match is
VERIFICATION; missing sides are non-signals; unparseable values are notes.
"""
import fraud_checks as fc
import fraud_roles as fr
from fraud_synthesis import SIGNAL_ADVISORIES, severity_points

TARGET_ROW = {"deed_no": "DD-2201", "sale_value": "1500000",
              "reg_date": "2026-01-10", "buyer_id": "B-77"}

COMPARE = [
    {"doc_field": "sale_value", "target_field": "sale_value",
     "type": "amount", "tolerance_pct": 1.0},
    {"doc_field": "reg_date", "target_field": "reg_date",
     "type": "date", "window_days": 3},
    {"doc_field": "buyer_id", "target_field": "buyer_id", "type": "id"},
]


# ── comparator ───────────────────────────────────────────────────────────────
def test_not_found_is_fact_grade():
    s, ok, _ = fc.verify_against_check(
        name="purchase_vs_registry", doc_ref="DD-9999", doc_values={},
        target_row=None, compare=COMPARE, target_name="registry.sale_deeds")
    assert not ok
    assert [x["signal"] for x in s] == [fc.SIGNAL_VERIFY_NOT_FOUND]
    assert "does not exist" in s[0]["why"]


def test_full_match_is_verification():
    s, ok, notes = fc.verify_against_check(
        name="purchase_vs_registry", doc_ref="DD-2201",
        doc_values={"sale_value": "₹15,00,000", "reg_date": "10 Jan 2026",
                    "buyer_id": "b 77"},
        target_row={k.lower(): v for k, v in TARGET_ROW.items()},
        compare=COMPARE, target_name="registry.sale_deeds")
    assert ok and s == [] and notes == []


def test_doctored_amount_flags_and_missing_side_does_not():
    row = {k.lower(): v for k, v in TARGET_ROW.items()}
    # A doctored bill: 2,500,000 vs registered 1,500,000.
    s, ok, _ = fc.verify_against_check(
        name="v", doc_ref="DD-2201", doc_values={"sale_value": "2500000"},
        target_row=row, compare=COMPARE)
    assert not ok and s[0]["signal"] == fc.SIGNAL_VERIFY_MISMATCH
    assert s[0]["field"] == "sale_value"
    # Document carries no date/buyer → those comparisons are non-signals.
    assert len(s) == 1
    # Unparseable amount → visible note, never a flag.
    s2, ok2, notes2 = fc.verify_against_check(
        name="v", doc_ref="DD-2201", doc_values={"sale_value": "fifteen lakh"},
        target_row=row, compare=COMPARE)
    assert ok2 and s2 == [] and any("unparseable" in n for n in notes2)


# ── gate + advisories ────────────────────────────────────────────────────────
def test_gate_scores_verify_signals():
    s, _, _ = fc.verify_against_check(
        name="v", doc_ref="X", doc_values={}, target_row=None, compare=[])
    total, counts = severity_points({"verify_findings": s})
    assert counts["verify_ref_not_found"] == 1 and total == 3
    for key in ("verify_ref_not_found", "verify_field_mismatch"):
        assert key in SIGNAL_ADVISORIES and SIGNAL_ADVISORIES[key]["advisory"]


# ── autowire ─────────────────────────────────────────────────────────────────
_TARGET_ENTRY = {
    "kind": "sql",
    "description": "Registered sale deeds (government registry mirror)",
    "columns": [{"name": "deed_no"}, {"name": "sale_value"},
                {"name": "reg_date"}, {"name": "buyer_id"}],
}
_CASE_COLS = [
    {"name": "loan_id", "is_primary_key": True},
    {"name": "purchase_bill_url", "artifact_role": "evidence"},
]
_BLOCK = {
    "name": "purchase_vs_registry",
    "target_dataset": "registry.sale_deeds",
    "match_field": "deed_no",
    "doc_column": "purchase_bill_url",
    "doc_ref_field": "deed_no",
    "compare": [{"doc_field": "sale_value", "target_field": "sale_value",
                 "type": "amount"}],
    "description": "collateral valuation must match the registered deed",
}


def test_verify_against_resolves_routing_and_pins():
    cfgs = fr.verify_against_from_screening(
        {"verify_against": [_BLOCK]},
        {"registry.sale_deeds": _TARGET_ENTRY}, dataset_columns=_CASE_COLS)
    assert len(cfgs) == 1
    c = cfgs[0]
    assert c["target_source_id"] == "registry" and c["target_kind"] == "sql"
    assert c["doc_column"] == "purchase_bill_url"
    assert c["target_description"].startswith("Registered sale deeds")
    assert c["compare"][0]["tolerance_pct"] == 1.0   # platform default filled


def test_verify_against_drops_loudly_on_mistakes(caplog):
    # Unknown target dataset → block dropped.
    assert fr.verify_against_from_screening(
        {"verify_against": [_BLOCK]}, {}, dataset_columns=_CASE_COLS) is None
    # Unpinned doc_column (not role-tagged on this dataset) → dropped.
    assert fr.verify_against_from_screening(
        {"verify_against": [{**_BLOCK, "doc_column": "random_col"}]},
        {"registry.sale_deeds": _TARGET_ENTRY},
        dataset_columns=_CASE_COLS) is None
    # Bad compare target_field → only that comparison dropped.
    cfgs = fr.verify_against_from_screening(
        {"verify_against": [{**_BLOCK, "compare": [
            {"doc_field": "x", "target_field": "no_such_col"}]}]},
        {"registry.sale_deeds": _TARGET_ENTRY}, dataset_columns=_CASE_COLS)
    assert cfgs and cfgs[0]["compare"] == []
    assert any("does not exist" in r.getMessage() for r in caplog.records)


def test_autowire_stamps_verify_against_and_description():
    cat = {
        ("loans", "loans.applications"): {
            "kind": "sql",
            "fraud_screening": {"applies": True, "verify_against": [_BLOCK]},
            "columns": _CASE_COLS,
        },
        ("registry", "registry.sale_deeds"): _TARGET_ENTRY,
    }

    class _Spec:
        tools_v2 = []

    fr.autowire_fraud_roles(_Spec(), cat, data_sources=[
        {"id": "l", "type": "mcp", "ref": "loans.applications"}])
    scr = next(t for t in _Spec.tools_v2
               if (t.get("kind") if isinstance(t, dict) else t.kind) == "consistency_check")
    va = scr.get("verify_against") if isinstance(scr, dict) else scr.verify_against
    assert va and len(va) == 1
    first = va[0].model_dump() if hasattr(va[0], "model_dump") else va[0]
    assert first["name"] == "purchase_vs_registry"
    desc = scr.get("description") if isinstance(scr, dict) else scr.description
    assert "purchase_vs_registry" in desc and "registry.sale_deeds" in desc
    assert "collateral valuation" in desc


def test_verify_sentence_refresh_is_idempotent():
    cfgs = fr.verify_against_from_screening(
        {"verify_against": [_BLOCK]},
        {"registry.sale_deeds": _TARGET_ENTRY}, dataset_columns=_CASE_COLS)
    d1 = fr._refresh_check_sentences("Base.", None, cfgs)
    assert d1.startswith("Base.") and "purchase_vs_registry" in d1
    assert fr._refresh_check_sentences(d1, None, cfgs) == d1
    assert fr._refresh_check_sentences(d1, None, None) == "Base."


def test_verify_against_check_model_rejects_typos():
    import pytest as _pytest
    from models import VerifyAgainstCheck

    VerifyAgainstCheck(name="v", target_source_id="r",
                       target_dataset="r.d", match_field="k",
                       doc_column="bill_url")
    with _pytest.raises(Exception):
        VerifyAgainstCheck(name="v", target_source_id="r",
                           target_dataset="r.d", match_field="k",
                           doc_column="bill_url", bogus=1)
