# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Factor scorecard — spec shape, publish rules, and the arithmetic.

See docs/factor-scorecard-plan.md. The properties under test are the ones that
make the feature safe to put in front of a credit team:

  * a composite is REPRODUCIBLE — pure function of (spec, findings), weights
    never seen by a model
  * checklist and composite are separate SHAPES, and the mode is PERMANENT
  * nothing is ever invented: an undeclared band, an over-weight score or a
    missing factor is surfaced, never smoothed over
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from factor_scoring import FactorScoringError, assign_band, build_scorecard, grade_for
from models import FactorSet, FactorSpec
from publish_validators import (
    validate_factor_checks_can_score,
    validate_factor_set,
    validate_factor_set_mode_stable,
)


# ---------------------------------------------------------------------------
# Fixtures — one composite rubric (dealer finance) and one checklist (aviation)
# ---------------------------------------------------------------------------


def _composite() -> FactorSet:
    return FactorSet(
        mode="composite",
        terminology={"panel": "Scorecard", "row": "factor", "band": "Band",
                     "composite": "Grade"},
        factors=[
            dict(id="payment_record", label="Payment track record", weight=25,
                 reads=dict(dataset_id="anchor_invoices",
                            where="dealer_id == {record.dealer_id}"),
                 bands=[dict(label="minor", max=10),
                        dict(label="moderate", max=20),
                        dict(label="severe")]),
            dict(id="vintage", label="Vintage", weight=10, scope="entity",
                 reads=dict(dataset_id="dealers"),
                 bands=[dict(label="established", max=5), dict(label="new")]),
            dict(id="requested_increase", label="Requested increase", weight=15,
                 scope="case", reads=dict(dataset_id="applications"),
                 bands=[dict(label="modest"), dict(label="aggressive")]),
        ],
        gates=[dict(id="exposure_cap", label="Single-dealer exposure cap")],
        grade_scale=[dict(min=80, grade="A"), dict(min=60, grade="B"),
                     dict(grade="C")],
    )


def _checklist() -> FactorSet:
    return FactorSet(
        mode="checklist",
        terminology={"panel": "Evaluation criteria", "row": "check",
                     "band": "Disposition"},
        factors=[
            dict(id="corrosion_limits", label="Corrosion within limits",
                 reads=dict(dataset_id="inspection_findings"),
                 bands=[dict(label="within_limits"), dict(label="conditional"),
                        dict(label="exceeds")]),
            dict(id="fastener_torque", label="Fastener torque within spec",
                 reads=dict(dataset_id="inspection_findings"),
                 bands=[dict(label="pass"), dict(label="fail")]),
        ],
    )


# ---------------------------------------------------------------------------
# Mode shape — composite and checklist are different objects
# ---------------------------------------------------------------------------


def test_composite_requires_a_weight_on_every_factor():
    with pytest.raises(ValidationError, match="requires a weight"):
        FactorSet(
            mode="composite",
            factors=[dict(id="a", label="A", weight=10,
                          reads=dict(dataset_id="d"), bands=[dict(label="ok")]),
                     dict(id="b", label="B",
                          reads=dict(dataset_id="d"), bands=[dict(label="ok")])],
            grade_scale=[dict(min=50, grade="A"), dict(grade="B")],
        )


def test_composite_requires_a_grade_scale():
    with pytest.raises(ValidationError, match="requires a grade_scale"):
        FactorSet(
            mode="composite",
            factors=[dict(id="a", label="A", weight=10,
                          reads=dict(dataset_id="d"), bands=[dict(label="ok")])],
        )


def test_checklist_forbids_weights():
    """A weight on a checklist means someone intended a total that will never
    be computed — that is a spec bug, not a harmless extra field."""
    with pytest.raises(ValidationError, match="forbids weights"):
        FactorSet(
            mode="checklist",
            factors=[dict(id="a", label="A", weight=10,
                          reads=dict(dataset_id="d"), bands=[dict(label="ok")])],
        )


def test_checklist_forbids_grade_scale():
    with pytest.raises(ValidationError, match="forbids grade_scale"):
        FactorSet(
            mode="checklist",
            factors=[dict(id="a", label="A",
                          reads=dict(dataset_id="d"), bands=[dict(label="ok")])],
            grade_scale=[dict(grade="A")],
        )


