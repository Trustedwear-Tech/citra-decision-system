# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Unit tests for the required data-LOOKUP dimension of the evidence guard.

Pure — no runtime, no LLM, no MCP. Pins the guarantee: when a bound mcp read
tool is marked ``required: true`` (a policy-mandated bureau / KYC / sanctions
check), a staged write is a violation unless that lookup actually RAN for an id
belonging to the case under review.
"""
from evidence_guard import (
    Anchor,
    ReadLedger,
    required_lookup_tools,
    required_lookup_violations,
)
from publish_validators import validate_required_lookup_is_bound


# ── stand-ins (only the fields the guard reads) ──────────────────────────────
class _McpTool:
    def __init__(self, name, *, dataset_id=None, required=False, kind="mcp"):
        self.name = name
        self.kind = kind
        self.dataset_id = dataset_id
        self.required = required
        self.tool_name = name


class _AgentSpec:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


_ANCHORS = [Anchor(field="application_id", value="APP-1")]
_WRITE = [{"action_id": "approve_loan", "payload": {"application_id": "APP-1"}}]


def _spec_with_required_cibil():
    return _AgentSpec([
        _McpTool("cibil_score", dataset_id="cibil.credit_score", required=True),
        _McpTool("cibil_history", dataset_id="cibil.credit_history", required=True),
        _McpTool("plain_read", dataset_id="ops.notes", required=False),
    ])


# ── required_lookup_tools selection ──────────────────────────────────────────
def test_selects_only_bound_required_mcp_tools():
    spec = _AgentSpec([
        _McpTool("cibil_score", dataset_id="cibil.credit_score", required=True),
        _McpTool("not_required", dataset_id="ds.x", required=False),
        _McpTool("unbound_required", dataset_id=None, required=True),   # unbound → skip
        _McpTool("required_action", dataset_id="ds.y", required=True, kind="mcp_action"),
    ])
    assert [t.name for t in required_lookup_tools(spec)] == ["cibil_score"]


def test_no_required_tools_returns_empty():
    spec = _AgentSpec([_McpTool("plain", dataset_id="ds.x", required=False)])
    assert required_lookup_tools(spec) == []


# ── ledger note_lookup_read + lookup_ran ─────────────────────────────────────
def test_note_lookup_read_records_tool_ran():
    led = ReadLedger()
    led.note_lookup_read(tool_name="cibil_score")
    assert "cibil_score" in led.lookup_tools_ran
    assert led.lookup_ran("cibil_score")
    assert not led.lookup_ran("cibil_history")
    assert not led.lookup_ran("")


# ── required_lookup_violations matrix ────────────────────────────────────────
def _ledger_with_record_and_lookups(*ran):
    """A ledger where the anchor record was read plus the named lookups ran."""
    led = ReadLedger()
    led.note_record_read(rows=[{"application_id": "APP-1", "pan": "PAN-1"}])
    for name in ran:
        led.note_lookup_read(tool_name=name)
    return led


def test_required_lookups_ran_no_violation():
    led = _ledger_with_record_and_lookups("cibil_score", "cibil_history")
    unmet = required_lookup_violations(
        planned_writes=_WRITE, anchors=_ANCHORS, ledger=led,
        agent_spec=_spec_with_required_cibil(),
    )
    assert unmet == []


def test_required_lookup_never_ran_is_violation():
    # Only the score ran; the required history lookup never did.
    led = _ledger_with_record_and_lookups("cibil_score")
    unmet = required_lookup_violations(
        planned_writes=_WRITE, anchors=_ANCHORS, ledger=led,
        agent_spec=_spec_with_required_cibil(),
    )
    assert len(unmet) == 1
    assert "cibil_history" in unmet[0]
    assert "never" in unmet[0].lower()


def test_no_required_lookup_ran_all_violations():
    led = _ledger_with_record_and_lookups()  # record read, but no lookups ran
    unmet = required_lookup_violations(
        planned_writes=_WRITE, anchors=_ANCHORS, ledger=led,
        agent_spec=_spec_with_required_cibil(),
    )
    assert len(unmet) == 2  # both cibil_score + cibil_history unmet


def test_no_writes_or_no_anchors_is_empty():
    led = _ledger_with_record_and_lookups("cibil_score", "cibil_history")
    spec = _spec_with_required_cibil()
    assert required_lookup_violations(
        planned_writes=[], anchors=_ANCHORS, ledger=led, agent_spec=spec) == []
    assert required_lookup_violations(
        planned_writes=_WRITE, anchors=[], ledger=led, agent_spec=spec) == []


def test_non_required_tool_never_gates():
    # plain_read is not required → never a violation even if never run
    spec = _AgentSpec([_McpTool("plain_read", dataset_id="ops.notes", required=False)])
    led = ReadLedger()
    led.note_record_read(rows=[{"application_id": "APP-1"}])
    assert required_lookup_violations(
        planned_writes=_WRITE, anchors=_ANCHORS, ledger=led, agent_spec=spec) == []


# ── W-09 publish validator (required lookup must be bound) ────────────────────
def test_w09_unbound_required_lookup_is_rejected():
    spec = _AgentSpec([_McpTool("cibil_score", dataset_id=None, required=True)])
    out = validate_required_lookup_is_bound(spec)
    assert len(out) == 1
    assert out[0]["rule_id"] == "W-09"
    assert "cibil_score" in out[0]["reason"]


def test_w09_bound_required_lookup_passes():
    spec = _AgentSpec([_McpTool("cibil_score", dataset_id="cibil.credit_score", required=True)])
    assert validate_required_lookup_is_bound(spec) == []


def test_w09_non_required_unbound_tool_passes():
    # unbound but NOT required → fine (a plain semantic read)
    spec = _AgentSpec([_McpTool("plain", dataset_id=None, required=False)])
    assert validate_required_lookup_is_bound(spec) == []


# ── proactive prompt stanza (guide, then enforce) ────────────────────────────
def test_required_lookups_prompt_names_the_tools():
    from runtime import _render_required_lookups_block
    block = _render_required_lookups_block(_spec_with_required_cibil())
    assert "MANDATORY" in block
    assert "cibil_score" in block and "cibil_history" in block
    assert "plain_read" not in block  # not required → not named


def test_required_lookups_prompt_empty_when_none():
    from runtime import _render_required_lookups_block
    spec = _AgentSpec([_McpTool("plain", dataset_id="ds.x", required=False)])
    assert _render_required_lookups_block(spec) == ""


def test_required_lookups_prompt_uses_same_selection_as_gate():
    # a required-but-UNBOUND tool is skipped by both the gate and the prompt
    from runtime import _render_required_lookups_block
    spec = _AgentSpec([_McpTool("unbound", dataset_id=None, required=True)])
    assert _render_required_lookups_block(spec) == ""
