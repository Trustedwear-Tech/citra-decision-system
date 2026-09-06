# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""The runtime reviews every item itself, and the gate knows what it skipped.

Pure: the MCP read and the tool dispatcher are stubbed. What is pinned:

  * every document that belongs to the anchor is dispatched, once, by the
    runtime - not left to the model
  * the findings carry the same fields a model-made finding carries
  * the cache is seeded so a repeat call dedupes
  * the ledger's EXPECTED set is what the gate then checks, by id
  * enumeration failure is a failed check, not a pass
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from evidence_guard import Anchor, ReadLedger, evidence_violations, required_lookup_violations
from item_pass import cache_key, coverage_from_timeline, run_item_pass


class _Tool:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Spec:
    def __init__(self, tools_v2, data_sources=()):
        self.tools_v2 = tools_v2
        self.data_sources = list(data_sources)


def _settings():
    return _Tool(item_pass_max_items=25)


_DISPATCH = {
    "review_claim_document": {
        "kind": "doc_extract", "name": "review_claim_document", "task_type": "claim-document",
        "data_source_id": "ds_documents", "url_column": "file_url", "key_field": "document_id",
        "required": True,
    },
    "lookup_policy": {"kind": "mcp", "name": "lookup_policy", "dataset_id": "insurance_claims.policies",
                      "dataset_kind": "sql"},
}
_APP = _Spec(tools_v2=[], data_sources=[{"id": "ds_documents", "ref": "insurance_claims.claim_documents"}])
_AGENT = _Spec(tools_v2=[
    _Tool(kind="mcp", name="lookup_documents", dataset_id="insurance_claims.claim_documents", dataset_kind="sql"),
    _Tool(kind="doc_extract", name="review_claim_document", data_source_id="ds_documents",
          key_field="document_id", required=True),
])
_ANCHORS = [Anchor(field="claim_id", value="CLM-1")]
_ROWS = [{"document_id": f"DOC-{i}", "claim_id": "CLM-1", "doc_type": "invoice"} for i in range(1, 6)]


def _finding_for(args):
    return {"item_id": args["record_id"], "item_type": "claim-document", "modality": "document",
            "fields": {"amount": 100}, "recommendation": "accept", "confidence": 0.9,
            "rationale": "looks genuine", "citations": [{"source_url": "s3://x"}]}


@pytest.mark.asyncio
async def test_every_document_is_reviewed_by_the_runtime():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[{"claim_id": "CLM-1"}])  # anchor_prefetch does this
    read = AsyncMock(return_value={"rows": _ROWS, "error": None})
    dispatched = []

    async def _dispatch(**kw):
        dispatched.append(kw["arguments"])
        return _finding_for(kw["arguments"])

    with patch("proxy_clients.call_dept_mcp_read", new=read), \
         patch("tools_v2_dispatch.dispatch_tools_v2_call", new=_dispatch):
        out = await run_item_pass(
            settings=_settings(), agent_spec=_AGENT, app_spec=_APP, dispatch_table=_DISPATCH,
            anchors=_ANCHORS, anchor_row={"claim_id": "CLM-1", "claimed_amount": 5000},
            action_name="triage_claim", auth_header="Bearer t", ledger=ledger, correlation_id="r1",
        )

    # enumerated by the parent key, from the item dataset, with the anchor value
    q = read.call_args.kwargs
    assert q["dataset_id"] == "insurance_claims.claim_documents" and q["kind"] == "sql"
    assert "claim_id" in q["query"] and "CLM-1" in q["query"]
    # one dispatch per document, by id, with case context
    assert [d["record_id"] for d in dispatched] == [f"DOC-{i}" for i in range(1, 6)]
    assert all("CLM-1" in d["query"] and "claimed_amount=5000" in d["query"] for d in dispatched)
    # findings carry the model-path projection
    assert len(out.findings) == 5 and {"item_id", "modality", "citations", "sop_fingerprint"} <= set(out.findings[0])
    # cache seeded in the loop's own key form
    assert cache_key("review_claim_document", dispatched[0]) in out.cache
    # the ledger knows what was expected and what was produced
    assert ledger.expected_items["review_claim_document"] == {f"DOC-{i}" for i in range(1, 6)}
    assert ledger.item_findings_produced["review_claim_document"] == ledger.expected_items["review_claim_document"]
    assert out.coverage["review_claim_document"] == {"expected": 5, "produced": 5, "missing": [], "error": None}
    assert "do NOT call the item tools again" in out.block
    # the gate is satisfied - on a CHILD table, where the anchor rule never could be
    assert evidence_violations(planned_writes=[{"tool": "w"}], anchors=_ANCHORS,
                               ledger=ledger, agent_spec=_AGENT) == []


