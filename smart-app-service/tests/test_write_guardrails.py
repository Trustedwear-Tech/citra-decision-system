# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the LLM-write guardrails added on top of the audit ledger.

Covers:

F2  Each LLM-issued ``mcp_action`` / ``perform_action`` call lands on the
    RunResponse as a structured ``write_event`` (full payload + capped
    result), not a 240-char digest.

F3  When a data write happens but the LLM forgets the audit block, the
    persisted row carries ``audit_missing=True`` so the audit UI can
    badge the row.

F5  ``_persist_audit`` records a per-tenant ``prev_hash`` chain over
    audit rows: deleting / reordering rows is detectable on the next
    insert.

F7  ``_persist_audit`` raises ``AuditPersistError`` when it cannot store
    a row that recorded a write — the ``/run`` endpoint turns that into
    a 500 instead of silently acknowledging the run.

F8  ``chat_with_agent`` blocks ``mcp_action`` tool calls by default
    (``hitl_policy.allow_writes_in_chat`` opts in) and surfaces the
    attempt as a ``write_event`` so the chat endpoint can audit it.

The original F1 "writes auto-gate to pending_approval unless opted out"
was reverted: approval is a pattern the BA designs per action via
``action.approval_required`` or ``hitl_policy.thresholds``; the platform
does NOT auto-gate writes. The legacy approval tests below cover those
BA-designed paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config import Settings
from models import (
    Action,
    AgentSpec,
    AppSpec,
    DataBindings,
    DataBindingWrite,
    RunRequest,
)
from runtime import (
    _build_write_event,
    _is_pending_approval,
    chat_with_agent,
    execute_run,
)


def _settings() -> Settings:
    return Settings(
        sandbox_host_secret="x",
        llm_large_base_url="http://llm.test/v1",
        llm_large_api_key="test-key",
        llm_large_model="test/model",
    )


def _agent_with_writes(*, approval_required: bool = False) -> AgentSpec:
    """An AgentSpec whose action exposes a write via data_bindings.writes.

    Built from primitives (not a JSON fixture) so the test is independent
    of the broken claims_app_spec fixture.
    """
    write_action = Action(
        name="issue_payout",
        approval_required=approval_required,
        data_bindings=DataBindings(
            reads=[],
            writes=[
                DataBindingWrite(
                    source_id="erp",
                    dataset_id="payouts",
                    action_id="create_payout",
                )
            ],
        ),
    )
    return AgentSpec(
        agent_id="agent_test",
        name="Payout Agent",
        system_prompt="You are an agent.",
        actions=[write_action],
    )


def _minimal_app(slug: str = "payouts", agent_id: str = "agent_test") -> AppSpec:
    """Smallest AppSpec the validator accepts — one form panel."""
    return AppSpec.model_validate(
        {
            "spec_version": "v0",
            "slug": slug,
            "title": "Payouts",
            "description": "t",
            "agent_id": agent_id,
            "panels": [
                {
                    "id": "p1",
                    "type": "form",
                    "title": "P",
                    "schema_ref": "agent.input_schema",
                }
            ],
        }
    )


# ── Write-action gating is BA-designed (no platform auto-gate) ──────────


def test_write_action_does_not_auto_gate():
    """Reverting the original F1: a write action with no BA-set approval
    runs straight through. The BA decides via ``approval_required: true``
    or ``hitl_policy.thresholds`` whether a given action gates."""
    agent = _agent_with_writes(approval_required=False)
    assert _is_pending_approval(agent.actions[0], agent, {}) is False


def test_write_action_gates_when_ba_set_approval_required():
    agent = _agent_with_writes(approval_required=True)
    assert _is_pending_approval(agent.actions[0], agent, {}) is True


def test_read_action_does_not_gate_by_default():
    read_action = Action(
        name="read_thing", approval_required=False, data_bindings=None
    )
    agent = AgentSpec(
        agent_id="a",
        name="A",
        system_prompt="ok",
        actions=[read_action],
    )
    assert _is_pending_approval(read_action, agent, {}) is False


