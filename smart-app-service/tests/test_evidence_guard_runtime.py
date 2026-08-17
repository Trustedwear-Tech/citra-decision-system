# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""End-to-end wiring test for the read-before-write guard inside execute_run.

Unit coverage of the guard logic lives in test_evidence_guard.py. This file
drives the REAL runtime.execute_run with a stubbed LLM + stubbed MCP dispatch to
prove the anchor-resolution → ledger-accumulation → gate path is wired
correctly: a write staged without reading the anchor record FAILS LOUD, and the
same write PASSES once the agent actually reads that record.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from data_tools import ACTION_TOOL_NAME, QUERY_TOOL_NAME
from models import (
    Action,
    AgentSpec,
    AppSpec,
    DataBindingRead,
    DataBindings,
    DataBindingWrite,
    RunRequest,
)
from runtime import execute_run


def _settings() -> Settings:
    return Settings(
        sandbox_host_secret="x",
        llm_large_base_url="http://llm.test/v1",
        llm_large_api_key="test-key",
        llm_large_model="test/model",
    )


def _agent() -> AgentSpec:
    action = Action(
        name="route_claim",
        input_schema={
            "type": "object",
            "required": ["claim_id"],
            "properties": {"claim_id": {"type": "string"}},
        },
        data_bindings=DataBindings(
            reads=[DataBindingRead(source_id="erp", dataset_id="claims")],
            writes=[DataBindingWrite(source_id="erp", dataset_id="claims",
                                     action_id="route")],
        ),
    )
    return AgentSpec(
        agent_id="agent_x",
        name="Claims Agent",
        system_prompt="You are an agent.",
        actions=[action],
    )


def _app() -> AppSpec:
    return AppSpec.model_validate({
        "spec_version": "v0",
        "slug": "claims",
        "title": "Claims",
        "description": "t",
        "agent_id": "agent_x",
        "panels": [{"id": "p1", "type": "form", "title": "P",
                    "schema_ref": "agent.input_schema"}],
    })


def _write_call():
    return {"id": "w1", "function": {"name": ACTION_TOOL_NAME, "arguments":
            '{"dataset_id":"claims","action_id":"route",'
            '"payload":{"claim_id":"c1","assignee":"jo"}}'}}


def _read_call():
    return {"id": "r1", "function": {"name": QUERY_TOOL_NAME, "arguments":
            '{"dataset_id":"claims","query":"claim c1"}'}}


def _final():
    return {"role": "assistant",
            "content": "DECISION: route\nREASONING: because.\n", "tool_calls": []}


