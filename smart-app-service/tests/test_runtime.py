# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for runtime.execute_run.

Verifies pure logic without hitting the upstream LLM:
- action resolution
- input validation against the action's input_schema
- approval gate short-circuits before LLM call
- failure path when the LLM endpoint is unreachable
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from config import Settings
from models import AgentSpec, AppSpec, RunRequest
from runtime import execute_run


def _final_turn():
    """A terminating assistant turn: a decision, no tool calls.

    The runtime must call the LLM for an approval-required action now (that call
    is what produces the plan), so the fake has to return something the agent
    loop can finish on. A bare AsyncMock() returns a MagicMock, which the loop
    cannot parse and the run dies as "failed" for reasons unrelated to approval.
    """
    return {"role": "assistant",
            "content": "DECISION: route\nREASONING: because.\n",
            "tool_calls": []}


@pytest.fixture(autouse=True)
def _kill_switch_stub(monkeypatch):
    """Stub the halt/pause collection for every test in this module.

    execute_run() consults the kill switch on every run, and get_control_col()
    raises "Database not initialised" when it was never wired. These tests call
    execute_run directly and never build a main fixture, so the run died on
    infrastructure — reported as status="failed" — and a test asserting
    "pending_approval" failed for a reason that had nothing to do with approval.
    Empty collection = nothing halted, which is the normal state.
    """
    import main
    from tests._test_helpers import _MemCol  # type: ignore

    monkeypatch.setattr(main, "_control_col", _MemCol(), raising=False)


FIXTURES = Path(__file__).parent / "fixtures"


def _load_specs() -> tuple[AppSpec, AgentSpec]:
    app_spec = AppSpec.model_validate(
        json.loads((FIXTURES / "claims_app_spec.json").read_text())
    )
    agent_spec = AgentSpec.model_validate(
        json.loads((FIXTURES / "claims_agent_spec.json").read_text())
    )
    return app_spec, agent_spec


def _settings() -> Settings:
    return Settings(
        sandbox_host_secret="x",
        llm_large_base_url="http://llm.test/v1",
        llm_large_api_key="test-key",
        llm_large_model="test/model",
    )


@pytest.mark.asyncio
async def test_unknown_action_raises_400():
    from fastapi import HTTPException

    app_spec, agent_spec = _load_specs()
    req = RunRequest(action="does_not_exist", inputs={})

    with pytest.raises(HTTPException) as exc:
        await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )
    assert exc.value.status_code == 400
    assert "does_not_exist" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_input_schema_violation_raises_422():
    from fastapi import HTTPException

    app_spec, agent_spec = _load_specs()
    # Reuse the agent-level input_schema on an action so the runtime sees
    # a per-action schema (which is what `_validate_inputs` checks).
    action = agent_spec.actions[0]
    action.input_schema = agent_spec.input_schema
    req = RunRequest(action=action.name, inputs={})

    with pytest.raises(HTTPException) as exc:
        await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_approval_required_short_circuits():
    app_spec, agent_spec = _load_specs()
    # Force an action to require approval.
    agent_spec.actions[0].approval_required = True
    # Build inputs that satisfy the action's schema (or empty if none).
    schema = agent_spec.actions[0].input_schema or {}
    inputs = _minimal_inputs(schema)

    req = RunRequest(action=agent_spec.actions[0].name, inputs=inputs)

    # CONTRACT NOTE — approval no longer SHORT-CIRCUITS, it forces plan-only.
    #
    # This test used to assert the LLM was never called. That was the old
    # contract: an approval_required action returned pending_approval without
    # inference. It was replaced deliberately, and runtime.py records why at the
    # gate: forcing plan_only makes every write-capable tool dry-run, the
    # intents accumulate into planned_writes, and the officer's Apply replays
    # exactly those. It also closes a hole on the unattended path — a
    # workflow-triggered run passes plan_only=False to auto-apply, so an
    # approval_required action reached by a trigger has to be pinned to planning
    # here or approval is silently skipped.
    #
    # So the LLM MUST run (it is what produces the plan). What must hold is that
    # the gate fired and nothing was committed behind the officer's back.
    with patch("runtime._call_llm", new=AsyncMock(return_value=_final_turn())):
        resp = await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )

    gate = [s for s in (resp.timeline or []) if s.get("step") == "approval_gate"]
    assert gate, f"approval gate never ran: {[s.get('step') for s in (resp.timeline or [])]}"
    assert gate[0].get("detail") == "approval_required", gate[0]
    # Planning only — no write committed without the officer.
    assert not getattr(resp, "write_events", None), getattr(resp, "write_events", None)


@pytest.mark.asyncio
async def test_inference_failure_returns_failed():
    app_spec, agent_spec = _load_specs()
    action = agent_spec.actions[0]
    action.approval_required = False  # make sure we go past gate
    inputs = _minimal_inputs(action.input_schema or {})

    req = RunRequest(action=action.name, inputs=inputs)

    async def _boom(*a, **kw):
        raise RuntimeError("inference offline")

    with patch("runtime._call_llm", new=_boom):
        resp = await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )
    assert resp.status == "failed"
    assert "inference offline" in (resp.error or "")


@pytest.mark.asyncio
async def test_happy_path_returns_completed():
    app_spec, agent_spec = _load_specs()
    action = agent_spec.actions[0]
    action.approval_required = False
    inputs = _minimal_inputs(action.input_schema or {})
    req = RunRequest(action=action.name, inputs=inputs)

    async def _ok(**kw):
        return {"role": "assistant", "content": "Approved with reasoning."}

    with patch("runtime._call_llm", new=_ok):
        resp = await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )
    assert resp.status == "completed"
    assert resp.outputs.get("text") == "Approved with reasoning."
    assert any(s["step"] == "llm_call" for s in resp.timeline)


def _minimal_inputs(schema: dict) -> dict:
    """Construct a minimal valid object satisfying `required` keys."""
    if not schema or schema.get("type") != "object":
        return {}
    inputs: dict = {}
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        spec = props.get(key, {})
        t = spec.get("type")
        if t == "string":
            inputs[key] = "x"
        elif t == "number" or t == "integer":
            inputs[key] = 1
        elif t == "boolean":
            inputs[key] = False
        elif t == "array":
            inputs[key] = []
        elif t == "object":
            inputs[key] = {}
        else:
            inputs[key] = "x"
    return inputs