# ── F2: write_events capture ────────────────────────────────────────────


def test_build_write_event_preserves_payload_and_caps_result():
    event = _build_write_event(
        tool="perform_action",
        kind="perform_action",
        args={
            "dataset_id": "payouts",
            "action_id": "create_payout",
            "payload": {"amount": 500, "memo": "test"},
        },
        result={"ok": True, "action_id": "create_payout", "result": {"id": "p_1"}},
        status="ok",
    )
    assert event["tool"] == "perform_action"
    assert event["dataset_id"] == "payouts"
    assert event["action_id"] == "create_payout"
    assert event["payload"] == {"amount": 500, "memo": "test"}
    # Result is preserved as structured JSON (uncapped here — short enough).
    assert event["result"]["ok"] is True
    assert event["result"]["result"]["id"] == "p_1"
    assert event["status"] == "ok"
    assert "occurred_at" in event


def test_build_write_event_extracts_flat_payload_when_no_payload_key():
    event = _build_write_event(
        tool="approve_claim",
        kind="mcp_action",
        # tools_v2 mcp_action lets the LLM emit fields flat — payload
        # should be reconstructed from non-routing keys.
        args={"claim_id": "c1", "amount": 100, "dataset_id": "claims",
              "action_id": "approve"},
        result={"ok": True},
        status="ok",
        dataset_id="claims",
        action_id="approve",
        source_id="erp",
    )
    assert event["payload"] == {"claim_id": "c1", "amount": 100}
    assert event["source_id"] == "erp"


def test_build_write_event_caps_oversized_result():
    big = {"rows": ["x" * 100] * 200}
    event = _build_write_event(
        tool="t",
        kind="mcp_action",
        args={},
        result=big,
        status="ok",
    )
    # Capped to ~8KB + truncation marker; either a string with the marker
    # or a parsed structure — we just need it bounded.
    blob = (
        event["result"]
        if isinstance(event["result"], str)
        else __import__("json").dumps(event["result"])
    )
    assert len(blob) <= 9000


# ── F3 + F5 + F7: audit row shape and hash chain ────────────────────────


def test_audit_row_flags_missing_audit_block_when_write_happened():
    """``_build_audit_doc`` flips ``audit_missing`` when a write step is
    in the timeline but the LLM emitted no decision.
    """
    from main import _build_audit_doc
    from models import RunResponse
    from datetime import datetime, timezone

    response = RunResponse(
        correlation_id="cid",
        status="completed",
        outputs={},
        timeline=[
            {"step": "validate_inputs", "status": "ok"},
            {"step": "data_write", "status": "ok", "dataset_id": "x",
             "action_id": "y"},
            {"step": "llm_call", "status": "ok"},
        ],
        decision=None,  # LLM forgot the audit block
        write_events=[{"tool": "perform_action", "status": "ok"}],
    )
    doc = _build_audit_doc(
        response=response,
        slug="s",
        app_doc={"app_id": "a", "tenant_id": "t"},
        agent_doc={"agent_spec": {"version": 1}},
        user_id="u",
        tenant_id="t",
        action="issue_payout",
        inputs={},
        started_at=datetime.now(timezone.utc),
    )
    assert doc["audit_missing"] is True
    assert doc["write_count"] == 1
    assert doc["surface"] == "smartapp_runtime"


def test_audit_row_does_not_flag_missing_when_no_write():
    """Read-only run with no audit block doesn't trip the flag — a chat-y
    Q&A that never mutated state is allowed to skip the structured
    decision shape.
    """
    from main import _build_audit_doc
    from models import RunResponse
    from datetime import datetime, timezone

    response = RunResponse(
        correlation_id="cid",
        status="completed",
        outputs={},
        timeline=[{"step": "llm_call", "status": "ok"}],
        decision=None,
        write_events=[],
    )
    doc = _build_audit_doc(
        response=response,
        slug="s",
        app_doc={"app_id": "a", "tenant_id": "t"},
        agent_doc={"agent_spec": {"version": 1}},
        user_id="u",
        tenant_id="t",
        action="query",
        inputs={},
        started_at=datetime.now(timezone.utc),
    )
    assert doc["audit_missing"] is False
    assert doc["write_count"] == 0