def test_grade_scale_must_descend_and_end_open():
    with pytest.raises(ValidationError, match="strictly DECREASING"):
        FactorSet(mode="composite",
                  factors=[dict(id="a", label="A", weight=10,
                                reads=dict(dataset_id="d"), bands=[dict(label="ok")])],
                  grade_scale=[dict(min=60, grade="B"), dict(min=80, grade="A"),
                               dict(grade="C")])
    with pytest.raises(ValidationError, match="catch-all"):
        FactorSet(mode="composite",
                  factors=[dict(id="a", label="A", weight=10,
                                reads=dict(dataset_id="d"), bands=[dict(label="ok")])],
                  grade_scale=[dict(min=80, grade="A"), dict(min=60, grade="B")])


def test_bands_may_not_mix_score_based_and_label_only():
    """Mixing them means nobody can say who decided the band."""
    with pytest.raises(ValidationError, match="ALL score-based"):
        FactorSpec(id="a", label="A", weight=10, reads=dict(dataset_id="d"),
                   bands=[dict(label="low", max=3), dict(label="mid"),
                          dict(label="high")])


def test_band_edges_must_strictly_increase():
    with pytest.raises(ValidationError, match="strictly.*increasing"):
        FactorSpec(id="a", label="A", weight=10, reads=dict(dataset_id="d"),
                   bands=[dict(label="x", max=8), dict(label="y", max=3),
                          dict(label="z")])


def test_duplicate_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate factor ids"):
        FactorSet(mode="checklist",
                  factors=[dict(id="a", label="A", reads=dict(dataset_id="d"),
                                bands=[dict(label="ok")]),
                           dict(id="a", label="A2", reads=dict(dataset_id="d"),
                                bands=[dict(label="ok")])])


def test_gate_and_factor_may_not_share_an_id():
    with pytest.raises(ValidationError, match="share an id"):
        FactorSet(mode="checklist",
                  factors=[dict(id="shared", label="A", reads=dict(dataset_id="d"),
                                bands=[dict(label="ok")])],
                  gates=[dict(id="shared", label="G")])


# ---------------------------------------------------------------------------
# Aggregation — the arithmetic no model touches
# ---------------------------------------------------------------------------


def test_composite_totals_bands_and_grades():
    card = build_scorecard(_composite(), [
        dict(factor_id="payment_record", score=18, rationale="3 delays",
             confidence=0.82),
        dict(factor_id="vintage", score=9, rationale="6 yrs"),
        dict(factor_id="requested_increase", score=6, band="aggressive",
             rationale="+75%"),
        dict(item_type="exposure_cap", recommendation="pass", rationale="8.75%"),
    ])
    assert card.total == 33.0
    assert card.max_total == 50.0
    assert card.percent == 66.0
    assert card.grade == "B"
    assert card.gated is False
    by_id = {r.factor_id: r for r in card.rows}
    assert by_id["payment_record"].band == "moderate"   # 18 > 10, <= 20
    assert by_id["vintage"].band == "new"               # 9 > 5 → catch-all
    assert by_id["requested_increase"].band == "aggressive"  # evaluator's label
    assert by_id["payment_record"].confidence == 0.82


def test_scoring_is_reproducible():
    """Same spec, same findings, same answer — the property model validation
    actually tests for."""
    findings = [dict(factor_id="payment_record", score=18),
                dict(factor_id="vintage", score=9),
                dict(factor_id="requested_increase", score=6, band="modest"),
                dict(item_type="exposure_cap", recommendation="pass")]
    first = build_scorecard(_composite(), findings)
    second = build_scorecard(_composite(), list(findings))
    assert first.model_dump() == second.model_dump()


def test_percent_is_of_the_attainable_maximum_not_a_fixed_100():
    """A rubric whose weights sum to 50 must grade correctly without anyone
    normalising by hand."""
    fset = FactorSet(
        mode="composite",
        factors=[dict(id="a", label="A", weight=30, reads=dict(dataset_id="d"),
                      bands=[dict(label="ok")]),
                 dict(id="b", label="B", weight=20, reads=dict(dataset_id="d"),
                      bands=[dict(label="ok")])],
        grade_scale=[dict(min=80, grade="A"), dict(grade="B")])
    card = build_scorecard(fset, [dict(factor_id="a", score=27),
                                  dict(factor_id="b", score=18)])
    assert card.max_total == 50.0
    assert card.percent == 90.0
    assert card.grade == "A"


