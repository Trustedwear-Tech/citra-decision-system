"""Unit tests for the read-before-write evidence guard (evidence_guard.py).

These are pure — no runtime, no LLM, no MCP. They pin the guarantee: a staged
write must be backed by an actual read of the anchor record, and of every
bound-and-required media tool, keyed to the caller-supplied id.
"""
import pytest

from evidence_guard import (
    Anchor,
    EvidenceNotReviewed,
    ReadLedger,
    assert_reads_cover_writes,
    required_media_tools,
    resolve_anchor_ids,
)


# ── lightweight stand-ins for the pydantic models (only fields the guard reads)
class _Action:
    def __init__(self, input_schema):
        self.input_schema = input_schema


class _MediaTool:
    def __init__(self, name, kind, data_source_id=None, required=True):
        self.name = name
        self.kind = kind
        self.data_source_id = data_source_id
        self.required = required


class _AgentSpec:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


_ROUTE_SCHEMA = {
    "type": "object",
    "required": ["complaint_id"],
    "properties": {"complaint_id": {"type": "string"}},
}
_WRITE = [{"source_id": "field_operations", "dataset_id": "field_operations.complaints",
           "action_id": "route_complaint", "payload": {"complaint_id": "CMP-1"}}]


# ── resolve_anchor_ids ───────────────────────────────────────────────────────
def test_anchor_is_required_string_id_present_in_inputs():
    anchors = resolve_anchor_ids(_Action(_ROUTE_SCHEMA), {"complaint_id": "CMP-1"})
    assert anchors == [Anchor(field="complaint_id", value="CMP-1")]


def test_non_string_required_props_are_not_anchors():
    schema = {
        "type": "object",
        "required": ["inspection_id", "count"],
        "properties": {
            "inspection_id": {"type": "string"},
            "count": {"type": "integer"},
        },
    }
    anchors = resolve_anchor_ids(_Action(schema), {"inspection_id": "INS-9", "count": 3})
    assert anchors == [Anchor(field="inspection_id", value="INS-9")]


def test_missing_or_blank_input_yields_no_anchor():
    assert resolve_anchor_ids(_Action(_ROUTE_SCHEMA), {}) == []
    assert resolve_anchor_ids(_Action(_ROUTE_SCHEMA), {"complaint_id": "   "}) == []


# ── ledger coverage ──────────────────────────────────────────────────────────
def test_record_covered_by_filter_value():
    led = ReadLedger()
    led.note_record_read(args={"filters": {"complaint_id": "CMP-1"}})
    assert led.record_covers("CMP-1")
    assert not led.record_covers("CMP-2")


def test_record_covered_by_returned_row():
    led = ReadLedger()
    led.note_record_read(args={"query": "new complaints"},
                         rows=[{"complaint_id": "CMP-1", "status": "new"}])
    assert led.record_covers("CMP-1")


def test_media_read_also_covers_record_and_media():
    led = ReadLedger()
    led.note_media_read(tool_name="defect_photo", record_id="INS-9")
    assert led.record_covers("INS-9")
    assert led.media_covers("defect_photo", {"INS-9"})
    assert not led.media_covers("defect_photo", {"INS-8"})


# ── required_media_tools inference ───────────────────────────────────────────
def test_bound_media_tool_is_required_by_default():
    spec = _AgentSpec([_MediaTool("defect_photo", "image_analyze", data_source_id="ds_ins")])
    assert [t.name for t in required_media_tools(spec)] == ["defect_photo"]


def test_required_false_opts_out():
    spec = _AgentSpec([_MediaTool("defect_photo", "image_analyze",
                                  data_source_id="ds_ins", required=False)])
    assert required_media_tools(spec) == []


def test_unbound_media_tool_not_enforced():
    spec = _AgentSpec([_MediaTool("adhoc_ocr", "image_analyze", data_source_id=None)])
    assert required_media_tools(spec) == []


# ── the gate ─────────────────────────────────────────────────────────────────
def test_gate_passes_when_record_read():
    led = ReadLedger()
    led.note_record_read(args={"filters": {"complaint_id": "CMP-1"}})
    assert_reads_cover_writes(
        planned_writes=_WRITE,
        anchors=[Anchor("complaint_id", "CMP-1")],
        ledger=led,
        agent_spec=_AgentSpec([]),
    )  # no raise


def test_gate_raises_when_record_never_read():
    with pytest.raises(EvidenceNotReviewed) as ei:
        assert_reads_cover_writes(
            planned_writes=_WRITE,
            anchors=[Anchor("complaint_id", "CMP-1")],
            ledger=ReadLedger(),  # empty — agent read nothing
            agent_spec=_AgentSpec([]),
        )
    assert "CMP-1" in str(ei.value)


def test_gate_raises_when_required_image_not_reviewed():
    led = ReadLedger()
    led.note_record_read(args={"filters": {"inspection_id": "INS-9"}})  # record read...
    spec = _AgentSpec([_MediaTool("defect_photo", "image_analyze", data_source_id="ds_ins")])
    with pytest.raises(EvidenceNotReviewed) as ei:
        assert_reads_cover_writes(
            planned_writes=_WRITE,
            anchors=[Anchor("inspection_id", "INS-9")],
            ledger=led,  # ...but photo never analysed
            agent_spec=spec,
        )
    assert "defect_photo" in str(ei.value)


def test_gate_passes_when_record_and_required_image_reviewed():
    led = ReadLedger()
    led.note_record_read(args={"filters": {"inspection_id": "INS-9"}})
    led.note_media_read(tool_name="defect_photo", record_id="INS-9")
    spec = _AgentSpec([_MediaTool("defect_photo", "image_analyze", data_source_id="ds_ins")])
    assert_reads_cover_writes(
        planned_writes=_WRITE,
        anchors=[Anchor("inspection_id", "INS-9")],
        ledger=led,
        agent_spec=spec,
    )  # no raise


def test_gate_noop_when_no_writes_or_no_anchors():
    # nothing staged → nothing to guard
    assert_reads_cover_writes(planned_writes=[], anchors=[Anchor("x", "1")],
                              ledger=ReadLedger(), agent_spec=_AgentSpec([]))
    # no caller id → nothing to prove was read
    assert_reads_cover_writes(planned_writes=_WRITE, anchors=[],
                              ledger=ReadLedger(), agent_spec=_AgentSpec([]))
