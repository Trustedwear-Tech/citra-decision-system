# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Deterministic scorecard assembly — the arithmetic the model never touches.

See ``docs/factor-scorecard-plan.md``. The division of labour is the whole
point of this module and is not negotiable:

    the MODEL scores one factor at a time, with its evidence
    this CODE applies the weights, sums, bands and grades

Weights are never put in front of the model and the composite never passes
through it. A composite that cannot be reproduced from the same findings is one
a model-validation team will reject, and rightly — so everything here is a pure
function of (spec, findings).

We execute the customer's rubric. Nothing in this file may contain domain
vocabulary: it knows "factor", "band", "grade", and every user-facing word comes
from the declared ``terminology``.

Fail-loud: a malformed finding raises rather than scoring around the hole, and a
declared factor with no finding is reported as ``unscored`` rather than dropped —
a composite computed over four of six factors looks identical to a complete one
unless somebody says so.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from models import (
    FactorBand,
    FactorScorecard,
    FactorScoreRow,
    FactorSet,
    FactorSpec,
    GateResult,
    GateSpec,
)

log = logging.getLogger(__name__)


class FactorScoringError(ValueError):
    """A finding could not be reconciled with the declared rubric.

    Raised, not swallowed: a scorecard that silently omits or mis-scores a
    factor is worse than no scorecard, because it renders with exactly the same
    authority as a correct one."""


# ---------------------------------------------------------------------------
# SOP fingerprinting — did the policy move under the rubric?
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def sop_fingerprint(text: Optional[str]) -> Optional[str]:
    """Stable hash of an SOP passage, for detecting that the policy changed.

    **Whitespace-insensitive on purpose.** Reflowing a paragraph, re-wrapping a
    PDF extraction or a different chunk order is not a policy change, and a
    fingerprint that fired on those would be ignored within a week — at which
    point it protects nothing. Normalise, then hash, so what fires is a change
    in the WORDS.

    Case is preserved: "shall" and "may" matter, and so does a capitalised
    defined term. Returns None for empty input so a missing SOP records nothing
    rather than a hash of "".
    """
    if not text or not text.strip():
        return None
    normalised = _WS.sub(" ", text).strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Band assignment
# ---------------------------------------------------------------------------


def assign_band(factor: FactorSpec, score: Optional[float],
                reported: Optional[str]) -> Optional[str]:
    """The band for one factor.

    Score-based bands are assigned HERE, from the number — the model does not
    get a vote, so two cases with the same score always band the same way.
    Label-only bands come from the evaluator, because their thresholds live in
    the SOP's own units (days late, a ratio, a torque figure) which score space
    does not carry. A reported label that is not in the declared set is an
    error, never a new band."""
    declared = [b.label for b in factor.bands]

    # One declared band is the catch-all and there is nothing to choose. Assign
    # it rather than returning None, which would read as "not yet banded".
    if len(factor.bands) == 1:
        return declared[0]

    if factor.score_based_bands:
        if score is None:
            return None
        for band in factor.bands[:-1]:
            if band.max is not None and score <= band.max:
                return band.label
        return factor.bands[-1].label

    if reported is None:
        return None
    if reported not in declared:
        raise FactorScoringError(
            f"factor '{factor.id}': evaluator returned band {reported!r}, which "
            f"is not declared. Declared bands: {declared}. An undeclared band is "
            "drift, not a new category."
        )
    return reported


def grade_for(factor_set: FactorSet, percent: Optional[float]) -> Optional[str]:
    """Map a percentage of the maximum attainable score to a declared grade.

    Thresholds are on the PERCENTAGE, never the raw total, so a rubric whose
    weights sum to 60 or 120 grades correctly without anyone normalising by
    hand. ``grade_scale`` is validated ordered-descending with a catch-all last,
    so the loop always terminates on a declared grade."""
    if factor_set.mode != "composite" or percent is None:
        return None
    for step in factor_set.grade_scale[:-1]:
        if step.min is not None and percent >= step.min:
            return step.grade
    return factor_set.grade_scale[-1].grade


# ---------------------------------------------------------------------------
# Findings → rows
# ---------------------------------------------------------------------------