def test_a_failed_gate_suppresses_the_composite():
    """'68/100 — declined' invites the officer to argue with the number instead
    of reading the gate."""
    card = build_scorecard(_composite(), [
        dict(factor_id="payment_record", score=18),
        dict(factor_id="vintage", score=9),
        dict(factor_id="requested_increase", score=6, band="modest"),
        dict(item_type="exposure_cap", recommendation="fail",
             rationale="14% of anchor turnover, cap is 10%"),
    ])
    assert card.gated is True
    assert card.gates[0].status == "fail"
    assert card.total is None and card.percent is None and card.grade is None
    assert len(card.rows) == 3          # rows still render as supporting detail


def test_an_unevaluated_gate_flags_and_never_passes_silently():
    card = build_scorecard(_composite(), [
        dict(factor_id="payment_record", score=18),
        dict(factor_id="vintage", score=9),
        dict(factor_id="requested_increase", score=6, band="modest"),
    ])
    assert card.gates[0].status == "flag"
    assert card.gated is True
    assert card.grade is None


def test_a_missing_factor_is_surfaced_and_left_out_of_the_denominator():
    """Dividing by the full rubric would silently penalise the case for a data
    problem; dropping it silently would hide that the grade is partial."""
    fset = _composite()
    card = build_scorecard(fset, [
        dict(factor_id="payment_record", score=20),
        dict(factor_id="vintage", score=10),
        dict(item_type="exposure_cap", recommendation="pass"),
    ])
    assert card.unscored_factor_ids == ["requested_increase"]
    assert card.max_total == 35.0        # 25 + 10, NOT 50
    assert card.percent == pytest.approx(85.7, abs=0.1)
    row = {r.factor_id: r for r in card.rows}["requested_increase"]
    assert row.unscored is True and row.score is None


def test_checklist_renders_rows_with_no_total():
    card = build_scorecard(_checklist(), [
        dict(factor_id="corrosion_limits", band="within_limits",
             rationale="skin within allowable"),
        dict(factor_id="fastener_torque", band="fail", rationale="2 under-torqued"),
    ])
    assert card.mode == "checklist"
    assert card.total is None and card.percent is None and card.grade is None
    assert [r.band for r in card.rows] == ["within_limits", "fail"]
    assert card.terminology.panel == "Evaluation criteria"


def test_absent_factor_set_yields_no_scorecard():
    assert build_scorecard(None, [dict(factor_id="x", score=1)]) is None


# ---------------------------------------------------------------------------
# Nothing is invented
# ---------------------------------------------------------------------------


def test_undeclared_band_is_an_error_not_a_new_category():
    fset = _checklist()
    with pytest.raises(FactorScoringError, match="not declared"):
        build_scorecard(fset, [dict(factor_id="corrosion_limits", band="probably_fine"),
                               dict(factor_id="fastener_torque", band="pass")])


def test_score_above_the_declared_weight_is_an_error():
    with pytest.raises(FactorScoringError, match="exceeds its declared weight"):
        build_scorecard(_composite(), [
            dict(factor_id="payment_record", score=99),
            dict(factor_id="vintage", score=9),
            dict(factor_id="requested_increase", score=6, band="modest"),
            dict(item_type="exposure_cap", recommendation="pass"),
        ])


def test_composite_without_a_score_is_unscored_not_a_zero():
    """A missing number is neither a 0 nor a reason to kill the run.

    Scoring it 0 would silently downgrade the case. Raising — which is what this
    used to do — took the whole run down, recommendation and planned writes with
    it, over one factor. `unscored` is the honest third option: the factor is
    named on the card, left out of BOTH sides of the fraction, and the officer
    sees a composite over 2 of 3 factors rather than a confident wrong one."""
    card = build_scorecard(_composite(), [
        dict(factor_id="payment_record", rationale="looks fine to me"),
        dict(factor_id="vintage", score=9),
        dict(factor_id="requested_increase", score=6, band="modest"),
        dict(item_type="exposure_cap", recommendation="pass"),
    ])
    assert card.unscored_factor_ids == ["payment_record"]
    row = next(r for r in card.rows if r.factor_id == "payment_record")
    assert row.unscored is True
    assert row.score is None, "a scoreless finding must not become a 0"
    # ... and its weight is out of the denominator, not quietly counted against
    # the case: 9 + 6 over the ATTAINABLE 10 + 15, not over the full 50.
    assert (card.total, card.max_total) == (15.0, 25.0)