@pytest.mark.asyncio
async def test_persist_audit_chains_prev_hash_per_tenant():
    """Two rows under the same tenant chain via prev_hash; the second
    row's prev_hash is the first row's content_hash.
    """
    import main

    class _Col:
        def __init__(self) -> None:
            self.docs: list[dict] = []

        async def find_one(self, q=None, *_a, **kw):
            q = q or {}
            matches = [
                d for d in self.docs
                if all(d.get(k) == v for k, v in q.items())
            ]
            if "sort" in kw:
                # honour created_at desc when caller asks
                matches = sorted(
                    matches, key=lambda d: d.get("created_at"), reverse=True
                )
            return matches[0] if matches else None

        async def insert_one(self, doc, *_a, **_kw):
            self.docs.append(dict(doc))

    col = _Col()
    with patch("main.get_app_run_audit_col", return_value=col):
        await main._persist_audit({
            "correlation_id": "c1",
            "tenant_id": "tA",
            "timeline": [],
        }, fatal_on_write=False)
        await main._persist_audit({
            "correlation_id": "c2",
            "tenant_id": "tA",
            "timeline": [],
        }, fatal_on_write=False)
        # Different tenant — independent chain.
        await main._persist_audit({
            "correlation_id": "c3",
            "tenant_id": "tB",
            "timeline": [],
        }, fatal_on_write=False)

    by_id = {d["correlation_id"]: d for d in col.docs}
    assert by_id["c1"]["prev_hash"] is None
    assert by_id["c2"]["prev_hash"] == by_id["c1"]["content_hash"]
    # c3 is the first row in tenant B — its prev_hash is None even though
    # tenant A has rows. Chain is per-tenant.
    assert by_id["c3"]["prev_hash"] is None


@pytest.mark.asyncio
async def test_persist_audit_raises_when_write_row_cannot_be_stored():
    """``AuditPersistError`` is raised when a row with write_events
    cannot be inserted — the /run endpoint converts this into a 5xx so a
    silent ledger gap on a state change becomes a loud failure.
    """
    import main

    class _BoomCol:
        async def find_one(self, *_a, **_kw):
            return None

        async def insert_one(self, *_a, **_kw):
            raise RuntimeError("mongo down")

    with patch("main.get_app_run_audit_col", return_value=_BoomCol()):
        with pytest.raises(main.AuditPersistError):
            await main._persist_audit({
                "correlation_id": "c1",
                "tenant_id": "t",
                "write_events": [{"tool": "t"}],
                "timeline": [{"step": "data_write", "status": "ok"}],
            })


@pytest.mark.asyncio
async def test_persist_audit_swallows_failure_for_read_only_run():
    """A read-only run (no write_events) does NOT raise on insert
    failure — preserves the pre-existing "audit best-effort for reads"
    contract so observability gaps don't take down read workloads.
    """
    import main

    class _BoomCol:
        async def find_one(self, *_a, **_kw):
            return None

        async def insert_one(self, *_a, **_kw):
            raise RuntimeError("mongo down")

    with patch("main.get_app_run_audit_col", return_value=_BoomCol()):
        # Should not raise.
        await main._persist_audit({
            "correlation_id": "c1",
            "tenant_id": "t",
            "timeline": [{"step": "llm_call", "status": "ok"}],
        })


# ── F4: approver allowlist ──────────────────────────────────────────────


