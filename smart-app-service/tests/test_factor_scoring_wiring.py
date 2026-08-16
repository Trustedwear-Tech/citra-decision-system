"""The seam: does what `check_evaluate` EMITS actually score?

This file exists because the other two factor test files did not cover it.
They fed `build_scorecard` hand-written finding dicts that already carried a
`score`, so the aggregator was well tested against fixtures that nothing in the
system produced. `check_evaluate` never set `score`, so on its first real run
every composite app would have marked EVERY factor unscored and rendered a
grade over an empty rubric.

So the rule here: drive the REAL producer, feed its REAL output to the REAL
aggregator, and assert on the composite. No hand-written findings.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from factor_scoring import build_scorecard
from models import AgentSpec, AppSpec, CheckEvaluateTool, FactorSet
from tools_v2_dispatch import build_openai_tools_from_tools_v2, dispatch_tools_v2_call


def _settings() -> Settings:
    return Settings(sandbox_host_secret="x", jwt_secret="y")


def _app(mode: str = "composite") -> AppSpec:
    # headless: this fixture exercises the SCORING path, not the UI. An AppSpec
    # must declare panels or be headless, and headless is the honest choice here.
    common = dict(slug="dealer-limit-review", title="Dealer limit review",
                  spec_version="v0", headless=True, agent_id="a1")
    if mode == "composite":
        fset = FactorSet(
            mode="composite",
            factors=[
                dict(id="payment_record", label="Payment track record", weight=25,
                     reads=dict(dataset_id="anchor_invoices"),
                     bands=[dict(label="minor", max=10), dict(label="moderate", max=20),
                            dict(label="severe")]),
                dict(id="vintage", label="Vintage", weight=15,
                     reads=dict(dataset_id="dealers"),
                     bands=[dict(label="established", max=10), dict(label="new")]),
            ],
            grade_scale=[dict(min=80, grade="A"), dict(min=60, grade="B"),
                         dict(grade="C")])
    else:
        fset = FactorSet(
            mode="checklist",
            factors=[dict(id="corrosion", label="Corrosion within limits",
                          reads=dict(dataset_id="inspections"),
                          bands=[dict(label="within_limits"), dict(label="conditional"),
                                 dict(label="exceeds")])])
    return AppSpec(**common, factor_set=fset)


def _agent(tool: CheckEvaluateTool) -> AgentSpec:
    return AgentSpec(spec_version="v0", agent_id="a1", name="a",
                     system_prompt="p", tools_v2=[tool])


def _run(tool, app_spec, canned, args=None):
    agent = _agent(tool)
    s = _settings()
    _openai, table = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=app_spec, settings=s)
    import runtime as _rt
    with patch.object(_rt, "_call_llm", AsyncMock(return_value={"content": json.dumps(canned)})):
        return asyncio.run(dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=app_spec, dispatch_table=table,
            tool_name=tool.name,
            arguments=args or {"data": {"delays": 3}, "query": "payment conduct"},
            auth_header=None))


# ---------------------------------------------------------------------------
# The weight never reaches the model
# ---------------------------------------------------------------------------


def test_the_model_is_asked_for_a_fraction_never_the_weight():
    """The composite has to be reproducible from the DECLARED weights alone, and
    the weight has to live in exactly one place. Telling the model 'score out of
    25' would duplicate the rubric into the prompt, where it drifts from the
    spec the moment anyone re-weights."""
    tool = CheckEvaluateTool(name="pr", task_type="payment_record", mode="llm")
    agent = _agent(tool)
    s = _settings()
    _openai, table = build_openai_tools_from_tools_v2(
        agent_spec=agent, app_spec=_app(), settings=s)
    seen = {}

    async def _capture(*a, **kw):
        seen["messages"] = kw.get("messages") or (a[1] if len(a) > 1 else None)
        return {"content": '{"score_fraction":0.72,"recommendation":"pass",'
                           '"confidence":0.8,"rationale":"3 delays"}'}

    import runtime as _rt
    with patch.object(_rt, "_call_llm", _capture):
        asyncio.run(dispatch_tools_v2_call(
            settings=s, agent_spec=agent, app_spec=_app(), dispatch_table=table,
            tool_name="pr", arguments={"data": {"delays": 3}, "query": "q"},
            auth_header=None))

    prompt = json.dumps(seen["messages"])
    assert "score_fraction" in prompt          # asked for the fraction
    assert "out of 25" not in prompt           # never told the scale
    assert '"weight"' not in prompt


# ---------------------------------------------------------------------------
# Producer → aggregator, end to end
# ---------------------------------------------------------------------------


def test_a_check_evaluate_finding_actually_scores():
    """The regression this file was written for."""
    app = _app()
    pr = _run(CheckEvaluateTool(name="pr", task_type="payment_record", mode="llm"),
              app, {"score_fraction": 0.72, "recommendation": "pass",
                    "confidence": 0.8, "rationale": "3 delays in 12 months"})
    vt = _run(CheckEvaluateTool(name="vt", task_type="vintage", mode="llm"),
              app, {"score_fraction": 0.9, "recommendation": "pass",
                    "confidence": 0.7, "rationale": "6 years"})

    assert pr["factor_id"] == "payment_record"
    assert pr["score"] == 18.0                 # 0.72 * 25, applied in CODE
    assert vt["score"] == 13.5                 # 0.9 * 15

    card = build_scorecard(app.factor_set, [pr, vt])
    assert card.total == 31.5
    assert card.max_total == 40.0
    assert card.percent == 78.8
    assert card.grade == "B"
    rows = {r.factor_id: r for r in card.rows}
    assert rows["payment_record"].band == "moderate"   # 18 → code-assigned
    assert rows["payment_record"].confidence == 0.8    # kept SEPARATE from score


def test_a_missing_fraction_fails_loud_rather_than_scoring_zero():
    """Zero would silently downgrade the case and full would silently pass it;
    both render identically to a real judgement."""
    res = _run(CheckEvaluateTool(name="pr", task_type="payment_record", mode="llm"),
               _app(), {"recommendation": "pass", "confidence": 0.9,
                        "rationale": "looks fine"})
    assert res.get("code") == "factor_not_scored"
    assert "cannot be scored" in res["error"]


def test_a_fraction_outside_0_1_is_clamped_not_rejected():
    res = _run(CheckEvaluateTool(name="pr", task_type="payment_record", mode="llm"),
               _app(), {"score_fraction": 1.7, "recommendation": "pass",
                        "confidence": 0.9, "rationale": "x"})
    assert res["score"] == 25.0                # clamped to the weight, never above


def test_checklist_mode_asks_for_a_declared_band_and_scores_nothing():
    app = _app("checklist")
    res = _run(CheckEvaluateTool(name="c", task_type="corrosion", mode="llm"),
               app, {"band": "conditional", "recommendation": "flag",
                     "confidence": 0.6, "rationale": "pitting at station 480"},
               args={"data": {"depth_mm": 0.4}, "query": "corrosion"})
    assert res["factor_id"] == "corrosion"
    assert res["band"] == "conditional"
    assert res["score"] is None                # a checklist has no numbers

    card = build_scorecard(app.factor_set, [res])
    assert card.rows[0].band == "conditional"
    assert card.total is None and card.grade is None


def test_a_check_that_is_not_a_declared_factor_is_untouched():
    """An ordinary bureau/KYC check on an app that also has a rubric must keep
    behaving exactly as it did — no factor_id, no score, no extra prompt key."""
    res = _run(CheckEvaluateTool(name="kyc", task_type="kyc-match", mode="llm"),
               _app(), {"subject": "identity match", "recommendation": "pass",
                        "confidence": 0.95, "rationale": "name and DOB agree"})
    assert res["factor_id"] is None
    assert res["score"] is None
    assert res["recommendation"] == "pass"


def test_an_app_with_no_factor_set_is_untouched():
    plain = AppSpec(slug="plain-app", title="t", spec_version="v0", headless=True,
                    agent_id="a1")
    res = _run(CheckEvaluateTool(name="c", task_type="cibil-check", mode="llm"),
               plain, {"recommendation": "pass", "confidence": 0.9, "rationale": "ok"})
    assert res["factor_id"] is None and res["score"] is None
    assert not res.get("error")


def test_the_runtime_projection_carries_every_field_the_scorecard_reads():
    """The seam that actually broke it.

    runtime.execute_run collects a tool's ItemFinding through an explicit KEY
    ALLOWLIST. `score`, `band`, `factor_id`, `clauses_fired` and
    `sop_fingerprint` were absent from it, so the dispatch produced a perfectly
    good score and the runtime threw it away one line later — after which
    build_scorecard marked the factor unscored, pointing at the evaluator when
    the loss had happened in the projection.

    The wiring test above could not catch it: it hands build_scorecard the
    dispatch output directly and never crosses this seam. So assert the
    allowlist itself, from the source, against what the scorecard reads."""
    import inspect
    import re

    import runtime as _rt

    src = inspect.getsource(_rt.execute_run)
    start = src.index('_kind in ("image_analyze", "doc_extract", "check_evaluate"')
    window = src[start:start + 2500]
    listed = set(re.findall(r'"([a-z_0-9]+)"', window))

    required = {"factor_id", "score", "band", "clauses_fired", "sop_fingerprint"}
    missing = required - listed
    assert not missing, (
        f"the item-finding projection in runtime.execute_run drops {sorted(missing)} — "
        "build_scorecard reads them, so they must be carried through"
    )


# ---------------------------------------------------------------------------
# The queue projection — "no grade" is not "gated"
# ---------------------------------------------------------------------------


def test_a_gated_case_is_marked_gated_in_the_queue():
    from panel_data import project_scorecard_columns

    row = project_scorecard_columns({
        "scorecard": {"gated": True, "grade": None, "percent": None}})
    assert row["gated"] is True
    assert row["grade"] is None


def test_a_checklist_row_is_not_reported_as_gated():
    """The bug this pins.

    A checklist card has no grade BY DESIGN — there is no composite to compute.
    The queue used to infer "gated" from the empty grade, so every checklist row
    was labelled with a policy breach that never happened. `gated` is its own
    field on the card; an absent grade means nothing on its own."""
    from panel_data import project_scorecard_columns

    row = project_scorecard_columns({
        "scorecard": {"gated": False, "grade": None, "percent": None,
                      "mode": "checklist"}})
    assert row["gated"] is False
    assert row["grade"] is None


def test_an_all_unscored_composite_is_not_reported_as_gated():
    """Same shape, different cause: every factor came back without data, so
    there is no percentage and no grade — but no gate failed either."""
    from panel_data import project_scorecard_columns

    row = project_scorecard_columns({
        "scorecard": {"gated": False, "grade": None, "percent": None,
                      "total": 0.0, "max_total": 0.0}})
    assert row["gated"] is False


def test_a_graded_case_carries_its_grade_and_percent():
    from panel_data import project_scorecard_columns

    row = project_scorecard_columns({
        "scorecard": {"gated": False, "grade": "B", "percent": 72.0}})
    assert (row["grade"], row["score_percent"], row["gated"]) == ("B", 72.0, False)


def test_a_row_with_no_scorecard_gets_no_columns():
    """An app without a factor set must not sprout a `gated` column reading
    False on every row — that is a claim it never made."""
    from panel_data import project_scorecard_columns

    row = project_scorecard_columns({"case_natural_key": "c1"})
    assert "gated" not in row and "grade" not in row