@pytest.mark.asyncio
async def test_a_document_the_tool_failed_on_is_named_by_the_gate():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[{"claim_id": "CLM-1"}])  # anchor_prefetch does this

    async def _dispatch(**kw):
        a = kw["arguments"]
        if a["record_id"] == "DOC-3":
            return {"error": "PDF has no text layer and no extractable page images", "code": "pdf_no_content"}
        return _finding_for(a)

    with patch("proxy_clients.call_dept_mcp_read", new=AsyncMock(return_value={"rows": _ROWS})), \
         patch("tools_v2_dispatch.dispatch_tools_v2_call", new=_dispatch):
        out = await run_item_pass(
            settings=_settings(), agent_spec=_AGENT, app_spec=_APP, dispatch_table=_DISPATCH,
            anchors=_ANCHORS, anchor_row=None, action_name="triage_claim", auth_header=None,
            ledger=ledger, correlation_id="r2",
        )
    assert out.coverage["review_claim_document"]["missing"] == ["DOC-3"]
    assert "DOC-3: FAILED" in out.block
    unmet = evidence_violations(planned_writes=[{"tool": "w"}], anchors=_ANCHORS,
                                ledger=ledger, agent_spec=_AGENT)
    assert unmet and "1 of 5 items were never reviewed by review_claim_document: DOC-3" in unmet[0]
    # and the officer's notice derives from the timeline the same way
    cov = coverage_from_timeline(out.timeline)
    assert cov["review_claim_document"]["missing"] == ["DOC-3"]


@pytest.mark.asyncio
async def test_enumeration_failure_is_a_failed_check_not_a_pass():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[{"claim_id": "CLM-1"}])  # anchor_prefetch does this
    with patch("proxy_clients.call_dept_mcp_read",
               new=AsyncMock(return_value={"rows": [], "error": "column \"claim_id\" does not exist"})), \
         patch("tools_v2_dispatch.dispatch_tools_v2_call", new=AsyncMock()) as disp:
        out = await run_item_pass(
            settings=_settings(), agent_spec=_AGENT, app_spec=_APP, dispatch_table=_DISPATCH,
            anchors=_ANCHORS, anchor_row=None, action_name="triage_claim", auth_header=None,
            ledger=ledger, correlation_id="r3",
        )
    disp.assert_not_called()
    assert "review_claim_document" in ledger.enumeration_errors
    unmet = evidence_violations(planned_writes=[{"tool": "w"}], anchors=_ANCHORS,
                                ledger=ledger, agent_spec=_AGENT)
    assert unmet and "could not enumerate" in unmet[0] and "does not exist" in unmet[0]
    assert out.timeline[0]["status"] == "error"


@pytest.mark.asyncio
async def test_over_cap_reviews_the_first_n_and_says_so():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[{"claim_id": "CLM-1"}])  # anchor_prefetch does this
    rows = [{"document_id": f"DOC-{i}", "claim_id": "CLM-1"} for i in range(1, 31)]
    with patch("proxy_clients.call_dept_mcp_read", new=AsyncMock(return_value={"rows": rows})), \
         patch("tools_v2_dispatch.dispatch_tools_v2_call",
               new=AsyncMock(side_effect=lambda **kw: _finding_for(kw["arguments"]))):
        out = await run_item_pass(
            settings=_Tool(item_pass_max_items=25), agent_spec=_AGENT, app_spec=_APP,
            dispatch_table=_DISPATCH, anchors=_ANCHORS, anchor_row=None, action_name="a",
            auth_header=None, ledger=ledger, correlation_id="r4",
        )
    c = out.coverage["review_claim_document"]
    assert c["expected"] == 25 and c["produced"] == 25 and "more than 25 items" in c["error"]
    assert out.timeline[0]["status"] == "partial"


def test_unbound_media_tools_are_left_to_the_model():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[{"claim_id": "CLM-1"}])  # anchor_prefetch does this
    # no data_source_id -> nothing to enumerate -> not touched, not a violation
    table = {"analyze_photo": {"kind": "image_analyze", "name": "analyze_photo", "task_type": "photo"}}
    import asyncio
    out = asyncio.run(run_item_pass(
        settings=_settings(), agent_spec=_Spec(tools_v2=[]), app_spec=_APP, dispatch_table=table,
        anchors=_ANCHORS, anchor_row=None, action_name="a", auth_header=None, ledger=ledger,
        correlation_id="r5"))
    assert out.findings == [] and out.coverage == {} and ledger.expected_items == {}


# ── API checks ──────────────────────────────────────────────────────────────

_API_DISPATCH = {
    "bureau_credit": {"kind": "mcp", "name": "bureau_credit", "source_id": "bureau", "tool_name": "credit_score",
                      "dataset_id": "bureau.credit_score", "dataset_kind": "rest", "required": True,
                      "lookup_inputs": {"required": ["pan"], "properties": {"pan": {"type": "string"}}}},
    "credit_check": {"kind": "check_evaluate", "name": "credit_check", "task_type": "credit-check",
                     "evaluates": "bureau_credit", "input_map": {"pan": "applicant_pan"}},
}
_API_AGENT = _Spec(tools_v2=[
    _Tool(kind="mcp", name="bureau_credit", dataset_id="bureau.credit_score", dataset_kind="rest", required=True),
    _Tool(kind="check_evaluate", name="credit_check", task_type="credit-check", evaluates="bureau_credit"),
])
_LOAN = [Anchor(field="application_id", value="APP-9")]
_LOAN_ROW = {"application_id": "APP-9", "applicant_pan": "ABCDE1234F", "amount": 250000}