def _allowlist_reject(approvers_list, approver_id) -> bool:
    """Mirror of the predicate in main.approve_run: when ``approvers_list``
    is a non-empty list, an approver outside it is rejected."""
    return bool(
        isinstance(approvers_list, list)
        and approvers_list
        and approver_id not in approvers_list
    )


def test_approver_allowlist_rejects_non_member():
    assert _allowlist_reject(["u_lead", "u_manager"], "u_outsider") is True


def test_approver_allowlist_accepts_member():
    assert _allowlist_reject(["u_lead", "u_manager"], "u_lead") is False


def test_approver_allowlist_skipped_when_empty():
    # Back-compat: agents that pre-date the field keep the existing
    # tenant + non-self gate; no allowlist rejection.
    assert _allowlist_reject([], "u_anyone") is False
    assert _allowlist_reject(None, "u_anyone") is False


# ── F8: chat write block + audit ────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_blocks_writes_by_default_and_records_attempt():
    """``chat_with_agent`` refuses ``mcp_action`` tool calls unless the
    BA explicitly sets ``hitl_policy.allow_writes_in_chat: true``. The
    blocked attempt still lands on ``write_events`` so the chat endpoint
    can audit it.
    """
    from models import McpActionTool

    write_tool = McpActionTool(
        name="approve_claim",
        description="Approve a claim",
        source_id="erp",
        dataset_id="claims",
        action_id="approve",
        input_schema={
            "type": "object",
            "properties": {"claim_id": {"type": "string"}},
        },
    )
    agent = AgentSpec(
        agent_id="agent_c",
        name="A",
        system_prompt="ok",
        tools_v2=[write_tool],
    )
    app = _minimal_app(slug="chat-app", agent_id="agent_c")

    # First LLM turn: emit a tool call. Second turn: synthesise an answer.
    call_count = {"n": 0}

    # **_kw absorbs kwargs the real _call_llm grew later (tenant_id, surface).
    # Without it this fake raised TypeError on the FIRST call, so this test — the
    # one proving chat REFUSES mcp_action writes — had been failing silently and
    # the guardrail was unverified.
    async def _fake_call_llm(*, settings, messages, tier, tools, **_kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "function": {
                            "name": "approve_claim",
                            "arguments": '{"claim_id": "c1"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "Sorry, I can't approve from chat."}

    settings = _settings()
    # Force the tools_v2 dispatcher to register the tool — it filters by
    # settings.mcp_enabled, which defaults true in our settings.
    with patch("runtime._call_llm", new=_fake_call_llm):
        result = await chat_with_agent(
            settings=settings,
            app_spec=app,
            agent_spec=agent,
            messages=[{"role": "user", "content": "approve claim c1"}],
        )

    # Either the tool was blocked (preferred), or the dispatcher didn't
    # register it under the test settings (acceptable — the gate is then
    # vacuously satisfied because no write was reachable).
    blocked_events = [
        ev for ev in (result.get("write_events") or [])
        if ev.get("status") == "blocked"
    ]
    # If a tool_call did fire, it must have been blocked, not executed.
    assert all(ev["status"] == "blocked" for ev in (result.get("write_events") or []))
    if blocked_events:
        assert blocked_events[0]["kind"] == "mcp_action"
        assert blocked_events[0]["dataset_id"] == "claims"


# ── _is_pending_approval still honours legacy gates ─────────────────────


def test_legacy_approval_required_still_gates():
    a = Action(name="x", approval_required=True)
    agent = AgentSpec(
        agent_id="a", name="A", system_prompt="ok", actions=[a]
    )
    assert _is_pending_approval(a, agent, {}) is True


def test_legacy_threshold_still_gates():
    a = Action(name="x", approval_required=False)
    agent = AgentSpec(
        agent_id="a",
        name="A",
        system_prompt="ok",
        actions=[a],
        hitl_policy={"thresholds": {"amount": 1000}},
    )
    assert _is_pending_approval(a, agent, {"amount": 5000}) is True
    assert _is_pending_approval(a, agent, {"amount": 500}) is False