def test_negative_score_is_an_error():
    with pytest.raises(FactorScoringError, match="negative"):
        build_scorecard(_composite(), [
            dict(factor_id="payment_record", score=-1),
            dict(factor_id="vintage", score=9),
            dict(factor_id="requested_increase", score=6, band="modest"),
            dict(item_type="exposure_cap", recommendation="pass"),
        ])


def test_score_is_not_confidence():
    """Two separate quantities: a policy score out of the weight, and the
    model's self-reported certainty. A run that reports only confidence has NOT
    scored the factor — confidence must never be promoted into the composite,
    however convenient the number looks."""
    card = build_scorecard(_composite(), [
        dict(factor_id="payment_record", confidence=0.9),
        dict(factor_id="vintage", score=9),
        dict(factor_id="requested_increase", score=6, band="modest"),
        dict(item_type="exposure_cap", recommendation="pass"),
    ])
    row = next(r for r in card.rows if r.factor_id == "payment_record")
    assert row.unscored is True and row.score is None
    assert card.total == 15.0, "0.9 confidence must not have become 0.9 score"


def test_single_band_factor_is_assigned_not_left_blank():
    factor = FactorSpec(id="a", label="A", weight=5, reads=dict(dataset_id="d"),
                        bands=[dict(label="assessed")])
    assert assign_band(factor, 3, None) == "assessed"


def test_grade_for_returns_none_on_a_checklist():
    assert grade_for(_checklist(), 90.0) is None


# ---------------------------------------------------------------------------
# Publish rules
# ---------------------------------------------------------------------------


class _Entry:
    def __init__(self, dataset_id):
        self.dataset_id = dataset_id
        self.columns = []


class _App:
    def __init__(self, factor_set, datasets=()):
        self.factor_set = factor_set
        self.dataset_directory = [_Entry(d) for d in datasets]


def test_factor_reading_an_unbound_dataset_is_rejected():
    errs = validate_factor_set(_App(_composite(),
                                    datasets=("anchor_invoices", "dealers")))
    assert len(errs) == 1
    assert errs[0]["code"] == "factor_unknown_dataset"
    assert "applications" in errs[0]["reason"]


def test_dataset_checks_are_skipped_when_the_directory_is_not_hydrated():
    assert validate_factor_set(_App(_composite())) == []


def test_absent_factor_set_passes_publish():
    assert validate_factor_set(_App(None)) == []


def test_document_factor_is_not_dataset_checked():
    """For kind='document', `dataset_id` is the ATTACHMENT COLUMN on the anchor
    record — the field holding the PDF — not a dataset id. Checking it against
    the bound datasets rejected every document-reading factor (a bank statement,
    an uploaded invoice) the moment the directory was hydrated, which is the one
    state a real publish is always in."""
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="statement", label="Bank statement quality",
                      reads=dict(kind="document", dataset_id="statement_pdf"),
                      bands=[dict(label="ok")])])
    assert validate_factor_set(_App(fset, datasets=("dealers",))) == []


def test_document_factor_still_needs_a_column():
    """Exempt from the EXISTENCE check, not from naming what it reads."""
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="statement", label="Bank statement quality",
                      reads=dict(kind="document"), bands=[dict(label="ok")])])
    errs = validate_factor_set(_App(fset, datasets=("dealers",)))
    assert errs and errs[0]["code"] == "factor_reads_unbound"


def test_lookup_factor_must_name_its_tool():
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="bureau", label="Bureau standing",
                      reads=dict(kind="lookup"), bands=[dict(label="ok")])])
    errs = validate_factor_set(_App(fset, datasets=("d",)))
    assert errs and errs[0]["code"] == "factor_lookup_without_tool"


def test_mode_flip_on_a_published_app_is_rejected():
    errs = validate_factor_set_mode_stable(
        _App(_composite()), {"factor_set": {"mode": "checklist"}})
    assert len(errs) == 1
    assert errs[0]["code"] == "factor_set_mode_changed"


def test_same_mode_republish_is_fine():
    assert validate_factor_set_mode_stable(
        _App(_composite()), {"factor_set": {"mode": "composite"}}) == []


def test_adding_a_factor_set_to_an_app_that_had_none_is_allowed():
    assert validate_factor_set_mode_stable(_App(_composite()), {"title": "x"}) == []