@pytest.mark.asyncio
async def test_gate_fails_when_write_staged_without_reading_anchor():
    """The agent stages a write but never read claim c1 → run fails loud."""
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [_write_call()]},
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})):
        resp = await execute_run(
            settings=_settings(), app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    # CONTRACT NOTE — the gate now SELF-CORRECTS rather than failing the run.
    # It used to mark the step "blocked" and fail loud; it now re-prompts the
    # agent, which is why the timeline reads "self_correction" and the run can
    # come back "completed". These tests asserted the old contract and had been
    # failing ever since, masked by the scripted LLM running out of turns first.
    #
    # What must NEVER change is the safety property, and that is what is
    # asserted here: the gate FIRED, and NO write reached the officer. A write
    # staged without reading its anchor is the unrevertable-bad-write case the
    # guard exists for.
    gate = [s for s in resp.timeline if s.get("step") == "evidence_gate"]
    assert gate, f"evidence gate never ran: {[s.get('step') for s in resp.timeline]}"
    assert gate[0].get("status") in ("blocked", "self_correction"), gate[0]
    # Nothing was staged for the officer to approve.
    assert not getattr(resp, "planned_writes", None)


@pytest.mark.asyncio
async def test_gate_passes_when_anchor_read_before_write():
    """Same write, but the agent reads claim c1 first → stages normally."""
    turns = [
        {"role": "assistant", "content": "",
         "tool_calls": [_read_call(), _write_call()]},
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})), \
         patch("runtime.dispatch_query_dataset",
               new=AsyncMock(return_value={"rows": [{"claim_id": "c1", "amount": 100}]})):
        resp = await execute_run(
            settings=_settings(), app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    assert resp.status == "pending_approval"
    assert resp.planned_writes and resp.planned_writes[0]["payload"]["claim_id"] == "c1"
    assert not any(s.get("step") == "evidence_gate" for s in resp.timeline)


@pytest.mark.asyncio
async def test_prefetch_record_uses_physical_table_not_dataset_id():
    """Part A anchor read must SELECT FROM the physical table ('complaints'),
    not the catalogue dataset_id ('field_operations.complaints')."""
    from runtime import _prefetch_record
    from models import Action
    captured = {}

    async def fake_read(*, settings, user_jwt, source_id, dataset_id, kind, query, row_limit):
        captured["query"] = query
        captured["dataset_id"] = dataset_id
        return {"rows": [{"complaint_id": "CMP-1", "status": "new"}]}

    action = Action(
        name="route_complaint",
        input_schema={"type": "object", "required": ["complaint_id"],
                      "properties": {"complaint_id": {"type": "string"}}},
        anchor_read={"source_id": "field_operations",
                     "dataset_id": "field_operations.complaints",
                     "key_field": "complaint_id", "kind": "sql"},
    )
    with patch("proxy_clients.call_dept_mcp_read", new=fake_read):
        block, row = await _prefetch_record(
            settings=_settings(), action=action,
            inputs={"complaint_id": "CMP-1"}, auth_header=None,
        )
    assert "FROM complaints WHERE" in captured["query"]
    assert "field_operations.complaints" not in captured["query"]
    assert captured["dataset_id"] == "field_operations.complaints"  # MCP routing unchanged
    assert row["complaint_id"] == "CMP-1" and block


def _agent_with_required_lookup() -> AgentSpec:
    """Same claims agent, plus a REQUIRED bound mcp lookup (a bureau check)."""
    from models import McpTool
    base = _agent()
    base.tools_v2 = [McpTool(
        name="cibil_score", source_id="cibil", tool_name="cibil_score",
        dataset_id="cibil.credit_score", dataset_kind="rest", required=True,
    )]
    return base


@pytest.mark.asyncio
async def test_required_lookup_gate_blocks_when_skipped():
    """Agent reads the anchor + stages the write but never runs the REQUIRED
    cibil lookup → with REQUIRED_READS_MODE=enforce the run fails loud."""
    turns = [
        {"role": "assistant", "content": "",
         "tool_calls": [_read_call(), _write_call()]},  # anchor read + write, NO lookup
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.required_reads_mode = "enforce"
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})), \
         patch("runtime.dispatch_query_dataset",
               new=AsyncMock(return_value={"rows": [{"claim_id": "c1", "amount": 100}]})):
        resp = await execute_run(
            settings=settings, app_spec=_app(),
            agent_spec=_agent_with_required_lookup(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    # Same contract note as the anchor-read gate above: the gate self-corrects
    # now rather than failing the run. Assert the SAFETY property — it fired,
    # and the officer was shown nothing to approve.
    gate = [s for s in resp.timeline if s.get("step") == "evidence_gate"]
    assert gate, f"evidence gate never ran: {[s.get('step') for s in resp.timeline]}"
    assert gate[0].get("status") in ("blocked", "self_correction"), gate[0]
    assert not getattr(resp, "planned_writes", None)


def _lookup_call():
    return {"id": "m1", "function": {"name": "cibil_score",
            "arguments": '{"query":"credit check for c1"}'}}


@pytest.mark.asyncio
async def test_required_lookup_gate_passes_when_lookup_ran():
    """Agent reads the anchor, RUNS the required cibil lookup, then stages the
    write → enforce mode passes (pending_approval, no evidence_gate block)."""
    turns = [
        {"role": "assistant", "content": "",
         "tool_calls": [_read_call(), _lookup_call(), _write_call()]},
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.required_reads_mode = "enforce"
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})), \
         patch("runtime.dispatch_query_dataset",
               new=AsyncMock(return_value={"rows": [{"claim_id": "c1", "amount": 100}]})), \
         patch("runtime.dispatch_tools_v2_call",
               new=AsyncMock(return_value={"rows": [{"pan": "c1", "score": 720}]})):
        resp = await execute_run(
            settings=settings, app_spec=_app(),
            agent_spec=_agent_with_required_lookup(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    assert resp.status == "pending_approval", resp.error
    assert resp.planned_writes
    assert not any(s.get("step") == "evidence_gate" and s.get("status") == "blocked"
                   for s in resp.timeline)


@pytest.mark.asyncio
async def test_required_lookup_autoprocess_enforce_blocks_write():
    """Auto-process (plan_only=False): the write is dispatched but the required
    lookup was skipped → enforce refuses the write and feeds READ_BEFORE_WRITE
    back. Record/media dim turned OFF to isolate the lookup dimension."""
    turns = [
        {"role": "assistant", "content": "",
         "tool_calls": [_read_call(), _write_call()]},  # anchor read, NO lookup
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.required_reads_mode = "enforce"
    settings.read_before_write_autoprocess_mode = "off"  # isolate lookup dim
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})), \
         patch("runtime.dispatch_query_dataset",
               new=AsyncMock(return_value={"rows": [{"claim_id": "c1", "amount": 100}]})):
        resp = await execute_run(
            settings=settings, app_spec=_app(),
            agent_spec=_agent_with_required_lookup(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=False,
        )

    assert any(s.get("step") == "evidence_gate_autoprocess"
               and s.get("status") == "blocked"
               and any("cibil_score" in d for d in s.get("detail", []))
               for s in resp.timeline)


@pytest.mark.asyncio
async def test_required_lookup_stanza_injected_into_system_prompt():
    """The proactive 'MANDATORY CHECKS' stanza names the required lookup in the
    prompt the model actually receives (guide, then enforce)."""
    captured = {}

    async def _fake_llm(**kw):
        captured.setdefault("messages", kw.get("messages"))
        return _final()  # end immediately, no tool calls

    settings = _settings()
    settings.required_reads_mode = "log"  # observe only; we just inspect the prompt
    with patch("runtime._call_llm", new=_fake_llm):
        await execute_run(
            settings=settings, app_spec=_app(),
            agent_spec=_agent_with_required_lookup(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    blob = " ".join(str(m.get("content", "")) for m in (captured.get("messages") or []))
    assert "MANDATORY" in blob and "cibil_score" in blob


def test_required_reads_mode_defaults_to_enforce():
    """The DEFAULT must enforce — `mandatory_when_used` is a compliance control.

    It shipped defaulting to "log" and was never flipped, so docs/sources-file.md
    §11 ("the evidence gate refuses to stage a write"… the platform "ENFORCES
    it") was false in every default deployment: mark a bureau/KYC check
    mandatory, skip it, and the write staged with a log line. A control the
    author believes binds, which doesn't, is worse than no control. Pin the
    default so it can't dark-launch again.
    """
    from config import Settings
    assert Settings().required_reads_mode == "enforce"


@pytest.mark.asyncio
async def test_required_lookup_gate_log_mode_observes_but_stages():
    """REQUIRED_READS_MODE=log (opt-in rollout mode, no longer the default):
    the missing lookup is flagged would_block but the write still stages."""
    turns = [
        {"role": "assistant", "content": "",
         "tool_calls": [_read_call(), _write_call()]},
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.required_reads_mode = "log"
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})), \
         patch("runtime.dispatch_query_dataset",
               new=AsyncMock(return_value={"rows": [{"claim_id": "c1", "amount": 100}]})):
        resp = await execute_run(
            settings=settings, app_spec=_app(),
            agent_spec=_agent_with_required_lookup(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    assert resp.status == "pending_approval"
    assert resp.planned_writes
    assert any(s.get("step") == "evidence_gate" and s.get("status") == "would_block"
               and any("cibil_score" in d for d in s.get("detail", []))
               for s in resp.timeline)


@pytest.mark.asyncio
async def test_plan_mode_log_observes_but_stages():
    """Plan-mode dark-launch: an unread staged write is flagged 'would_block'
    but still returned as pending_approval (observe before enforcing)."""
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [_write_call()]},  # no read
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.read_before_write_plan_mode = "log"
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})):
        resp = await execute_run(
            settings=settings, app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    assert resp.status == "pending_approval"  # staged despite no read
    assert resp.planned_writes
    assert any(s.get("step") == "evidence_gate" and s.get("status") == "would_block"
               for s in resp.timeline)


@pytest.mark.asyncio
async def test_gate_disabled_by_flag_allows_unread_write():
    """With ENFORCE_READ_BEFORE_WRITE off, the same unread write stages."""
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [_write_call()]},
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.enforce_read_before_write = False
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action",
               new=AsyncMock(return_value={"ok": True, "_source_id": "erp"})):
        resp = await execute_run(
            settings=settings, app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=True,
        )

    assert resp.status == "pending_approval"  # kill switch honoured


