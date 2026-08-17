# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Phase 5 — E6 declarative date rules, E5 statement reconciliation,
E3 resubmission-after-rejection join.

Doctrine holds: absent data is a non-signal, unparseable data is a visible
note, single OCR-noise breaks never flag, and the E3 join is corroboration
that quotes the prior decision verbatim.
"""
import pytest

import fraud_checks as fc
import fraud_roles as fr
from fraud_synthesis import SIGNAL_ADVISORIES, severity_points


# ── E6: date rules comparator ────────────────────────────────────────────────
ROW = {"work_order_date": "2026-03-10", "inspection_date": "2026-03-08",
       "policy_start": "2026-03-01", "claim_date": "2026-03-04"}


def test_impossible_ordering_fires():
    s, notes = fc.date_rules_check(ROW, [
        {"name": "inspection_after_work_order",
         "earlier_field": "work_order_date", "later_field": "inspection_date"}])
    assert len(s) == 1 and s[0]["signal"] == fc.SIGNAL_DATE_RULE
    assert "BEFORE" in s[0]["why"] and s[0]["days_between"] == -2
    assert notes == []


def test_min_days_window_catches_opportunistic_claim():
    rules = [{"name": "claim_after_policy_start", "earlier_field": "policy_start",
              "later_field": "claim_date", "min_days_between": 15}]
    s, _ = fc.date_rules_check(ROW, rules)
    assert len(s) == 1 and "only 3 day(s)" in s[0]["why"]
    # 20 days later → clean.
    s2, _ = fc.date_rules_check({**ROW, "claim_date": "2026-03-21"}, rules)
    assert s2 == []


def test_max_days_and_absent_and_unparseable():
    rules = [{"name": "statement_fresh", "earlier_field": "statement_date",
              "later_field": "application_date", "max_days_between": 90}]
    # Stale document → fires.
    s, _ = fc.date_rules_check(
        {"statement_date": "2025-10-01", "application_date": "2026-03-01"}, rules)
    assert len(s) == 1 and "beyond the allowed 90" in s[0]["why"]
    # Absent side → non-signal, no note.
    s2, n2 = fc.date_rules_check({"application_date": "2026-03-01"}, rules)
    assert s2 == [] and n2 == []
    # Unparseable → visible note, never a flag.
    s3, n3 = fc.date_rules_check(
        {"statement_date": "last month", "application_date": "2026-03-01"}, rules)
    assert s3 == [] and any("unparseable" in n for n in n3)


# ── E5: statement reconciliation ─────────────────────────────────────────────
def _rows(*balances_and_txns):
    return [{"balance": b, "credit": c, "debit": d}
            for b, c, d in balances_and_txns]


def test_clean_chain_and_single_break_do_not_flag():
    clean = _rows(("1000.00", None, None), ("1200.00", "200.00", None),
                  ("900.00", None, "300.00"))
    s, notes = fc.statement_reconciliation(clean)
    assert s == [] and notes == []
    # ONE break (OCR misread) → note, not a signal.
    one = _rows(("1000.00", None, None), ("1300.00", "200.00", None),
                ("1000.00", None, "300.00"))
    s1, n1 = fc.statement_reconciliation(one)
    assert s1 == [] and any("OCR noise" in n for n in n1)


def test_repeated_breaks_flag_fabrication():
    fab = _rows(("1000.00", None, None), ("1500.00", "200.00", None),
                ("1100.00", None, "300.00"), ("2400.00", "100.00", None))
    s, _ = fc.statement_reconciliation(fab)
    assert len(s) == 1 and s[0]["signal"] == fc.SIGNAL_STATEMENT_BREAK
    assert s[0]["break_count"] == 3 and s[0]["rows_checked"] == 3
    total, counts = severity_points({"statement_findings": s})
    assert counts["statement_chain_break"] == 1 and total == 3


def test_unparseable_balance_skips_link_and_signed_amount_works():
    rows = [{"balance": "1000", "amount": None},
            {"balance": "??", "amount": "50"},         # unreadable → chain reset
            {"balance": "900", "amount": "-100"},       # no prior link
            {"balance": "800", "amount": "-100"}]       # clean signed-amount link
    s, notes = fc.statement_reconciliation(rows)
    assert s == [] and any("unparseable" in n for n in notes)


def test_unrecognized_txn_columns_never_flag_a_genuine_statement():
    """Review fix: rows whose transaction columns the extractor named
    differently (deposit/withdrawal) — or garbled — must NOT read as txn=0
    breaks; the chain link is skipped with a visible note."""
    rows = [{"balance": "1000.00", "deposit": "200.00"},
            {"balance": "1200.00", "deposit": None, "withdrawal": "300.00"},
            {"balance": "900.00", "withdrawal": "100.00"},
            {"balance": "800.00"}]
    s, notes = fc.statement_reconciliation(rows)
    assert s == []
    assert any("chain link skipped" in n for n in notes)
    # Garbled credit next to a real balance movement: also never a break.
    s2, n2 = fc.statement_reconciliation(
        [{"balance": "1000.00", "credit": "0"},
         {"balance": "1200.00", "credit": "2OO.OO"},   # OCR letter-Os
         {"balance": "1400.00", "credit": "2OO.OO"}])
    assert s2 == [] and any("chain link skipped" in n for n in n2)


# ── E6: autowire ─────────────────────────────────────────────────────────────
def test_date_rules_from_screening_validates_columns(caplog):
    cols = {"work_order_date", "inspection_date"}
    rules = fr.date_rules_from_screening(
        {"date_rules": [
            {"name": "ok", "earlier_field": "work_order_date",
             "later_field": "inspection_date", "min_days_between": 0},
            {"name": "ghost", "earlier_field": "no_such_col",
             "later_field": "inspection_date"}]},
        cols)
    assert len(rules) == 1 and rules[0]["name"] == "ok"
    assert any("do not exist" in r.getMessage() for r in caplog.records)
    assert fr.date_rules_from_screening({"date_rules": []}, cols) is None


def test_autowire_stamps_date_rules_and_kind():
    cat = {("ops", "ops.inspections"): {
        "kind": "sql",
        "fraud_screening": {
            "applies": True,
            "date_rules": [{"name": "order", "earlier_field": "work_order_date",
                            "later_field": "inspection_date"}],
        },
        "columns": [{"name": "id", "is_primary_key": True},
                    {"name": "work_order_date"}, {"name": "inspection_date"},
                    {"name": "photo", "artifact_role": "evidence"}],
    }}

    class _Spec:
        tools_v2 = []

    fr.autowire_fraud_roles(_Spec(), cat, data_sources=[
        {"id": "i", "type": "mcp", "ref": "ops.inspections"}])
    scr = next(t for t in _Spec.tools_v2
               if (t.get("kind") if isinstance(t, dict) else t.kind) == "consistency_check")
    dr = scr.get("date_rules") if isinstance(scr, dict) else scr.date_rules
    first = dr[0].model_dump() if hasattr(dr[0], "model_dump") else dr[0]
    assert first["name"] == "order"
    kind = scr.get("dataset_kind") if isinstance(scr, dict) else scr.dataset_kind
    assert kind == "sql"


# ── E3: resubmission-after-rejection join ────────────────────────────────────
class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCol:
    def __init__(self, docs):
        self.docs = docs
        self.last_query = None

    def find(self, query, projection=None):
        self.last_query = query
        return _FakeCursor(self.docs)


@pytest.mark.anyio
async def test_rejected_priors_fires_only_on_denial_decisions(monkeypatch):
    import entity_links
    import main

    # The PRODUCTION record_keys shape (main._build_decision_record): payload
    # + the stamped key_values candidates — never a bare key_value field.
    prior_denied = {
        "decision_id": "d1", "slug": "theft-triage", "mode": "human_approved",
        "created_at": "2026-05-02T10:00:00Z",
        "recommendation": {"decision": "Reject the waiver request — tampering confirmed"},
        "record_keys": [{"payload": {"case_id": "CASE-812", "status": "rejected"},
                         "key_values": ["CASE-812", "rejected"]}],
    }
    prior_ok = {
        "decision_id": "d2", "slug": "theft-triage", "mode": "human_approved",
        "created_at": "2026-05-03T10:00:00Z",
        "recommendation": {"decision": "Approve the payment plan"},
        "record_keys": [{"payload": {"case_id": "CASE-900"},
                         "key_values": ["CASE-900"]}],
    }
    # An APPROVAL whose AI text mentions 'reject' only negated — must not fire.
    prior_negated = {
        "decision_id": "d3", "slug": "theft-triage", "mode": "human_approved",
        "created_at": "2026-05-04T10:00:00Z",
        "recommendation": {"decision":
                           "Payment verified — the dispute should not be rejected"},
        "record_keys": [{"payload": {"case_id": "CASE-901"},
                         "key_values": ["CASE-901"]}],
    }
    # An OVERRIDDEN approval: the AI said reject, the officer changed the
    # writes — the AI text no longer describes the committed decision.
    prior_overridden = {
        "decision_id": "d4", "slug": "theft-triage", "mode": "human_approved",
        "created_at": "2026-05-05T10:00:00Z",
        "recommendation": {"decision": "Reject the claim"},
        "overrides": [{"dataset_id": "x", "override": {"status": {"from": "rejected", "to": "approved"}}}],
        "record_keys": [{"payload": {"case_id": "CASE-902"},
                         "key_values": ["CASE-902"]}],
    }
    col = _FakeCol([prior_denied, prior_ok, prior_negated, prior_overridden])
    monkeypatch.setattr(main, "_db", object(), raising=False)
    monkeypatch.setattr(main, "get_decision_records_col", lambda: col)

    sigs = [{"signal": "shared_identifier", "entity_type": "phone",
             "field": "contact_phone",
             "other_cases": [{"record_ref": "ops.cases:CASE-812"},
                             {"record_ref": "ops.cases:CASE-900"},
                             {"record_ref": "ops.cases:CASE-901"},
                             {"record_ref": "ops.cases:CASE-902"}]}]
    out = await entity_links.rejected_priors(tenant_id="acme", entity_signals=sigs)
    assert len(out) == 1
    hit = out[0]
    assert hit["signal"] == "resubmitted_after_rejection"
    assert hit["prior_record"] == "CASE-812"
    assert "Reject the waiver" in hit["note"]
    # The query matches the stamped key_values (the field production actually
    # writes) and excludes mode=human_rejected (officer rejected the AI's
    # RECOMMENDATION — not a denied case).
    assert "record_keys.key_values" in col.last_query
    assert "human_rejected" not in str(col.last_query["mode"])
    # Scoring + advisory coverage.
    total, counts = severity_points({"entity_signals": out})
    assert counts["resubmitted_after_rejection"] == 1
    for key in ("date_rule_violation", "statement_chain_break",
                "resubmitted_after_rejection"):
        assert key in SIGNAL_ADVISORIES and SIGNAL_ADVISORIES[key]["advisory"]


def test_reads_as_denial_negation_window():
    import entity_links
    assert entity_links._reads_as_denial("Reject the waiver request")
    assert entity_links._reads_as_denial("The claim is denied — tampering")
    # 'fail' family — inspection apps phrase denials as FAIL (live-observed on
    # prod: 'FAIL — fraudulent photo and recycled inspection report').
    assert entity_links._reads_as_denial("FAIL — fraudulent photo, recycled report")
    assert entity_links._reads_as_denial("Failed the inspection — wrong asset")
    assert not entity_links._reads_as_denial("the case should not fail; approve")
    assert not entity_links._reads_as_denial("should not be rejected; approve")
    assert not entity_links._reads_as_denial("don't decline the payment plan")
    assert not entity_links._reads_as_denial("Approve the payment plan")
    # Negation elsewhere + a real denial still reads as denial.
    assert entity_links._reads_as_denial(
        "The evidence is not conclusive, however reject the waiver")


@pytest.mark.anyio
async def test_rejected_priors_empty_inputs():
    import entity_links
    assert await entity_links.rejected_priors(tenant_id=None, entity_signals=[]) == []
    assert await entity_links.rejected_priors(
        tenant_id="t", entity_signals=[{"signal": "shared_identifier"}]) == []