@pytest.mark.asyncio
async def test_an_api_check_is_run_by_the_runtime_with_the_records_own_values():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[_LOAN_ROW])
    calls = []

    async def _dispatch(**kw):
        calls.append((kw["tool_name"], kw["arguments"]))
        if kw["tool_name"] == "bureau_credit":
            return {"rows": [{"credit_score": 712, "score_band": "good"}]}
        return {"item_id": kw["arguments"]["item_id"], "item_type": "credit-check", "modality": "api",
                "fields": {"credit_score": 712}, "recommendation": "pass", "confidence": 0.8,
                "rationale": "above the 700 floor", "citations": []}

    with patch("tools_v2_dispatch.dispatch_tools_v2_call", new=_dispatch):
        out = await run_item_pass(
            settings=_settings(), agent_spec=_API_AGENT, app_spec=_Spec(tools_v2=[]), dispatch_table=_API_DISPATCH,
            anchors=_LOAN, anchor_row=_LOAN_ROW, action_name="decide_loan", auth_header=None,
            ledger=ledger, correlation_id="a1",
        )
    # the lookup ran with the PAN read off the record through input_map, then the check judged its row
    assert calls[0] == ("bureau_credit", {"filters": {"pan": "ABCDE1234F"}})
    assert calls[1][0] == "credit_check" and calls[1][1]["data"] == {"credit_score": 712, "score_band": "good"}
    assert calls[1][1]["item_id"] == "credit-check" and "APP-9" in calls[1][1]["query"]
    assert [f["item_id"] for f in out.findings] == ["credit-check"]
    assert out.coverage["credit_check"] == {"expected": 1, "produced": 1, "missing": [], "error": None}
    # the runtime's own call satisfies the required-lookup gate and the check gate
    assert required_lookup_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger,
                                      agent_spec=_API_AGENT) == []
    assert evidence_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger,
                               agent_spec=_API_AGENT) == []
    assert "bureau_credit for {'pan': 'ABCDE1234F'}" in out.block


@pytest.mark.asyncio
async def test_a_missing_input_column_blocks_with_the_column_named():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[{"application_id": "APP-9"}])
    with patch("tools_v2_dispatch.dispatch_tools_v2_call", new=AsyncMock()) as disp:
        await run_item_pass(
            settings=_settings(), agent_spec=_API_AGENT, app_spec=_Spec(tools_v2=[]), dispatch_table=_API_DISPATCH,
            anchors=_LOAN, anchor_row={"application_id": "APP-9"}, action_name="a", auth_header=None,
            ledger=ledger, correlation_id="a2",
        )
    disp.assert_not_called()
    unmet = evidence_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger, agent_spec=_API_AGENT)
    assert unmet and "applicant_pan" in unmet[0] and "'pan'" in unmet[0]


@pytest.mark.asyncio
async def test_a_failed_lookup_leaves_the_check_unreviewed_and_the_gate_says_so():
    ledger = ReadLedger()
    ledger.note_record_read(rows=[_LOAN_ROW])
    with patch("tools_v2_dispatch.dispatch_tools_v2_call",
               new=AsyncMock(return_value={"error": "bureau returned 503"})):
        out = await run_item_pass(
            settings=_settings(), agent_spec=_API_AGENT, app_spec=_Spec(tools_v2=[]), dispatch_table=_API_DISPATCH,
            anchors=_LOAN, anchor_row=_LOAN_ROW, action_name="a", auth_header=None,
            ledger=ledger, correlation_id="a3",
        )
    assert out.coverage["credit_check"]["missing"] == ["credit-check"] and "503" in out.coverage["credit_check"]["error"]
    unmet = evidence_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger, agent_spec=_API_AGENT)
    assert any("1 of 1 items were never reviewed by credit_check" in u for u in unmet)
    # the lookup never ran either - that gate fires too, on its own
    assert required_lookup_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger,
                                      agent_spec=_API_AGENT)


def test_a_required_check_the_pass_never_reached_is_a_violation():
    # ITEM_PASS_MODE=off and the model never called it: no expectation, no finding
    ledger = ReadLedger()
    ledger.note_record_read(rows=[_LOAN_ROW])
    ledger.note_lookup_read(tool_name="bureau_credit")
    unmet = evidence_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger, agent_spec=_API_AGENT)
    assert unmet and "required check 'credit_check'" in unmet[0]
    ledger.note_item_finding(tool_name="credit_check", item_id="credit-check")  # the model did judge it
    assert evidence_violations(planned_writes=[{"tool": "w"}], anchors=_LOAN, ledger=ledger, agent_spec=_API_AGENT) == []