def _finding_key(finding: Dict[str, Any]) -> Optional[str]:
    """Which declared factor (or gate) a finding answers.

    ``factor_id`` is authoritative. ``item_type`` is the fallback because a
    ``check_evaluate`` tool names its ``task_type`` there, and an app whose tool
    task_types already match its factor ids needs no extra wiring."""
    for key in ("factor_id", "item_type", "item_id"):
        value = finding.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _index_findings(findings: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Last finding wins per id — a re-run of one check supersedes its earlier
    verdict within the same run, and that is the only ordering guarantee the
    runtime offers."""
    indexed: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        key = _finding_key(finding)
        if key:
            indexed[key] = finding
    return indexed


def _coerce_score(factor: FactorSpec, raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError) as exc:
        raise FactorScoringError(
            f"factor '{factor.id}': score {raw!r} is not a number"
        ) from exc
    if score < 0:
        raise FactorScoringError(f"factor '{factor.id}': score {score} is negative")
    if factor.weight is not None and score > factor.weight:
        raise FactorScoringError(
            f"factor '{factor.id}': score {score} exceeds its declared weight "
            f"{factor.weight}. A factor cannot score above its own scale."
        )
    return score


def _row_for(factor: FactorSpec, finding: Optional[Dict[str, Any]],
             mode: str) -> FactorScoreRow:
    if finding is None:
        return FactorScoreRow(
            factor_id=factor.id, label=factor.label, scope=factor.scope,
            weight=factor.weight, unscored=True,
            rationale="No finding was produced for this factor in this run.",
        )

    score = _coerce_score(factor, finding.get("score")) if mode == "composite" else None
    if mode == "composite" and score is None:
        # A finding arrived, but with no number. This USED to raise, which took
        # the whole run down — recommendation and planned writes with it — over
        # one factor. That is the wrong trade: `unscored` already exists, is
        # honest, is surfaced to the officer, and is excluded from the
        # denominator, so it is strictly better than either killing the run or
        # inventing a 0.
        #
        # It also aligns the two paths. In llm mode a missing score_fraction is
        # already refused at the tool (code=factor_not_scored), so no finding is
        # collected and the row lands here as `unscored` anyway. The raise only
        # ever fired for a finding that reached us scoreless by another route —
        # in practice a rule-mode check wired to a factor, which FS-06 now
        # rejects at publish. Loud in the log, visible on the card, not fatal.
        log.warning(
            "[SCORECARD] factor %r produced a finding with no score — treating "
            "it as UNSCORED. In composite mode a factor must come back with a "
            "number; a rule-mode check cannot supply one (see publish rule "
            "FS-06).", factor.id,
        )
        # Keep everything the evaluator DID produce. Only the number is
        # missing; throwing away a declared band, the clauses that fired and
        # the SOP fingerprint would hide work that was actually done — a
        # label-only factor can return a valid band without a score, and the
        # officer should see it even though it cannot be counted.
        _declared_fp = getattr(factor.sop, "fingerprint", None) if factor.sop else None
        _observed_fp = finding.get("sop_fingerprint")
        return FactorScoreRow(
            factor_id=factor.id, label=factor.label, scope=factor.scope,
            weight=factor.weight, unscored=True,
            band=assign_band(factor, None, finding.get("band")),
            rationale=(str(finding.get("rationale") or "")
                       or "This factor returned a finding but no score, so it "
                          "could not be counted."),
            citations=list(finding.get("citations") or []),
            clauses_fired=list(finding.get("clauses_fired") or []),
            sop_drift=bool(_declared_fp and _observed_fp
                           and _declared_fp != _observed_fp),
        )

    confidence = finding.get("confidence")
    # Drift is only claimed when BOTH sides exist. A factor that was never
    # fingerprinted, or a rule-mode check that fetches no SOP, reports nothing —
    # silence here means "not checked", and it must not read as "verified".
    declared_fp = getattr(factor.sop, "fingerprint", None) if factor.sop else None
    observed_fp = finding.get("sop_fingerprint")
    drift = bool(declared_fp and observed_fp and declared_fp != observed_fp)

    return FactorScoreRow(
        factor_id=factor.id,
        label=factor.label,
        scope=factor.scope,
        score=score,
        weight=factor.weight,
        band=assign_band(factor, score, finding.get("band")),
        rationale=str(finding.get("rationale") or ""),
        citations=list(finding.get("citations") or []),
        clauses_fired=list(finding.get("clauses_fired") or []),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        sop_drift=drift,
    )


def _gate_result(gate: GateSpec, finding: Optional[Dict[str, Any]]) -> GateResult:
    """A gate that produced no finding is FLAGGED, never passed.

    Mirrors ``check_evaluate`` mode='rule', where a rule error degrades to
    'flag'. The asymmetry is deliberate: an unevaluated gate must stop a case
    for a human, because the alternative is a policy limit that quietly stopped
    being enforced."""
    if finding is None:
        return GateResult(
            gate_id=gate.id, label=gate.label, status="flag",
            rationale="No verdict was produced for this gate in this run.",
        )
    raw = str(finding.get("recommendation") or finding.get("status") or "").lower()
    if raw in ("pass", "ok", "accept", "approve", "clear"):
        status = "pass"
    elif raw in ("fail", "reject", "decline", "breach"):
        status = "fail"
    else:
        status = "flag"
    return GateResult(
        gate_id=gate.id, label=gate.label, status=status,
        rationale=str(finding.get("rationale") or ""),
        citations=list(finding.get("citations") or []),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_scorecard(factor_set: Optional[FactorSet],
                    findings: Optional[Iterable[Dict[str, Any]]]) -> Optional[FactorScorecard]:
    """Assemble the scorecard for one run. Pure: same inputs, same output.

    Returns None when the app declares no rubric — the common case, and not an
    error. Most Decision Apps have prose reasons and no grid."""
    if factor_set is None:
        return None

    indexed = _index_findings(list(findings or []))

    gates = [_gate_result(g, indexed.get(g.id)) for g in factor_set.gates]
    gated = any(g.status in ("fail", "flag") for g in gates)

    rows = [_row_for(f, indexed.get(f.id), factor_set.mode) for f in factor_set.factors]
    unscored = [r.factor_id for r in rows if r.unscored]
    drifted = [r.factor_id for r in rows if r.sop_drift]

    card = FactorScorecard(
        mode=factor_set.mode,
        terminology=factor_set.terminology,
        gates=gates,
        gated=gated,
        rows=rows,
        unscored_factor_ids=unscored,
        sop_drift_factor_ids=drifted,
    )
    if drifted:
        # Loud, because the alternative is scoring against last year's policy
        # with nothing in the logs to say so. The caller marks the app for
        # re-extraction; scoring continues, because we cannot tell a material
        # edit from a typo and halting a portfolio on either would be worse.
        log.warning(
            "[SCORECARD] SOP drift — %d factor(s) were extracted against a "
            "policy passage that has since changed: %s", len(drifted), drifted,
        )

    if factor_set.mode != "composite":
        return card

    # A gate that failed or could not be evaluated suppresses the composite.
    # Showing "68/100 — declined" beneath a breached policy limit invites the
    # officer to argue with the number instead of reading the gate.
    if gated:
        log.info(
            "[SCORECARD] composite suppressed — gates not clear: %s",
            [g.gate_id for g in gates if g.status != "pass"],
        )
        return card

    _recompute_composite(card, factor_set)

    if unscored:
        log.warning(
            "[SCORECARD] composite computed over %d of %d factors — unscored: %s",
            len(rows) - len(unscored), len(rows), unscored,
        )
    return card


def _recompute_composite(card: FactorScorecard, factor_set: FactorSet) -> None:
    """Set total / max_total / percent / grade from the card's current rows.

    The ONE place the composite is computed, so a card assembled at /run and one
    recomputed after an officer override can never disagree about the
    arithmetic. Mutates in place; a gated or checklist card is left alone."""
    if factor_set.mode != "composite" or card.gated:
        return
    scored = [r for r in card.rows if not r.unscored]
    if not scored:
        return

    # Denominator is the ATTAINABLE maximum — the weights of the factors that
    # actually produced a finding. Dividing by the full rubric's maximum would
    # silently penalise a case for a missing factor, which is a data problem
    # masquerading as a credit one.
    total = sum(r.score or 0.0 for r in scored)
    max_total = sum(r.weight or 0.0 for r in scored)
    if max_total <= 0:
        raise FactorScoringError(
            "attainable maximum is zero — every scored factor has a "
            "non-positive weight, which the spec validator should have rejected"
        )

    card.total = round(total, 2)
    card.max_total = round(max_total, 2)
    card.percent = round(100.0 * total / max_total, 1)
    card.grade = grade_for(factor_set, card.percent)


# ---------------------------------------------------------------------------
# Officer override (docs/factor-scorecard-plan.md phase 4)
# ---------------------------------------------------------------------------


def stored_score(card: Dict[str, Any], factor_id: str) -> Optional[float]:
    """The score as STORED, read from the raw dict.

    Read before the parsed row is mutated — reading it afterwards would record
    the officer's number as the model's."""
    for row in (card.get("rows") or []):
        if row.get("factor_id") == factor_id:
            return row.get("score")
    return None


def apply_factor_override(
    card: Dict[str, Any],
    factor_set: FactorSet,
    *,
    factor_id: str,
    score: Optional[float] = None,
    band: Optional[str] = None,
    reason: str,
    actor: Optional[str],
    at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Apply one officer's correction to a stored scorecard and recompute.

    Takes and returns the card as a dict — it is stored denormalised on the
    staging row — but round-trips through the model so every field is validated
    exactly as the /run path validates it.

    Three properties this must not lose:

      * **The model's own score survives.** It moves to ``original_score`` on the
        FIRST override and is never touched again, so a second edit still shows
        what the AI said rather than what the last human did.
      * **The reason is mandatory.** An unexplained number is precisely the
        artefact this scorecard exists to replace.
      * **The pre-override grade is preserved**, stamped once, so the effect of
        human judgement on the grade stays legible however many edits follow.

    A gated card is refused: its composite is suppressed because a hard policy
    gate decided the case, and editing a factor underneath cannot change that.
    Refusing says so; accepting would look like it worked and change nothing.
    """
    reason = (reason or "").strip()
    if not reason:
        raise FactorScoringError(
            "an override needs a reason: an unexplained number is the artefact "
            "this scorecard exists to replace"
        )
    if score is None and band is None:
        raise FactorScoringError("an override must change a score or a band")
    if score is not None and factor_set.mode != "composite":
        raise FactorScoringError(
            f"mode='{factor_set.mode}' has no scores to override — a checklist "
            "records a band, not a number. Override the band instead."
        )
    if score is None and factor_set.mode == "composite":
        # The mirror of the rule above, and it closes a real hole. A composite
        # factor is counted by its NUMBER: the denominator is the weight of the
        # factors that produced one. Accepting a band with no score used to mark
        # the row scored, which put the full weight in the denominator against a
        # null — so an officer setting a favourable band DROPPED the case's
        # grade. (Measured: 8/10 = 80% grade A became 8/30 = 26.7% grade C when
        # a 20-weight factor was banded "growing".) That is the same "a missing
        # number must never become a 0" failure this module refuses everywhere
        # else, arriving through the officer instead of the model.
        #
        # It is also meaningless for a score-based factor, where assign_band
        # DERIVES the band from the score and returns None without one — the
        # override would have changed nothing but the row's status.
        raise FactorScoringError(
            "a composite factor is counted by its score, so an override must "
            "supply one — a band alone would put this factor's full weight into "
            "the total with nothing against it, and lower the grade. Give the "
            "score you think it deserves (you may set the band with it)."
        )

    model = FactorScorecard.model_validate(card)
    if model.gated:
        raise FactorScoringError(
            "this case is gated — a hard policy gate decided it, so the "
            "composite is suppressed and overriding a factor beneath it would "
            "change nothing. Address the gate."
        )

    spec = next((f for f in factor_set.factors if f.id == factor_id), None)
    if spec is None:
        raise FactorScoringError(
            f"factor '{factor_id}' is not in this app's declared rubric: "
            f"{[f.id for f in factor_set.factors]}"
        )
    row = next((r for r in model.rows if r.factor_id == factor_id), None)
    if row is None:
        raise FactorScoringError(f"factor '{factor_id}' has no row on this scorecard")

    # Captured BEFORE mutation, and only on the first override.
    first_override = row.overridden_by is None and row.original_score is None
    if first_override:
        row.original_score = stored_score(card, factor_id)

    if score is not None:
        # The same ceiling the model is held to. An officer cannot award a
        # factor more than its declared scale either — that would be a rubric
        # change made one case at a time.
        row.score = _coerce_score(spec, score)
        row.band = assign_band(spec, row.score, band or row.band)
        # An overridden factor is scored BY DEFINITION even if the model
        # produced nothing for it: the officer just supplied the judgement.
        row.unscored = False
    else:
        # CHECKLIST only — a composite band-only override is refused above.
        # Here there is no composite to distort, so the officer's disposition IS
        # the judgement: mark the row scored, or the card renders their own
        # decision beside the words "not scored" and `hide_unscored` drops the
        # one row the override has to be visible on.
        row.band = assign_band(spec, row.score, band)
        row.unscored = False

    row.override_reason = reason[:500]
    row.overridden_by = actor
    row.overridden_at = at or datetime.now(timezone.utc)

    if not model.overridden:
        model.grade_before_override = model.grade
        model.percent_before_override = model.percent
        model.overridden = True

    model.unscored_factor_ids = [r.factor_id for r in model.rows if r.unscored]
    model.revision = (model.revision or 0) + 1
    _recompute_composite(model, factor_set)
    log.info(
        "[SCORECARD] factor %r overridden by %s: %s -> %s (grade %s -> %s)",
        factor_id, actor, row.original_score, row.score,
        model.grade_before_override, model.grade,
    )
    return model.model_dump(mode="json")
