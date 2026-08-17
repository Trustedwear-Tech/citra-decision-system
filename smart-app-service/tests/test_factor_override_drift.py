# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Officer overrides and SOP drift — phases 4 and 5 of the factor scorecard.

The properties under test are the ones that keep an override honest:

  * the model's own score survives every subsequent edit
  * an override can never be invisible — the pre-override grade is preserved
  * the composite is recomputed by the SAME code that computed it at /run
  * a reason is mandatory, and the officer is held to the same score ceiling
  * drift FLAGS, it does not block, and it never claims a check it did not make
"""
from __future__ import annotations

import pytest

from factor_scoring import (
    FactorScoringError,
    apply_factor_override,
    build_scorecard,
    sop_fingerprint,
)
from models import FactorSet


def _fset(with_sop_fp: str | None = None) -> FactorSet:
    sop = {"source": "credit_policy", "query": "delay adverse"}
    if with_sop_fp:
        sop["fingerprint"] = with_sop_fp
    return FactorSet(
        mode="composite",
        factors=[
            dict(id="payment_record", label="Payment track record", weight=25,
                 reads=dict(dataset_id="anchor_invoices"), sop=sop,
                 bands=[dict(label="minor", max=10), dict(label="moderate", max=20),
                        dict(label="severe")]),
            dict(id="vintage", label="Vintage", weight=25,
                 reads=dict(dataset_id="dealers"),
                 bands=[dict(label="established", max=15), dict(label="new")]),
        ],
        grade_scale=[dict(min=80, grade="A"), dict(min=60, grade="B"), dict(grade="C")],
    )


def _card(fset: FactorSet, **finding_overrides):
    base = [
        dict(factor_id="payment_record", score=10, rationale="3 delays"),
        dict(factor_id="vintage", score=20, rationale="6 yrs"),
    ]
    for f in base:
        f.update(finding_overrides.get(f["factor_id"], {}))
    return build_scorecard(fset, base).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Phase 4 — the override
# ---------------------------------------------------------------------------


def test_override_recomputes_the_composite():
    fset = _fset()
    card = _card(fset)
    assert card["total"] == 30.0 and card["percent"] == 60.0 and card["grade"] == "B"

    out = apply_factor_override(card, fset, factor_id="payment_record",
                                score=20, reason="Q1 delays were the anchor's ERP error",
                                actor="officer@bank")
    assert out["total"] == 40.0
    assert out["percent"] == 80.0
    assert out["grade"] == "A"


def test_the_models_own_score_survives():
    fset = _fset()
    out = apply_factor_override(_card(fset), fset, factor_id="payment_record",
                                score=20, reason="anchor ERP error", actor="a@b")
    row = {r["factor_id"]: r for r in out["rows"]}["payment_record"]
    assert row["score"] == 20.0
    assert row["original_score"] == 10.0
    assert row["overridden_by"] == "a@b"
    assert row["override_reason"] == "anchor ERP error"
    assert row["overridden_at"]


def test_a_second_edit_still_shows_what_the_model_said():
    """original_score is written once. Otherwise the second override records the
    FIRST officer's number as the model's, and the AI's judgement is lost."""
    fset = _fset()
    once = apply_factor_override(_card(fset), fset, factor_id="payment_record",
                                 score=20, reason="first", actor="a@b")
    twice = apply_factor_override(once, fset, factor_id="payment_record",
                                  score=15, reason="second, on review", actor="c@d")
    row = {r["factor_id"]: r for r in twice["rows"]}["payment_record"]
    assert row["score"] == 15.0
    assert row["original_score"] == 10.0          # still the MODEL's
    assert row["overridden_by"] == "c@d"


def test_an_override_can_never_be_invisible():
    fset = _fset()
    out = apply_factor_override(_card(fset), fset, factor_id="payment_record",
                                score=20, reason="anchor ERP error", actor="a@b")
    assert out["overridden"] is True
    assert out["grade_before_override"] == "B"
    assert out["percent_before_override"] == 60.0
    assert out["grade"] == "A"                     # both are on the card


def test_the_pre_override_grade_is_stamped_once():
    """Two edits must still report the ORIGINAL grade, not the one after edit 1."""
    fset = _fset()
    once = apply_factor_override(_card(fset), fset, factor_id="payment_record",
                                 score=20, reason="first", actor="a@b")
    twice = apply_factor_override(once, fset, factor_id="vintage",
                                  score=25, reason="second", actor="a@b")
    assert twice["grade_before_override"] == "B"
    assert twice["percent_before_override"] == 60.0


