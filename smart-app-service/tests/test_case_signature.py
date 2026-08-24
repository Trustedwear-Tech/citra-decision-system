# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Case signature — deterministic facet derivation + CS-01 publish validation.
Phase B of docs/clause-memory-graph-plan.md.

Pins the properties the clause routing depends on:
  * derivation is DETERMINISTIC and canonical (same case ⇒ byte-identical
    tokens, or the subset query in §6 is unstable);
  * ontology drift becomes a COUNTABLE __unknown token, never a silent new
    bucket and never a mis-route;
  * a signal facet is OMITTED when screening never ran — 'clear' is a claim
    about a check that happened, and must not be guessed;
  * an undecidable facet (null, absent, non-numeric, unparseable) becomes
    __unknown for THAT FAMILY ONLY and never empties the whole token set —
    derivation is all-or-nothing, so a per-facet raise cost the case its
    entire clause memory;
  * CS-01 rejects facets that would emit __unknown for every case.
"""
from __future__ import annotations

import logging

import pytest

from case_signature import (
    PLATFORM_SIGNAL_IDS,
    UNKNOWN,
    band_token,
    derive_facets,
    learning_config,
    reason_codes,
    signature_of,
)

SIG = {
    "version": 1,
    "facets": [
        {"family": "loss_type", "kind": "enum", "from_column": "loss_type",
         "values": ["collision", "theft", "fire", "windshield"]},
        {"family": "amount_band", "kind": "band", "from_column": "claim_amount",
         "edges": [1000, 25000, 100000]},
        {"family": "police_report", "kind": "presence",
         "from_column": "police_report_no"},
        {"family": "policy_age", "kind": "age_band",
         "from_columns": ["policy_start_date", "loss_date"], "edges": [30, 180]},
        {"family": "exif", "kind": "signal",
         "signal_id": "exif_capture_before_claim"},
    ],
    "reason_codes": [
        {"code": "evidence_insufficient", "label": "Evidence insufficient"},
        {"code": "exclusion_applies", "label": "Exclusion applies"},
        {"code": "other", "label": "Something else"},
    ],
}

CASE = {
    "loss_type": "Theft",
    "claim_amount": 38000,
    "police_report_no": None,
    "policy_start_date": "2026-06-01",
    "loss_date": "2026-06-19",
}


# ── band tokens ──────────────────────────────────────────────────────────────
def test_band_token_covers_every_interval():
    e = [1000, 25000, 100000]
    assert band_token("a", 500, e) == "a:lt_1000"
    assert band_token("a", 1000, e) == "a:1000_25000"
    assert band_token("a", 24999, e) == "a:1000_25000"
    assert band_token("a", 25000, e) == "a:25000_100000"
    assert band_token("a", 100000, e) == "a:gte_100000"
    assert band_token("a", 999999, e) == "a:gte_100000"


# ── derivation ───────────────────────────────────────────────────────────────
def test_derives_the_worked_example():
    facets, unknown = derive_facets(
        CASE, SIG, signals=[], signals_ran=True,
        domain={"vertical": "insurance", "country": "US"})
    assert unknown == []
    assert facets == [
        "amount_band:25000_100000",
        "country:us",
        "exif:clear",
        "loss_type:theft",
        "police_report:absent",
        "policy_age:lt_30",
        "vertical:insurance",
    ]


def test_derivation_is_canonical_and_stable():
    a, _ = derive_facets(CASE, SIG, signals_ran=True)
    b, _ = derive_facets(dict(CASE), SIG, signals_ran=True)
    assert a == b == sorted(set(a))     # deduped + sorted, byte-identical


def test_presence_present_when_populated():
    facets, _ = derive_facets({**CASE, "police_report_no": "FIR-991"}, SIG,
                              signals_ran=True)
    assert "police_report:present" in facets


def test_empty_string_counts_as_absent():
    facets, _ = derive_facets({**CASE, "police_report_no": "   "}, SIG,
                              signals_ran=True)
    assert "police_report:absent" in facets


# ── drift is countable, never silent ─────────────────────────────────────────
def test_undeclared_enum_value_becomes_unknown_and_is_reported():
    facets, unknown = derive_facets({**CASE, "loss_type": "hail"}, SIG,
                                    signals_ran=True)
    assert f"loss_type:{UNKNOWN}" in facets
    assert unknown == ["loss_type"]
    # and it did NOT quietly become a new legitimate bucket
    assert "loss_type:hail" not in facets


def test_value_map_rescues_a_legacy_encoding():
    sig = {**SIG, "facets": [
        {"family": "loss_type", "kind": "enum", "from_column": "loss_type",
         "values": ["theft"], "value_map": {"thft": "theft"}}]}
    facets, unknown = derive_facets({"loss_type": "THFT"}, sig, signals_ran=True)
    assert facets == ["loss_type:theft"] and unknown == []


def test_unparseable_date_is_unknown_not_omitted():
    facets, unknown = derive_facets({**CASE, "loss_date": "not-a-date"}, SIG,
                                    signals_ran=True)
    assert f"policy_age:{UNKNOWN}" in facets and "policy_age" in unknown


# ── signals are never guessed ────────────────────────────────────────────────
def test_signal_facet_omitted_when_screening_never_ran():
    facets, _ = derive_facets(CASE, SIG, signals=None, signals_ran=False)
    assert not any(f.startswith("exif:") for f in facets)


def test_signal_fires_from_the_platform_signal_shape():
    facets, _ = derive_facets(
        CASE, SIG,
        signals=[{"signal": "exif_capture_before_claim", "severity": "warn"}],
        signals_ran=True)
    assert "exif:fired" in facets


def test_signal_clear_only_when_screening_ran():
    facets, _ = derive_facets(CASE, SIG, signals=[{"signal": "other_thing"}],
                              signals_ran=True)
    assert "exif:clear" in facets


def test_platform_signal_registry_is_grounded_in_real_emitters():
    # These are imported from fraud_checks/entity_links, not re-typed — a
    # renamed signal must break the import, not silently never fire.
    for sid in ("exif_capture_before_claim", "payment_amount_mismatch",
                "verify_field_mismatch", "shared_identifier",
                "resubmitted_after_rejection"):
        assert sid in PLATFORM_SIGNAL_IDS


# ── loud when the spec and the data disagree — but never fatal ───────────────
#
# These used to assert a raise. Derivation is all-or-nothing, so raising for ONE
# facet returned `case_facets: []` for the WHOLE case: no clause retrieval, no
# learning, reported to nobody as anything but a routine warning. `__unknown` is
# the honest per-facet answer and no clause may be scoped to it, so an
# undecidable family stops matching rather than matching the wrong thing.


def test_band_on_non_numeric_column_is_unknown_not_fatal(caplog):
    with caplog.at_level(logging.ERROR):
        facets, unknown = derive_facets(
            {**CASE, "claim_amount": "quite a lot"}, SIG, signals_ran=True)
    assert "amount_band:__unknown" in facets
    assert "amount_band" in unknown
    # the OTHER families still route — that is the whole point
    assert "loss_type:theft" in facets
    # a non-null non-number is a SPEC error and must be loud about it
    assert "SPEC ERROR" in caplog.text


def test_null_band_column_is_unknown_not_fatal(caplog):
    """The real case that took an app's clause memory out.

    A nullable numeric column is legitimate — a personal loan has no LTV, so
    `ltv_percent` is NULL on exactly the cases where the concept does not apply.
    The spec is right, the data is right, and publish could never have rejected
    it."""
    with caplog.at_level(logging.WARNING):
        facets, unknown = derive_facets(
            {**CASE, "claim_amount": None}, SIG, signals_ran=True)
    assert "amount_band:__unknown" in facets
    assert "amount_band" in unknown
    assert "loss_type:theft" in facets
    assert "SPEC ERROR" not in caplog.text, "a null is a data gap, not a spec bug"


def test_missing_column_for_band_is_unknown_not_fatal():
    rec = {k: v for k, v in CASE.items() if k != "claim_amount"}
    facets, unknown = derive_facets(rec, SIG, signals_ran=True)
    assert "amount_band:__unknown" in facets
    assert "amount_band" in unknown


def test_one_bad_facet_never_costs_the_whole_signature():
    """The property that was actually violated: derivation is all-or-nothing, so
    a single unusable value must not empty the token set."""
    rec = {**CASE, "claim_amount": None}
    facets, _ = derive_facets(rec, SIG, signals_ran=True)
    assert len(facets) >= 4, f"lost the other families: {facets}"
    assert facets != []


def test_malformed_facet_spec_is_skipped_not_fatal():
    sig = {"facets": [{"family": "BAD FAMILY", "kind": "enum"},
                      {"family": "ok", "kind": "nonsense"},
                      {"family": "loss_type", "kind": "enum",
                       "from_column": "loss_type", "values": ["theft"]}]}
    facets, _ = derive_facets({"loss_type": "theft"}, sig, signals_ran=True)
    assert facets == ["loss_type:theft"]


def test_no_signature_yields_only_domain_facets():
    facets, unknown = derive_facets(CASE, None, domain={"country": "IN"})
    assert facets == ["country:in"] and unknown == []


# ── accessors tolerate every shape a caller holds ────────────────────────────
def test_signature_of_accepts_mongo_doc_and_bare_spec():
    assert signature_of({"app_spec": {"case_signature": SIG}})["version"] == 1
    assert signature_of({"case_signature": SIG})["version"] == 1
    assert signature_of({"app_spec": {}}) is None
    assert signature_of(None) is None


def test_learning_defaults_keep_apps_on_the_legacy_path():
    cfg = learning_config(SIG)
    assert cfg["mode"] == "summary"          # opt-in, never auto-switched
    assert cfg["promotion_min_officers"] == 3
    assert cfg["clause_budget_words"] == 1000


def test_reason_codes_extracted():
    assert reason_codes(SIG) == ["evidence_insufficient", "exclusion_applies",
                                 "other"]


# ── CS-01 publish validation ─────────────────────────────────────────────────
def _app(sig_dict, *, columns=None):
    from models import AppSpec

    spec = {"spec_version": "v0", "slug": "motor-claims", "title": "Motor",
            "headless": True, "agent_id": "ag_motor", "case_signature": sig_dict}
    app = AppSpec.model_validate(spec)
    if columns is not None:
        from models import DatasetDirectoryColumn, DatasetDirectoryEntry

        app.dataset_directory = [DatasetDirectoryEntry(
            dataset_id="claims.motor", source_id="s1", source_name="Claims",
            columns=[DatasetDirectoryColumn(name=n, type=t)
                     for n, t in columns.items()])]
    return app


_GOOD_COLS = {
    "loss_type": "string", "claim_amount": "number",
    "police_report_no": "string", "policy_start_date": "timestamp",
    "loss_date": "timestamp",
}


def test_cs01_passes_a_well_formed_signature():
    from publish_validators import validate_case_signature

    assert validate_case_signature(_app(SIG, columns=_GOOD_COLS)) == []


def test_cs01_absent_signature_is_valid():
    from models import AppSpec
    from publish_validators import validate_case_signature

    app = AppSpec.model_validate({"spec_version": "v0", "slug": "no-signature",
                                  "title": "A", "headless": True,
                                  "agent_id": "ag_a"})
    assert validate_case_signature(app) == []


def test_cs01_rejects_unknown_column():
    from publish_validators import validate_case_signature

    sig = {**SIG, "facets": [{"family": "loss_type", "kind": "enum",
                              "from_column": "nope", "values": ["theft"]}]}
    errs = validate_case_signature(_app(sig, columns=_GOOD_COLS))
    assert [e["code"] for e in errs] == ["case_signature_unknown_column"]


def test_cs01_rejects_band_on_a_string_column():
    from publish_validators import validate_case_signature

    sig = {**SIG, "facets": [{"family": "amt", "kind": "band",
                              "from_column": "loss_type", "edges": [10]}]}
    errs = validate_case_signature(_app(sig, columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_type_mismatch" for e in errs)


def test_cs01_rejects_unknown_signal():
    from publish_validators import validate_case_signature

    sig = {**SIG, "facets": [{"family": "x", "kind": "signal",
                              "signal_id": "invented_signal"}]}
    errs = validate_case_signature(_app(sig, columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_unknown_signal" for e in errs)


def test_cs01_rejects_enum_without_declared_values():
    from publish_validators import validate_case_signature

    sig = {**SIG, "facets": [{"family": "loss_type", "kind": "enum",
                              "from_column": "loss_type"}]}
    errs = validate_case_signature(_app(sig, columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_unknown_column" for e in errs)


def test_cs01_rejects_non_increasing_edges():
    from publish_validators import validate_case_signature

    sig = {**SIG, "facets": [{"family": "amt", "kind": "band",
                              "from_column": "claim_amount",
                              "edges": [100, 100, 50]}]}
    errs = validate_case_signature(_app(sig, columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_bad_bands" for e in errs)


def test_cs01_rejects_duplicate_family():
    from publish_validators import validate_case_signature

    f = {"family": "loss_type", "kind": "enum", "from_column": "loss_type",
         "values": ["theft"]}
    errs = validate_case_signature(_app({**SIG, "facets": [f, dict(f)]},
                                        columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_duplicate_family" for e in errs)


def test_cs01_no_longer_requires_a_reason_taxonomy():
    """reason_codes is DEPRECATED — an app declaring none publishes fine.

    It used to require at least two substantive codes. That floor now fails
    every newly built app, because the builder no longer authors a taxonomy:
    clustering partitions on contested_fields (derived from the override delta)
    and scopes judgements by facets, so a hand-picked label influences nothing.
    """
    from publish_validators import validate_case_signature

    assert validate_case_signature(_app({**SIG, "reason_codes": []},
                                        columns=_GOOD_COLS)) == []
    # an other-only taxonomy is likewise no longer an error
    assert validate_case_signature(
        _app({**SIG, "reason_codes": [{"code": "other", "label": "Other"}]},
             columns=_GOOD_COLS)) == []


def test_cs01_still_rejects_colliding_codes_on_legacy_specs():
    """Specs published before the removal still carry codes, and the guards
    that stop two lessons silently merging still apply to them."""
    from publish_validators import validate_case_signature

    errs = validate_case_signature(
        _app({**SIG, "reason_codes": [{"code": "dup", "label": "A"},
                                      {"code": "dup", "label": "B"}]},
             columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_thin_taxonomy" for e in errs)


def test_cs01_rejects_an_unusably_sparse_facet_space():
    from publish_validators import validate_case_signature

    facets = [{"family": f"f{i}", "kind": "enum", "from_column": "loss_type",
               "values": [f"v{j}" for j in range(9)]} for i in range(5)]
    errs = validate_case_signature(_app({**SIG, "facets": facets},
                                        columns=_GOOD_COLS))
    assert any(e["code"] == "case_signature_cardinality" for e in errs)


def test_cs01_skips_column_checks_when_directory_not_hydrated():
    from publish_validators import validate_case_signature

    # No dataset_directory ⇒ cannot verify columns; structural rules still run,
    # but a valid spec must not be rejected for lack of a directory.
    assert validate_case_signature(_app(SIG)) == []


# ── CS-03: a published signature cannot vanish on rebuild ────────────────────
#
# The builder re-authors the spec from scratch on a rebuild, so an omitted
# optional field is indistinguishable from a deliberate removal. For this field
# the two outcomes are wildly different: omission kills every clause the app has
# learned, silently, while they keep reading `active` in the store.


def _spec(sig=None):
    from models import AppSpec

    body = {"spec_version": "v0", "slug": "cs03-app", "title": "A", "headless": True,
            "agent_id": "ag_a"}
    if sig is not None:
        body["case_signature"] = sig
    return AppSpec.model_validate(body)


_PREV = {"case_signature": {"version": 1, "facets": [
    {"family": "product", "kind": "enum", "from_column": "product",
     "values": ["home"]},
    {"family": "amount_band", "kind": "band", "from_column": "amt",
     "edges": [1000]},
]}}


def test_cs03_dropping_a_published_signature_is_rejected():
    from publish_validators import validate_case_signature_stable

    errs = validate_case_signature_stable(_spec(None), _PREV)
    assert len(errs) == 1
    assert errs[0]["code"] == "case_signature_dropped_on_rebuild"
    # names what was lost, so the agent can restore it without guessing
    assert errs[0]["previous_families"] == ["amount_band", "product"]
    assert "SEED_APP_SPEC" in errs[0]["reason"]


def test_cs03_keeping_the_signature_passes():
    from publish_validators import validate_case_signature_stable

    assert validate_case_signature_stable(
        _spec(_PREV["case_signature"]), _PREV) == []


def test_cs03_changing_the_families_is_allowed():
    """Publish already reconciles a changed family set — migrating a renamed
    family through `aliases`, orphaning one that vanished. Both are visible, so
    CS-03 stays out of it and rejects only TOTAL loss."""
    from publish_validators import validate_case_signature_stable

    new = {"version": 1, "facets": [
        {"family": "product_type", "kind": "enum", "from_column": "product",
         "values": ["home"], "aliases": ["product"]}]}
    assert validate_case_signature_stable(_spec(new), _PREV) == []


def test_cs03_first_publish_has_nothing_to_lose():
    from publish_validators import validate_case_signature_stable

    assert validate_case_signature_stable(_spec(None), None) == []
    assert validate_case_signature_stable(_spec(None), {}) == []


def test_cs03_an_app_that_never_had_one_may_still_omit_it():
    """Adding a signature stays genuinely optional — CS-03 only defends one
    that already exists."""
    from publish_validators import validate_case_signature_stable

    assert validate_case_signature_stable(
        _spec(None), {"title": "A", "case_signature": None}) == []


# ── CS-04: the facet families must be confirmed by a human ───────────────────
#
# The families decide what "cases like this one" means for every judgement the
# app will ever learn. They were authored by the agent and, until this rule,
# nobody was required to look at them — the skill said "narrate it in the
# summary", which is prose that leaves no record.

_FACET = {"family": "product", "kind": "enum", "from_column": "product",
          "values": ["home"]}
_FACET2 = {"family": "amount_band", "kind": "band", "from_column": "amt",
           "edges": [1000]}


def _sig_spec(**sig):
    from models import AppSpec

    body = {"spec_version": "v0", "slug": "cs04-app", "title": "A",
            "headless": True, "agent_id": "ag_a"}
    if sig:
        body["case_signature"] = {"facets": [_FACET, _FACET2], **sig}
    return AppSpec.model_validate(body)


def test_cs04_a_signature_with_no_agreed_list_is_rejected():
    """No record of what the BA accepted — so the proposal never happened, or
    happened and was not carried through."""
    from publish_validators import validate_case_signature_confirmed

    errs = validate_case_signature_confirmed(_sig_spec(version=1))
    assert len(errs) == 1
    assert errs[0]["code"] == "case_signature_unconfirmed"
    # names what needs proposing, so the agent can show it rather than guess
    assert errs[0]["declared_families"] == ["amount_band", "product"]


def test_cs04_a_confirmed_signature_passes():
    from publish_validators import validate_case_signature_confirmed

    assert validate_case_signature_confirmed(_sig_spec(
        confirmed_families=["product", "amount_band"])) == []   # order-insensitive


def test_cs04_does_not_require_an_identity():
    """Deliberately not an identity check — who built the app is already in
    RBAC and the audit trail, and the governed question is who changes DATA."""
    from publish_validators import validate_case_signature_confirmed

    assert validate_case_signature_confirmed(_sig_spec(
        confirmed_families=["product", "amount_band"])) == []
    assert validate_case_signature_confirmed(_sig_spec(
        confirmed_by="anyone-at-all",
        confirmed_families=["product", "amount_band"])) == []


def test_cs04_naming_a_confirmer_without_the_list_is_still_rejected():
    """An identity alone records nothing about WHAT was agreed."""
    from publish_validators import validate_case_signature_confirmed

    errs = validate_case_signature_confirmed(_sig_spec(confirmed_by="ba@acme"))
    assert errs and errs[0]["code"] == "case_signature_unconfirmed"


def test_cs04_agreeing_one_set_and_shipping_another_is_rejected():
    """THE check. The BA said "drop amount_band" — and it shipped anyway. That
    divergence is invisible once the app is running, which is why it is the one
    thing publish refuses."""
    from publish_validators import validate_case_signature_confirmed

    errs = validate_case_signature_confirmed(_sig_spec(
        confirmed_families=["product"]))
    assert errs and errs[0]["code"] == "confirmed_families_mismatch"
    assert "amount_band" in errs[0]["reason"]      # says what was added
    assert errs[0]["confirmed_families"] == ["product"]


def test_cs04_an_app_with_no_signature_has_nothing_to_confirm():
    from publish_validators import validate_case_signature_confirmed

    assert validate_case_signature_confirmed(_sig_spec()) == []


def test_cs04_an_empty_agreed_list_does_not_count():
    from publish_validators import validate_case_signature_confirmed

    errs = validate_case_signature_confirmed(_sig_spec(confirmed_families=[]))
    assert errs and errs[0]["code"] == "case_signature_unconfirmed"


def test_confirmation_survives_a_round_trip_on_the_app_spec():
    """A field that validates in isolation and is dropped by AppSpec would make
    the whole rule inert — the same way the runtime key allowlist silently ate
    the factor scores."""
    from models import AppSpec

    spec = _sig_spec(confirmed_by="ba@acme",
                     confirmed_families=["product", "amount_band"])
    again = AppSpec.model_validate(spec.model_dump())
    assert again.case_signature.confirmed_by == "ba@acme"   # optional provenance
    assert sorted(again.case_signature.confirmed_families) == ["amount_band", "product"]