def test_first_publish_has_nothing_to_preserve():
    assert validate_factor_set_mode_stable(_App(_composite()), None) == []


def test_removing_a_factor_set_is_allowed():
    """The app stops producing a grid, which is visible rather than silent."""
    assert validate_factor_set_mode_stable(
        _App(None), {"factor_set": {"mode": "composite"}}) == []

# ---------------------------------------------------------------------------
# FS-05 — the rubric you FOUND must be the rubric you DECLARE
# ---------------------------------------------------------------------------
#
# This rule used to be a regex over the app's prose. It fired correctly on the
# spec that prompted it and was then walked around by a builder whose own skill
# file documented the trigger words. These tests cover the replacement: a
# comparison between two things the builder STATED, with no vocabulary in
# between to paraphrase.


def _finding(verdict, **kw):
    from models import RubricFinding
    base = dict(source="sop_library_lending", doc_path="/policy/credit-v4.2.txt")
    if verdict != "none":
        base["evidence"] = dict(factors_named=6, weights_present=True,
                                grade_scale_present=True,
                                excerpt="4.3 Scored factors and weights … total 100 marks")
    else:
        base["reason"] = "eligibility gates and narrative guidance only; no scoring"
    base.update(kw)
    return RubricFinding(verdict=verdict, **base)


class _AppRF:
    def __init__(self, finding=None, factor_set=None):
        self.rubric_finding = finding
        self.factor_set = factor_set
        self.dataset_directory = []


def _checklist_set():
    return FactorSet(
        mode="checklist",
        factors=[dict(id="corrosion", label="Corrosion", reads=dict(dataset_id="d"),
                      bands=[dict(label="within_limits"), dict(label="exceeds")])])


def test_found_a_weighted_rubric_and_declared_nothing_is_blocked():
    """The exact miss this rule exists for: the builder read the policy,
    recorded a weighted rubric, and published with factor_set null — leaving
    the scoring as prose the model does arithmetic on."""
    from publish_validators import validate_rubric_finding_matches_declaration as v

    errs = v(_AppRF(_finding("weighted_rubric"), factor_set=None))
    assert len(errs) == 1
    assert errs[0]["code"] == "rubric_found_but_not_declared"
    assert "verdict='none'" in errs[0]["reason"]     # names the honest way out


def test_found_a_checklist_and_declared_nothing_is_blocked():
    from publish_validators import validate_rubric_finding_matches_declaration as v

    errs = v(_AppRF(_finding("criteria_checklist"), factor_set=None))
    assert len(errs) == 1
    assert errs[0]["code"] == "rubric_found_but_not_declared"


def test_a_matching_declaration_passes():
    from publish_validators import validate_rubric_finding_matches_declaration as v

    assert v(_AppRF(_finding("weighted_rubric"), factor_set=_composite())) == []
    assert v(_AppRF(_finding("criteria_checklist"), factor_set=_checklist_set())) == []


def test_shape_mismatch_is_blocked_in_both_directions():
    """A checklist over a weighted policy drops the grade the policy defines; a
    composite over a checklist invents weights nobody agreed to."""
    from publish_validators import validate_rubric_finding_matches_declaration as v

    errs = v(_AppRF(_finding("weighted_rubric"), factor_set=_checklist_set()))
    assert errs and errs[0]["code"] == "rubric_finding_mode_mismatch"
    assert "silently drops the grade" in errs[0]["reason"]

    errs = v(_AppRF(_finding("criteria_checklist"), factor_set=_composite()))
    assert errs and errs[0]["code"] == "rubric_finding_mode_mismatch"
    assert "invents weights" in errs[0]["reason"]


def test_verdict_none_passes_with_or_without_a_factor_set():
    """'No rubric in the document' plus a declared factor set is legitimate —
    the BA supplied the weights themselves. Blocking it would punish honesty."""
    from publish_validators import validate_rubric_finding_matches_declaration as v

    assert v(_AppRF(_finding("none"), factor_set=None)) == []
    assert v(_AppRF(_finding("none"), factor_set=_composite())) == []


def test_no_record_means_nothing_is_checked():
    """The honest limit of this rule, asserted so it is not mistaken for
    coverage: an app that never read a policy made no claim."""
    from publish_validators import validate_rubric_finding_matches_declaration as v

    assert v(_AppRF(finding=None, factor_set=None)) == []