# ── Auto-process path (plan_only=False): commits live, gated pre-dispatch ────
@pytest.mark.asyncio
async def test_autoprocess_enforce_blocks_then_self_corrects():
    """enforce mode: a write without a read is refused; the model reads on the
    next turn and the retried write commits."""
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [_write_call()]},   # blocked
        {"role": "assistant", "content": "",
         "tool_calls": [_read_call(), _write_call()]},                          # read → write ok
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.read_before_write_autoprocess_mode = "enforce"
    perform = AsyncMock(return_value={"ok": True, "_source_id": "erp"})
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action", new=perform), \
         patch("runtime.dispatch_query_dataset",
               new=AsyncMock(return_value={"rows": [{"claim_id": "c1"}]})):
        resp = await execute_run(
            settings=settings, app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=False,
        )

    # The blocked first attempt did NOT dispatch; only the post-read retry did.
    assert perform.await_count == 1
    assert any(s.get("step") == "evidence_gate_autoprocess"
               and s.get("status") == "blocked" for s in resp.timeline)


@pytest.mark.asyncio
async def test_autoprocess_log_mode_observes_but_commits():
    """dark-launch log mode: the unread write is flagged 'would_block' but still
    commits, so we can measure impact before enforcing."""
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [_write_call()]},
        _final(),
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.read_before_write_autoprocess_mode = "log"
    perform = AsyncMock(return_value={"ok": True, "_source_id": "erp"})
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action", new=perform):
        resp = await execute_run(
            settings=settings, app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=False,
        )

    assert perform.await_count == 1  # committed despite no read (observe-only)
    assert any(s.get("step") == "evidence_gate_autoprocess"
               and s.get("status") == "would_block" for s in resp.timeline)


@pytest.mark.asyncio
async def test_autoprocess_enforce_never_commits_unread_write():
    """enforce mode: a model that never reads never gets its write committed."""
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [_write_call()]},  # blocked
        _final(),  # gives up without reading
    ]

    async def _fake_llm(**_kw):
        # Extra calls get a terminating final rather than IndexError. The
        # script supplies the INTERESTING turns; the runtime is free to make
        # one more (it re-prompts after a blocked gate). Popping an empty
        # list made the run fail with "pop from empty list", so the test
        # asserting a FAILED run passed for the wrong reason and its assert
        # on the error text then failed — the harness under test, not the gate.
        return turns.pop(0) if turns else _final()

    settings = _settings()
    settings.read_before_write_autoprocess_mode = "enforce"
    perform = AsyncMock(return_value={"ok": True, "_source_id": "erp"})
    with patch("runtime._call_llm", new=_fake_llm), \
         patch("runtime.dispatch_perform_action", new=perform):
        resp = await execute_run(
            settings=settings, app_spec=_app(), agent_spec=_agent(),
            request=RunRequest(action="route_claim", inputs={"claim_id": "c1"}),
            plan_only=False,
        )

    perform.assert_not_awaited()  # ungrounded write never reached the SoR