def test_reason_is_mandatory():
    fset = _fset()
    with pytest.raises(FactorScoringError, match="needs a reason"):
        apply_factor_override(_card(fset), fset, factor_id="payment_record",
                              score=20, reason="   ", actor="a@b")


def test_the_officer_is_held_to_the_same_ceiling_as_the_model():
    """Awarding above the declared weight would be a rubric change made one
    case at a time."""
    fset = _fset()
    with pytest.raises(FactorScoringError, match="exceeds its declared weight"):
        apply_factor_override(_card(fset), fset, factor_id="payment_record",
                              score=99, reason="generous", actor="a@b")
    with pytest.raises(FactorScoringError, match="negative"):
        apply_factor_override(_card(fset), fset, factor_id="payment_record",
                              score=-1, reason="harsh", actor="a@b")


def test_override_reassigns_the_band_from_the_new_score():
    fset = _fset()
    out = apply_factor_override(_card(fset), fset, factor_id="payment_record",
                                score=20, reason="anchor ERP error", actor="a@b")
    row = {r["factor_id"]: r for r in out["rows"]}["payment_record"]
    assert row["band"] == "moderate"               # 20 <= 20, code-assigned


def test_unknown_factor_is_rejected():
    fset = _fset()
    with pytest.raises(FactorScoringError, match="not in this app's declared rubric"):
        apply_factor_override(_card(fset), fset, factor_id="not_a_factor",
                              score=1, reason="x", actor="a@b")


def test_a_gated_case_refuses_the_override():
    """The gate decided the case; editing a factor beneath it changes nothing,
    so saying no beats appearing to work."""
    fset = FactorSet(
        mode="composite",
        factors=[dict(id="a", label="A", weight=10, reads=dict(dataset_id="d"),
                      bands=[dict(label="ok")])],
        gates=[dict(id="cap", label="Exposure cap")],
        grade_scale=[dict(min=50, grade="A"), dict(grade="B")])
    card = build_scorecard(fset, [
        dict(factor_id="a", score=5),
        dict(item_type="cap", recommendation="fail", rationale="14% vs 10%"),
    ]).model_dump(mode="json")
    assert card["gated"] is True
    with pytest.raises(FactorScoringError, match="gated"):
        apply_factor_override(card, fset, factor_id="a", score=9,
                              reason="mitigants", actor="a@b")


def test_overriding_an_unscored_factor_scores_it():
    """The officer just supplied the judgement the model failed to produce, so
    the factor re-enters the denominator."""
    fset = _fset()
    card = build_scorecard(fset, [dict(factor_id="vintage", score=20)]).model_dump(mode="json")
    assert card["unscored_factor_ids"] == ["payment_record"]
    assert card["max_total"] == 25.0

    out = apply_factor_override(card, fset, factor_id="payment_record",
                                score=12, reason="scored by hand from the invoices",
                                actor="a@b")
    assert out["unscored_factor_ids"] == []
    assert out["max_total"] == 50.0
    assert out["total"] == 32.0


def test_checklist_rejects_a_score_override():
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="corrosion", label="Corrosion", reads=dict(dataset_id="d"),
                      bands=[dict(label="within_limits"), dict(label="exceeds")])])
    card = build_scorecard(fset, [dict(factor_id="corrosion", band="within_limits")]
                           ).model_dump(mode="json")
    with pytest.raises(FactorScoringError, match="no scores to override"):
        apply_factor_override(card, fset, factor_id="corrosion", score=5,
                              reason="x", actor="a@b")


def test_checklist_band_override_works():
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="corrosion", label="Corrosion", reads=dict(dataset_id="d"),
                      bands=[dict(label="within_limits"), dict(label="exceeds")])])
    card = build_scorecard(fset, [dict(factor_id="corrosion", band="within_limits")]
                           ).model_dump(mode="json")
    out = apply_factor_override(card, fset, factor_id="corrosion", band="exceeds",
                                reason="borescope shows through-thickness", actor="a@b")
    assert out["rows"][0]["band"] == "exceeds"
    assert out["overridden"] is True
    assert out["total"] is None                    # still no composite