def test_the_prose_heuristic_is_gone():
    """Deleted, not kept as a fallback. Two rules watching the same thing, one
    of them gameable, is worse than one — the gameable one shapes behaviour
    while looking like redundancy."""
    import publish_validators as pv

    assert not hasattr(pv, "validate_scoring_prose_without_factor_set")
    assert not hasattr(pv, "_FS05_WEIGHT_RE")
    assert not hasattr(pv, "_FS05_AGGREGATE_RE")


# ── the record itself ──────────────────────────────────────────────────────


def test_verdict_none_requires_a_reason():
    """'I read it and found no rubric' is only checkable — and only contestable
    by the BA — when it says why."""
    from models import RubricFinding

    with pytest.raises(ValidationError, match="requires a reason"):
        RubricFinding(source="s", verdict="none")


def test_a_positive_verdict_requires_evidence():
    from models import RubricFinding

    with pytest.raises(ValidationError, match="requires evidence"):
        RubricFinding(source="s", verdict="weighted_rubric")


def test_the_record_carries_no_fingerprint():
    """Deliberate. The SOP passage is re-read on every run, so the evidence is
    always current; only the weights are frozen, and those change through a
    committee rather than silently. This record is about what was found and
    declared, not about watching a document."""
    from models import RubricFinding

    assert "fingerprint" not in RubricFinding.model_fields


def test_the_record_survives_a_round_trip_on_the_app_spec():
    """It has to reach publish intact — a field that validates in isolation and
    is dropped by AppSpec would make the whole rule inert."""
    from models import AppSpec

    spec = AppSpec(slug="round-trip-app", title="t", spec_version="v0",
                   headless=True, agent_id="a1",
                   rubric_finding=_finding("weighted_rubric",
                                           confirmed_by="ba@acme").model_dump())
    again = AppSpec.model_validate(spec.model_dump())
    assert again.rubric_finding.verdict == "weighted_rubric"
    assert again.rubric_finding.confirmed_by == "ba@acme"
    assert again.rubric_finding.evidence.factors_named == 6


# ---------------------------------------------------------------------------
# FS-06 — a declared factor needs a check that can produce a number
# ---------------------------------------------------------------------------


class _Tool:
    def __init__(self, name, kind="check_evaluate", mode="llm", task_type=None):
        self.name = name
        self.kind = kind
        self.mode = mode
        self.task_type = task_type


class _Agent:
    def __init__(self, *tools):
        self.tools_v2 = list(tools)


def test_rule_mode_check_on_a_factor_is_blocked():
    """The failure it prevents is invisible at runtime.

    A mode='rule' check returns a verdict — pass/flag/fail — and no number. Wire
    it to a weighted factor and the finding arrives scoreless, so the factor is
    marked unscored and its weight drops out of the denominator on EVERY case.
    The grade still renders, confidently, over a rubric that is not the one the
    customer signed. Nothing in the run looks wrong; only publish can catch it."""
    errs = validate_factor_checks_can_score(
        _App(_composite()),
        _Agent(_Tool("check_payment", mode="rule", task_type="payment_record")))
    assert len(errs) == 1
    assert errs[0]["code"] == "rule_mode_check_cannot_score_a_factor"
    assert "payment_record" in errs[0]["reason"]


def test_llm_mode_check_on_a_factor_is_fine():
    assert validate_factor_checks_can_score(
        _App(_composite()),
        _Agent(_Tool("check_payment", mode="llm", task_type="payment_record"))) == []


def test_rule_mode_check_on_a_gate_is_fine():
    """A gate is exactly what rule mode is for — deterministic, pass/fail, no
    number wanted. Only DECLARED FACTORS are in scope."""
    assert validate_factor_checks_can_score(
        _App(_composite()),
        _Agent(_Tool("check_cap", mode="rule", task_type="exposure_cap"))) == []


def test_rule_mode_check_under_checklist_is_fine():
    """A checklist row carries a band, not a number, so a rule verdict can stand
    in for one. The rule is composite-only on purpose."""
    assert validate_factor_checks_can_score(
        _App(_checklist()),
        _Agent(_Tool("check_torque", mode="rule",
                     task_type="fastener_torque"))) == []


def test_fs06_is_inert_without_a_factor_set_or_agent():
    agent = _Agent(_Tool("c", mode="rule", task_type="payment_record"))
    assert validate_factor_checks_can_score(_App(None), agent) == []
    assert validate_factor_checks_can_score(_App(_composite()), None) == []
