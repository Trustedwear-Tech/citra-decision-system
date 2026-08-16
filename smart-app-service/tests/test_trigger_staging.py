"""Trigger → unified-staging path (recommendation-only).

These cover the migration where a SmartApp's recommendation is produced by the
app's OWN agent fired on a trigger (webhook / schedule / poll) and staged into
the SAME inbox as an on-demand /run — no workflow engine. They test the units
directly because the broader trigger tests are blocked by an unrelated publish
fixture, so this is the only coverage of:

  * main._stage_recommendation — builds + upserts the staging row, returns the
    composite correlation_id that routes Approve to the staging-replay path.
  * trigger_runner: a triggered run is plan_only (never auto-commits) and is
    STAGED (not written to pending_runs) when a stager is injected.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from types import SimpleNamespace

_TRIGGER_SETTINGS = SimpleNamespace(jwt_secret="test-secret", jwt_issuer="Citra-AI", jwt_algorithm="HS256")


JWT_SECRET = "smart-app-service-test-secret"
os.environ.setdefault("JWT_SECRET", JWT_SECRET)
os.environ.setdefault("JWT_ISSUER", "Citra-AI")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
import trigger_runner  # noqa: E402
from env_context import set_current_env  # noqa: E402
from models import AgentSpec, AppSpec, RunResponse, Trigger  # noqa: E402


class _FakeCol:
    """Captures replace_one / insert_one calls (async)."""

    def __init__(self) -> None:
        self.replaced: list = []
        self.inserted: list = []

    async def replace_one(self, flt, doc, upsert=False):
        self.replaced.append((flt, doc, upsert))

    async def insert_one(self, doc):
        self.inserted.append(doc)


# ---------------------------------------------------------------------------
# _stage_recommendation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_recommendation_upserts_and_returns_composite(monkeypatch):
    col = _FakeCol()
    monkeypatch.setattr(main, "get_workflow_staging_col", lambda: col)

    resp = RunResponse(
        correlation_id="run_abc",
        status="pending_approval",
        decision="Reassign to JE Patna",
        reasoning="SLA breached; nearest available officer.",
        outputs={"text": "evidence summary"},
        planned_writes=[{
            "source_id": "field_ops", "dataset_id": "field_ops.complaints",
            "action_id": "reassign", "payload": {"id": "C-1", "officer": "je_patna"},
            "idempotency_key": "k1",
        }],
    )

    composite = await main._stage_recommendation(
        response=resp,
        slug="complaints-triage",
        inputs={"claim_id": "C-1"},
        tenant_id="acme",
        published_by_user_id="system:complaints-triage",
        source="trigger",
    )

    # case_natural_key is derived from the record id in inputs (claim_id),
    # so a re-run on the same record upserts rather than duplicates.
    assert composite == "run_abc:C-1"
    assert len(col.replaced) == 1
    flt, doc, upsert = col.replaced[0]
    assert flt == {"workflow_execution_id": "run_abc", "case_natural_key": "C-1"}
    assert upsert is True
    assert doc["slug"] == "complaints-triage"
    assert doc["source"] == "trigger"
    assert doc["status"] == "pending_review"
    assert doc["planned_writes"] == resp.planned_writes
    assert doc["llm_recommendation_text"] == "Reassign to JE Patna"


@pytest.mark.asyncio
async def test_stage_recommendation_falls_back_to_run_id_without_record_key(monkeypatch):
    col = _FakeCol()
    monkeypatch.setattr(main, "get_workflow_staging_col", lambda: col)
    resp = RunResponse(correlation_id="run_xyz", status="pending_approval")

    composite = await main._stage_recommendation(
        response=resp, slug="s", inputs={"note": "no id here"},
        tenant_id="t", published_by_user_id="u",
    )

    # No id-like field → case key falls back to the run id.
    assert composite == "run_xyz:run_xyz"
    assert col.replaced[0][0]["case_natural_key"] == "run_xyz"


# ---------------------------------------------------------------------------
# trigger path: plan_only + stage (not pending_runs)
# ---------------------------------------------------------------------------


def _load_specs():
    fx = Path(__file__).parent / "fixtures"
    import json
    app_spec = AppSpec.model_validate(json.loads((fx / "claims_app_spec.json").read_text()))
    agent_spec = AgentSpec.model_validate(json.loads((fx / "claims_agent_spec.json").read_text()))
    return app_spec, agent_spec


@pytest.mark.asyncio
async def test_triggered_run_is_plan_only_and_staged_not_pending(monkeypatch):
    app_spec, agent_spec = _load_specs()
    trigger = Trigger(id="claim_poll", type="poll", action="intake_claim", enabled=True)

    captured = {}

    async def _fake_execute_run(**kwargs):
        captured.update(kwargs)
        return RunResponse(correlation_id="run_1", status="pending_approval")

    monkeypatch.setattr(trigger_runner, "execute_run", _fake_execute_run)

    staged = []

    async def _fake_stager(**kwargs):
        staged.append(kwargs)
        return "run_1:C-9"

    pending = _FakeCol()
    rec = await trigger_runner._fire_and_persist(
        settings=_TRIGGER_SETTINGS,
        app_spec=app_spec,
        agent_spec=agent_spec,
        trigger=trigger,
        inputs={"claim_id": "C-9"},
        pending_runs_col=pending,
        app_doc={"app_id": "a", "agent_id": "ag", "tenant_id": "acme", "slug": app_spec.slug},
        stage_recommendation=_fake_stager,
    )

    # A triggered run never auto-commits: plan_only must be True.
    assert captured.get("plan_only") is True
    # The recommendation is STAGED into the unified inbox, not pending_runs.
    assert len(staged) == 1
    assert staged[0]["source"] == "trigger"
    assert len(pending.inserted) == 0
    assert rec["fired"] is True
    assert rec["correlation_id"] == "run_1:C-9"


@pytest.mark.asyncio
async def test_triggered_run_without_stager_fails_loud(monkeypatch):
    """No injected stager → FAIL LOUD. The legacy pending_runs fallback was
    removed together with the pending_runs approval path; a recommendation that
    cannot be staged into the unified officer queue must raise rather than
    insert an un-approvable husk row."""
    app_spec, agent_spec = _load_specs()
    trigger = Trigger(id="t1", type="schedule.interval", action="intake_claim",
                      enabled=True, every_seconds=60)

    async def _fake_execute_run(**kwargs):
        return RunResponse(correlation_id="run_2", status="pending_approval")

    monkeypatch.setattr(trigger_runner, "execute_run", _fake_execute_run)

    pending = _FakeCol()
    with pytest.raises(RuntimeError, match="stage_recommendation"):
        await trigger_runner._fire_and_persist(
            settings=_TRIGGER_SETTINGS,
            app_spec=app_spec,
            agent_spec=agent_spec,
            trigger=trigger,
            inputs={},
            pending_runs_col=pending,
            app_doc={"app_id": "a", "agent_id": "ag", "tenant_id": "acme", "slug": app_spec.slug},
            stage_recommendation=None,
        )
    assert len(pending.inserted) == 0  # no un-approvable husk row created


# ---------------------------------------------------------------------------
# poll fan-out bounds: per-tick cap (prod), one-at-a-time (test), force_fire
# ---------------------------------------------------------------------------


def _poll_trigger(enabled=True):
    return Trigger(
        id="p", type="poll", action="intake_claim", enabled=enabled,
        tool="claims.list", dedup_key="claim_id",
        input_template={"claim_id": "$row.claim_id"},
    )


async def _stub_run_and_stage(monkeypatch):
    calls = []

    async def _fake_execute_run(**kw):
        calls.append(kw)
        return RunResponse(correlation_id=f"run_{len(calls)}", status="pending_approval")

    monkeypatch.setattr(trigger_runner, "execute_run", _fake_execute_run)

    staged = []

    async def _stager(**kw):
        staged.append(kw)
        return f"run:{len(staged)}"

    return calls, staged, _stager


@pytest.mark.asyncio
async def test_poll_caps_rows_per_tick_in_prod(monkeypatch):
    set_current_env("prod")
    try:
        app_spec, agent_spec = _load_specs()
        calls, staged, stager = await _stub_run_and_stage(monkeypatch)
        rows = [{"claim_id": f"C-{i}"} for i in range(100)]
        activity, processed = await trigger_runner._process_new_poll_rows(
            settings=_TRIGGER_SETTINGS, app_spec=app_spec, agent_spec=agent_spec,
            trigger=_poll_trigger(), rows=rows, seen=set(),
            max_rows=25, concurrency=5,
            pending_runs_col=_FakeCol(),
            app_doc={"slug": app_spec.slug, "tenant_id": "acme"},
            stage_recommendation=stager,
        )
        # Only the bounded page is processed this tick; the other 75 wait.
        assert len(processed) == 25
        assert len(activity) == 25
        assert len(staged) == 25
        assert processed == [f"C-{i}" for i in range(25)]
    finally:
        set_current_env("prod")


@pytest.mark.asyncio
async def test_poll_one_at_a_time_in_test(monkeypatch):
    set_current_env("test")
    try:
        app_spec, agent_spec = _load_specs()
        calls, staged, stager = await _stub_run_and_stage(monkeypatch)
        rows = [{"claim_id": f"C-{i}"} for i in range(100)]
        activity, processed = await trigger_runner._process_new_poll_rows(
            settings=_TRIGGER_SETTINGS, app_spec=app_spec, agent_spec=agent_spec,
            trigger=_poll_trigger(), rows=rows, seen=set(),
            max_rows=25, concurrency=5,  # asked for 25, but TEST forces 1
            pending_runs_col=_FakeCol(),
            app_doc={"slug": app_spec.slug, "tenant_id": "acme"},
            stage_recommendation=stager,
        )
        assert len(processed) == 1
        assert processed == ["C-0"]
    finally:
        set_current_env("prod")


@pytest.mark.asyncio
async def test_poll_skips_already_seen_rows(monkeypatch):
    set_current_env("prod")
    try:
        app_spec, agent_spec = _load_specs()
        calls, staged, stager = await _stub_run_and_stage(monkeypatch)
        rows = [{"claim_id": f"C-{i}"} for i in range(5)]
        _, processed = await trigger_runner._process_new_poll_rows(
            settings=_TRIGGER_SETTINGS, app_spec=app_spec, agent_spec=agent_spec,
            trigger=_poll_trigger(), rows=rows, seen={"C-0", "C-1", "C-2"},
            max_rows=25, concurrency=5,
            pending_runs_col=_FakeCol(),
            app_doc={"slug": app_spec.slug, "tenant_id": "acme"},
            stage_recommendation=stager,
        )
        assert processed == ["C-3", "C-4"]
    finally:
        set_current_env("prod")


@pytest.mark.asyncio
async def test_run_trigger_once_fires_disabled_poll_one_row(monkeypatch):
    """Run-now works on a DEACTIVATED trigger (force_fire) and processes exactly
    one new row."""
    set_current_env("prod")
    try:
        app_spec, agent_spec = _load_specs()
        calls, staged, stager = await _stub_run_and_stage(monkeypatch)

        async def _fake_poll(**kw):
            return [{"claim_id": "C-1"}, {"claim_id": "C-2"}]

        monkeypatch.setattr(trigger_runner, "_call_poll_tool", _fake_poll)

        activity, processed = await trigger_runner.run_trigger_once(
            settings=_TRIGGER_SETTINGS, app_spec=app_spec, agent_spec=agent_spec,
            trigger=_poll_trigger(enabled=False),  # deactivated — must still fire
            state={}, inputs=None,
            pending_runs_col=_FakeCol(),
            app_doc={"slug": app_spec.slug, "tenant_id": "acme"},
            stage_recommendation=stager,
        )
        assert len(processed) == 1  # exactly one, even though 2 rows returned
        assert len(staged) == 1
        assert activity[0]["fired"] is True
    finally:
        set_current_env("prod")