def test_a_band_override_marks_the_row_scored():
    """Same rule as the score path: the officer has just decided the row, so it
    is no longer 'not scored'. Left unscored the card renders their own
    disposition beside the words "not scored", and a panel with hide_unscored
    would drop the row they just filled in — the one place the override has to
    be visible."""
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="corrosion", label="Corrosion", reads=dict(dataset_id="d"),
                      bands=[dict(label="within_limits"), dict(label="exceeds")]),
                 dict(id="torque", label="Torque", reads=dict(dataset_id="d"),
                      bands=[dict(label="pass"), dict(label="fail")])])
    # Nothing came back for `corrosion` — it starts life unscored.
    card = build_scorecard(fset, [dict(factor_id="torque", band="pass")]
                           ).model_dump(mode="json")
    assert card["unscored_factor_ids"] == ["corrosion"]

    out = apply_factor_override(card, fset, factor_id="corrosion", band="exceeds",
                                reason="borescope shows through-thickness",
                                actor="a@b")
    row = next(r for r in out["rows"] if r["factor_id"] == "corrosion")
    assert row["band"] == "exceeds"
    assert row["unscored"] is False
    assert out["unscored_factor_ids"] == []


def test_an_undeclared_band_cannot_be_introduced_by_override():
    fset = FactorSet(
        mode="checklist",
        factors=[dict(id="corrosion", label="Corrosion", reads=dict(dataset_id="d"),
                      bands=[dict(label="within_limits"), dict(label="exceeds")])])
    card = build_scorecard(fset, [dict(factor_id="corrosion", band="within_limits")]
                           ).model_dump(mode="json")
    with pytest.raises(FactorScoringError, match="not declared"):
        apply_factor_override(card, fset, factor_id="corrosion", band="probably_fine",
                              reason="looks ok", actor="a@b")


def test_override_must_change_something():
    fset = _fset()
    with pytest.raises(FactorScoringError, match="must change a score or a band"):
        apply_factor_override(_card(fset), fset, factor_id="payment_record",
                              reason="just noting", actor="a@b")


# ---------------------------------------------------------------------------
# Phase 5 — SOP drift
# ---------------------------------------------------------------------------


def test_fingerprint_ignores_whitespace_but_not_words():
    """A fingerprint that fired on a re-flowed PDF extraction would be muted
    within a week, at which point it protects nothing."""
    assert sop_fingerprint("Delays  beyond\n7 days") == sop_fingerprint("Delays beyond 7 days")
    assert sop_fingerprint("shall be treated") != sop_fingerprint("may be treated")
    assert sop_fingerprint("") is None
    assert sop_fingerprint(None) is None
    # Case is preserved — a capitalised defined term is not the same word.
    assert sop_fingerprint("Adverse") != sop_fingerprint("adverse")


def test_drift_is_detected_and_reported_but_does_not_block():
    fset = _fset(with_sop_fp=sop_fingerprint("Delays beyond 7 days are adverse"))
    card = build_scorecard(fset, [
        dict(factor_id="payment_record", score=10,
             sop_fingerprint=sop_fingerprint("Delays beyond 14 days are adverse")),
        dict(factor_id="vintage", score=20),
    ])
    assert card.sop_drift_factor_ids == ["payment_record"]
    assert {r.factor_id: r.sop_drift for r in card.rows} == {
        "payment_record": True, "vintage": False}
    # Scoring continues — we cannot tell a material edit from a typo, and
    # halting a portfolio on either would be worse than flagging.
    assert card.total == 30.0 and card.grade == "B"


def test_matching_fingerprints_report_no_drift():
    fp = sop_fingerprint("Delays beyond 7 days are adverse")
    fset = _fset(with_sop_fp=fp)
    card = build_scorecard(fset, [
        dict(factor_id="payment_record", score=10, sop_fingerprint=fp),
        dict(factor_id="vintage", score=20),
    ])
    assert card.sop_drift_factor_ids == []


def test_silence_is_never_read_as_verified():
    """Drift is only claimed when BOTH sides exist. A factor that was never
    fingerprinted, or a rule-mode check that fetches no SOP, must report
    nothing — 'not checked' must not render as 'checked and fine'."""
    # declared, never observed (e.g. rule mode)
    fset = _fset(with_sop_fp="abc123")
    card = build_scorecard(fset, [dict(factor_id="payment_record", score=10),
                                  dict(factor_id="vintage", score=20)])
    assert card.sop_drift_factor_ids == []

    # observed, never declared (rubric predates fingerprinting)
    fset2 = _fset()
    card2 = build_scorecard(fset2, [
        dict(factor_id="payment_record", score=10, sop_fingerprint="zzz"),
        dict(factor_id="vintage", score=20)])
    assert card2.sop_drift_factor_ids == []


