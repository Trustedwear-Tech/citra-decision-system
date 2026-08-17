# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Phase 1 — ontology domain triple → locale stamped onto fraud screens.

The ontology's domain.country decides which ID validators run and how
ambiguous dates parse for THAT dataset's checks; the FRAUD_LOCALE env demotes
to a fallback for un-annotated sources. The stamp must follow the ontology
both ways: set when a domain appears, CLEARED when it disappears (a stale
country silently misparsing dates is exactly the failure this prevents).
"""
import fraud_roles as fr
from fraud_checks import validate_formats


def test_locale_from_domain():
    assert fr.locale_from_domain({"country": "IN"}) == "in"
    assert fr.locale_from_domain({"country": "US", "vertical": "utility"}) == "us"
    assert fr.locale_from_domain(None) is None
    assert fr.locale_from_domain({}) is None
    assert fr.locale_from_domain({"country": ""}) is None


def _cat(domain=None):
    entry = {
        "kind": "sql",
        "fraud_screening": {"applies": True},
        "columns": [
            {"name": "case_id", "is_primary_key": True},
            {"name": "photo_url", "artifact_role": "evidence"},
        ],
    }
    if domain is not None:
        entry["domain"] = domain
    return {("field_ops", "field_ops.cases"): entry}


def _screen(spec):
    return next(t for t in spec.tools_v2
                if (t.get("kind") if isinstance(t, dict) else t.kind) == "consistency_check")


def _locale_of(tool):
    return tool.get("locale") if isinstance(tool, dict) else tool.locale


def test_autowire_stamps_locale_at_create_and_reconciles_changes():
    class _Spec:
        tools_v2 = []

    sources = [{"id": "c", "type": "mcp", "ref": "field_ops.cases"}]
    # CREATE with an IN domain → stamped 'in'.
    fr.autowire_fraud_roles(_Spec(), _cat({"country": "IN", "vertical": "utility",
                                           "sub_vertical": "power_recovery"}),
                            data_sources=sources)
    assert _locale_of(_screen(_Spec)) == "in"
    # Ontology moves to US → reconcile flips the stamp.
    fr.autowire_fraud_roles(_Spec(), _cat({"country": "US", "vertical": "utility",
                                           "sub_vertical": "power_recovery"}),
                            data_sources=sources)
    assert _locale_of(_screen(_Spec)) == "us"
    # Ontology drops its domain → stamp CLEARED (env fallback), never stale.
    fr.autowire_fraud_roles(_Spec(), _cat(None), data_sources=sources)
    assert _locale_of(_screen(_Spec)) is None


def test_locale_selects_the_validator_pack():
    """The same value is judged by the caller's pack: an Indian mobile number
    passes under 'in' and a US phone field flags it under 'us' — proving the
    per-dataset locale (not the env) picks the rules."""
    values = {"phone": "2125551234"}          # valid NANP (212-555-1234)
    assert validate_formats(values, locale="us") == []
    ind = validate_formats(values, locale="in")
    assert ind and ind[0]["field"] == "phone"  # Indian mobiles start 6-9


# ── Phase 2: vertical packs — explicit > pack > platform default ────────────
def test_pack_default_fills_only_when_ontology_is_silent():
    ledger = {"kind": "sql", "columns": [{"name": "ref"}]}
    cols = [{"name": "id", "is_primary_key": True},
            {"name": "receipt", "artifact_role": "payment_proof"}]
    claims_domain = {"vertical": "insurance", "sub_vertical": "claims",
                     "country": "US"}
    # Insurance claims: omitted window → pack's 7 (repair payments settle slow).
    cfg = fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "b.p", "match_field": "ref"}},
        {"b.p": ledger}, dataset_columns=cols, domain=claims_domain)
    assert cfg["date_window_days"] == 7
    assert cfg["amount_tolerance_pct"] == 1.0      # no pack value → platform
    # Explicit ontology value ALWAYS wins over the pack.
    cfg = fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "b.p", "match_field": "ref",
                           "date_window_days": 2}},
        {"b.p": ledger}, dataset_columns=cols, domain=claims_domain)
    assert cfg["date_window_days"] == 2
    # No domain → platform default 3, exactly the pre-pack behavior.
    cfg = fr.payment_proof_from_screening(
        {"payment_proof": {"ledger_dataset": "b.p", "match_field": "ref"}},
        {"b.p": ledger}, dataset_columns=cols)
    assert cfg["date_window_days"] == 3


def test_pack_gps_radius_for_inspections():
    dom = {"vertical": "utility", "sub_vertical": "metering_inspection",
           "country": "IN"}
    # Ontology silent → premise-bound 1 km from the pack.
    ctx = fr.claim_context_from_screening(
        {"incident_date_field": "d"}, "sql", {"d"}, domain=dom)
    assert ctx["gps_radius_km"] == 1.0
    # Explicit 0 (strict) survives — never swallowed by the pack.
    ctx = fr.claim_context_from_screening(
        {"incident_date_field": "d", "gps_radius_km": 0}, "sql", {"d"}, domain=dom)
    assert ctx["gps_radius_km"] == 0
    # No domain → nothing stamped; the comparator's 10 km applies at run time.
    ctx = fr.claim_context_from_screening(
        {"incident_date_field": "d"}, "sql", {"d"})
    assert "gps_radius_km" not in ctx


def test_autowire_stamps_domain_badge_and_advises(caplog):
    class _Spec:
        tools_v2 = []

    dom = {"vertical": "banking", "sub_vertical": "loan_recovery",
           "country": "IN"}
    cat = {("loans", "loans.disputes"): {
        "kind": "sql", "domain": dom,
        "fraud_screening": {"applies": True},
        "columns": [{"name": "id", "is_primary_key": True},
                    {"name": "doc_url", "artifact_role": "evidence"}],
    }}
    fr.autowire_fraud_roles(_Spec(), cat, data_sources=[
        {"id": "d", "type": "mcp", "ref": "loans.disputes"}])
    scr = next(t for t in _Spec.tools_v2
               if (t.get("kind") if isinstance(t, dict) else t.kind) == "consistency_check")
    badge = scr.get("domain") if isinstance(scr, dict) else scr.domain
    assert badge == {"vertical": "banking", "sub_vertical": "loan_recovery",
                     "country": "IN"}
    # loan_recovery + documents + NO payment_proof → the killer-check advisory.
    assert any("killer check" in r.getMessage() for r in caplog.records)
