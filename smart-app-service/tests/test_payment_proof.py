# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Payment-proof verification (E4) — 'I already paid, here's the receipt.'

Pins the doctrine: 'reference not found' is a fact-grade signal (only when
the lookup actually RAN); a full match is VERIFICATION, never a flag; OCR
tolerances keep honest receipts from flagging; ontology mis-declarations are
dropped LOUDLY at autowire, never silently no-oped at runtime.
"""
import fraud_checks as fc
import fraud_roles as fr
from fraud_synthesis import SIGNAL_ADVISORIES, severity_points

CFG = {
    "ledger_dataset": "billing.payments",
    "match_field": "transaction_ref",
    "amount_field": "amount",
    "date_field": "payment_date",
    "party_field": "consumer_id",
    "amount_tolerance_pct": 1.0,
    "date_window_days": 3,
}

LEDGER_ROW = {"transaction_ref": "UTR-778", "amount": "4120.00",
              "payment_date": "2026-03-12", "consumer_id": "1000007344"}


# ── comparator ───────────────────────────────────────────────────────────────
def test_ref_not_found_is_a_fact_signal():
    signals, verified, _ = fc.payment_proof_check(
        doc_ref="UTR-999", ledger_row=None, cfg=CFG)
    assert not verified
    assert [s["signal"] for s in signals] == [fc.SIGNAL_PAY_NOT_FOUND]
    assert "never happened" in signals[0]["why"]


def test_full_match_is_verification_not_a_flag():
    signals, verified, notes = fc.payment_proof_check(
        doc_ref="UTR-778", doc_amount="₹4,120", doc_date="12 Mar 2026",
        doc_party="1000007344", ledger_row=LEDGER_ROW, cfg=CFG)
    assert verified is True and signals == [] and notes == []


def test_amount_tolerance_absorbs_ocr_noise_but_catches_doctoring():
    # Within 1%: no flag (4,120 vs 4,120.41).
    s1, v1, _ = fc.payment_proof_check(
        doc_ref="UTR-778", doc_amount="4120.41", ledger_row=LEDGER_ROW, cfg=CFG)
    assert v1 and s1 == []
    # 41,200 vs 4,120 — a doctored digit: flags.
    s2, v2, _ = fc.payment_proof_check(
        doc_ref="UTR-778", doc_amount="41200", ledger_row=LEDGER_ROW, cfg=CFG)
    assert not v2 and s2[0]["signal"] == fc.SIGNAL_PAY_AMOUNT


def test_date_window_and_party_reuse():
    s, v, _ = fc.payment_proof_check(
        doc_ref="UTR-778", doc_date="2026-03-14",  # within 3d window
        doc_party="1000099999",                    # someone ELSE's receipt
        ledger_row=LEDGER_ROW, cfg=CFG)
    assert not v
    assert [x["signal"] for x in s] == [fc.SIGNAL_PAY_PARTY]
    assert "belongs to" in s[0]["why"]


def test_absent_sides_are_non_signals():
    # Ledger has no amount column declared / doc carries no date: nothing flags.
    cfg = {**CFG, "amount_field": None, "date_field": None, "party_field": None}
    s, v, _ = fc.payment_proof_check(
        doc_ref="UTR-778", doc_amount="99999", ledger_row=LEDGER_ROW, cfg=cfg)
    assert v and s == []


def test_unparseable_doc_amount_is_a_visible_note():
    s, v, notes = fc.payment_proof_check(
        doc_ref="UTR-778", doc_amount="four thousand", ledger_row=LEDGER_ROW, cfg=CFG)
    assert v and s == []
    assert any("unparseable" in n for n in notes)


# ── gate scoring + advisories ────────────────────────────────────────────────
def test_gate_scores_payment_signals_but_never_verification():
    signals, _, _ = fc.payment_proof_check(doc_ref="X", ledger_row=None, cfg=CFG)
    total, counts = severity_points({"payment_findings": signals,
                                     "payment_verified": True})
    assert counts["payment_ref_not_found"] == 1 and total == 3
    assert "payment_verified" not in counts


def test_every_payment_signal_has_an_advisory():
    for key in ("payment_ref_not_found", "payment_amount_mismatch",
                "payment_date_mismatch", "payment_party_mismatch"):
        assert key in SIGNAL_ADVISORIES and SIGNAL_ADVISORIES[key]["advisory"]


# ── autowire: ontology → stamped config, fail-loud validation ────────────────
_LEDGER_ENTRY = {
    "kind": "sql",
    "description": "Settled customer payments, UTR-keyed",
    "columns": [
        {"name": "transaction_ref"}, {"name": "amount"},
        {"name": "payment_date"}, {"name": "consumer_id"},
    ],
}

# The SCREENED dataset's columns — the receipt column is TAGGED (F1), the other
# attached bill is plain evidence and must never enter the payment match.
_CASE_COLUMNS = [
    {"name": "complaint_id", "is_primary_key": True},
    {"name": "receipt_url", "artifact_role": "payment_proof"},
    {"name": "purchase_bill_url", "artifact_role": "evidence"},
]


def test_payment_proof_from_screening_resolves_ledger_routing():
    cfg = fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "billing.payments",
                           "match_field": "transaction_ref",
                           "amount_field": "amount",
                           "party_field": "consumer_id"}},
        {"billing.payments": _LEDGER_ENTRY},
        dataset_columns=_CASE_COLUMNS,
    )
    assert cfg["ledger_source_id"] == "billing"
    assert cfg["ledger_kind"] == "sql"
    assert cfg["amount_field"] == "amount"
    assert cfg["doc_ref_field"] == "transaction_ref"   # default
    assert cfg["amount_tolerance_pct"] == 1.0
    # F1: pinned to the TAGGED column only — the purchase bill is not in it.
    assert cfg["doc_columns"] == ["receipt_url"]
    # F2: the ledger's own description rides along for the tool description.
    assert cfg["ledger_description"] == "Settled customer payments, UTR-keyed"


def test_payment_proof_unpinned_drops_loudly(caplog):
    # No column tagged payment_proof → whole block dropped (never run unpinned).
    assert fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "billing.payments",
                           "match_field": "transaction_ref"}},
        {"billing.payments": _LEDGER_ENTRY},
        dataset_columns=[{"name": "id", "is_primary_key": True},
                         {"name": "doc_url", "artifact_role": "evidence"}],
    ) is None
    assert any("payment_proof" in r.getMessage() and "tagged" in r.getMessage()
               for r in caplog.records)


def test_payment_proof_unknown_ledger_or_column_drops_loudly(caplog):
    # Unknown ledger dataset → whole block dropped.
    assert fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "nope.missing",
                           "match_field": "transaction_ref"}}, {},
        dataset_columns=_CASE_COLUMNS) is None
    # Bad match_field → dropped; bad optional column → only that comparison dropped.
    assert fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "billing.payments",
                           "match_field": "no_such_col"}},
        {"billing.payments": _LEDGER_ENTRY},
        dataset_columns=_CASE_COLUMNS) is None
    cfg = fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "billing.payments",
                           "match_field": "transaction_ref",
                           "amount_field": "no_such_col"}},
        {"billing.payments": _LEDGER_ENTRY},
        dataset_columns=_CASE_COLUMNS)
    assert cfg is not None and "amount_field" not in cfg
    assert any("does not exist" in r.getMessage() for r in caplog.records)


# ── F2: the agent-facing description names the ledger + the pinned document ──
def test_tool_description_names_ledger_and_receipt_column():
    cfg = fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "billing.payments",
                           "match_field": "transaction_ref"}},
        {"billing.payments": _LEDGER_ENTRY},
        dataset_columns=_CASE_COLUMNS)
    s = fr._payment_proof_sentence(cfg)
    assert "billing.payments" in s
    assert "Settled customer payments" in s
    assert "receipt_url" in s and "never from other attached bills" in s
    # Idempotent refresh: re-stamping replaces, clearing removes — a
    # hand-authored prefix survives both.
    d1 = fr._refresh_check_sentences("My custom screen.", cfg, None)
    assert d1.startswith("My custom screen.") and "billing.payments" in d1
    d2 = fr._refresh_check_sentences(d1, cfg, None)
    assert d2 == d1                       # no duplicate sentence
    assert fr._refresh_check_sentences(d1, None, None) == "My custom screen."
    # Overflow clamp (review fix): the composed description can NEVER exceed
    # the model cap — an oversize value persisted via setattr would brick the
    # app on its next model_validate load.
    big = dict(cfg, ledger_description="x" * 300)
    huge = fr._refresh_check_sentences("p" * 500, big, [
        {"name": f"v{i}", "doc_column": "receipt_url",
         "target_dataset": "a.b", "target_description": "y" * 300,
         "description": "z" * 300} for i in range(3)])
    assert len(huge) <= fr._DESC_HARD_MAX


# ── F3: the check runs ONLY when the tagged receipt is actually attached ─────
def test_payment_doc_attached_gate():
    findings_ok = [{"column": "receipt_url", "sha256": "x"},
                   {"column": "purchase_bill_url", "sha256": "y"}]
    ok, note = fc.payment_doc_attached(["receipt_url"], findings_ok)
    assert ok and note is None
    # Receipt column empty/unreadable → skipped with a visible note, even
    # though ANOTHER bill is attached.
    findings_other = [{"column": "receipt_url", "error": "empty value"},
                      {"column": "purchase_bill_url", "sha256": "y"}]
    ok, note = fc.payment_doc_attached(["receipt_url"], findings_other)
    assert not ok and "receipt_url" in note and "skipped" in note
    # Unpinned config (no doc_columns) may NEVER run.
    ok, note = fc.payment_doc_attached([], findings_ok)
    assert not ok and "republish" in note


def test_autowire_stamps_payment_proof_on_screen():
    cat = {
        ("field_operations", "field_operations.complaints"): {
            "kind": "sql",
            "fraud_screening": {
                "applies": True,
                "payment_proof": {"ledger_dataset": "billing.payments",
                                  "match_field": "transaction_ref"},
            },
            "columns": list(_CASE_COLUMNS),
        },
        ("billing", "billing.payments"): _LEDGER_ENTRY,
    }

    class _Spec:
        tools_v2 = []

    fr.autowire_fraud_roles(_Spec(), cat, data_sources=[
        {"id": "cmp", "type": "mcp", "ref": "field_operations.complaints"}])
    screens = [t for t in _Spec.tools_v2
               if (t.get("kind") if isinstance(t, dict) else t.kind) == "consistency_check"]
    assert len(screens) == 1
    scr = screens[0]
    pp = scr.get("payment_proof") if isinstance(scr, dict) else scr.payment_proof
    if hasattr(pp, "model_dump"):
        pp = pp.model_dump()
    assert pp["ledger_dataset"] == "billing.payments"
    assert pp["ledger_kind"] == "sql"
    assert pp["doc_columns"] == ["receipt_url"]
    # The tagged receipt column is fingerprinted too (reuse across cases is a
    # signal), alongside the evidence bill.
    cols = scr.get("url_columns") if isinstance(scr, dict) else scr.url_columns
    assert "receipt_url" in cols and "purchase_bill_url" in cols
    # F2: the CREATE-time description already names ledger + pinned column.
    desc = scr.get("description") if isinstance(scr, dict) else scr.description
    assert "billing.payments" in desc and "receipt_url" in desc


def test_reused_receipt_is_a_fraud_signal_with_its_own_label():
    got = fr.interpret_reuse(artifact_role="payment_proof", exact_duplicate=True)
    assert got["is_fraud_signal"] is True
    assert got["label"] == "reused payment proof"
    assert "cannot clear more than one" in got["why"]


def test_payment_proof_check_model_rejects_typos():
    import pytest as _pytest
    from models import PaymentProofCheck

    PaymentProofCheck(ledger_source_id="billing", ledger_dataset="billing.payments",
                      match_field="transaction_ref",
                      doc_columns=["receipt_url"], ledger_description="payments")
    with _pytest.raises(Exception):
        PaymentProofCheck(ledger_source_id="b", ledger_dataset="b.p",
                          match_field="x", bogus=1)


# ── F6: Indian lakh-crore digit grouping must parse like US grouping ─────────
def test_normalize_amount_indian_grouping():
    assert fc.normalize_amount("₹1,23,456.00") == fc.normalize_amount("123456")
    assert fc.normalize_amount("₹41,20,000") == fc.normalize_amount("$4,120,000.00")