def test_drift_survives_an_override():
    """An officer correcting a score does not make the stale policy current."""
    fset = _fset(with_sop_fp=sop_fingerprint("original policy text"))
    card = build_scorecard(fset, [
        dict(factor_id="payment_record", score=10,
             sop_fingerprint=sop_fingerprint("edited policy text")),
        dict(factor_id="vintage", score=20),
    ]).model_dump(mode="json")
    out = apply_factor_override(card, fset, factor_id="payment_record",
                                score=18, reason="read the invoices myself", actor="a@b")
    assert out["sop_drift_factor_ids"] == ["payment_record"]
    assert {r["factor_id"]: r["sop_drift"] for r in out["rows"]}["payment_record"] is True


def test_revision_increments_so_a_concurrent_edit_can_be_detected():
    """Two officers correcting different factors on one case both read the same
    card. Without a revision the second write replaces the first and the
    correction vanishes having appeared to save — the exact failure class this
    feature exists to prevent. The endpoint makes its write conditional on the
    revision it read; this asserts the token actually moves."""
    fset = _fset()
    card = _card(fset)
    assert card["revision"] == 0

    once = apply_factor_override(card, fset, factor_id="payment_record",
                                 score=20, reason="first", actor="a@b")
    assert once["revision"] == 1

    twice = apply_factor_override(once, fset, factor_id="vintage",
                                  score=22, reason="second", actor="c@d")
    assert twice["revision"] == 2
    # Both corrections survive — this is what the guard protects.
    rows = {r["factor_id"]: r for r in twice["rows"]}
    assert rows["payment_record"]["score"] == 20.0
    assert rows["vintage"]["score"] == 22.0


def test_a_fingerprint_without_doc_path_is_rejected_at_publish():
    """In query mode the runtime hashes a top-k retrieval, which moves when the
    index is rebuilt or an unrelated document outranks a chunk. A fingerprint
    there would sit in the spec looking like a live guarantee while checking
    nothing — or crying wolf forever."""
    from publish_validators import validate_factor_set

    class _App:
        def __init__(self, fs):
            self.factor_set = fs
            self.dataset_directory = []

    errs = validate_factor_set(_App(_fset(with_sop_fp="abc123")))
    assert len(errs) == 1
    assert errs[0]["code"] == "factor_fingerprint_without_doc_path"


def test_a_fingerprint_with_doc_path_passes():
    from publish_validators import validate_factor_set

    fs = FactorSet(
        mode="composite",
        factors=[dict(id="a", label="A", weight=10, reads=dict(dataset_id="d"),
                      sop=dict(source="policy", doc_path="/policy/credit.pdf",
                               fingerprint="abc123"),
                      bands=[dict(label="ok")])],
        grade_scale=[dict(min=50, grade="A"), dict(grade="B")])

    class _App:
        def __init__(self, f):
            self.factor_set = f
            self.dataset_directory = []

    assert validate_factor_set(_App(fs)) == []


def test_a_band_only_override_on_a_composite_is_refused():
    """The regression this guards.

    A composite factor is counted by its NUMBER — the denominator is the weight
    of the factors that produced one. Accepting a band with no score marked the
    row scored, which put the full weight into the denominator against a null,
    so an officer setting a FAVOURABLE band dropped the case's grade. Measured
    before the fix: 8/10 = 80% grade A became 8/30 = 26.7% grade C when a
    20-weight factor was banded "growing".

    It is also meaningless for a score-based factor: assign_band derives the
    band from the score and returns None without one, so the override changed
    nothing except the row's status."""
    fset = _fset()
    card = build_scorecard(fset, [dict(factor_id="vintage", score=20)]).model_dump(mode="json")
    before = (card["total"], card["max_total"], card["grade"])

    with pytest.raises(FactorScoringError, match="counted by its score"):
        apply_factor_override(card, fset, factor_id="payment_record",
                              band="minor", reason="looks fine from the invoices",
                              actor="a@b")
    # and the card is untouched by the refused override
    assert (card["total"], card["max_total"], card["grade"]) == before


def test_a_composite_override_with_a_score_still_works():
    """The refusal above must not block the legitimate move."""
    fset = _fset()
    card = build_scorecard(fset, [dict(factor_id="vintage", score=20)]).model_dump(mode="json")
    out = apply_factor_override(card, fset, factor_id="payment_record", score=20,
                                band="minor", reason="scored by hand from the invoices",
                                actor="a@b")
    row = next(r for r in out["rows"] if r["factor_id"] == "payment_record")
    assert row["score"] == 20 and row["unscored"] is False
    assert out["max_total"] == 50.0        # both factors now count
