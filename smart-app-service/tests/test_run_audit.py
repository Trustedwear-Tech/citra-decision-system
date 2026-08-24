# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the /run audit trail.

Covers the runtime-side pieces (structured audit-block extraction, tool
result digests, reference capture on RunResponse) and the main-side
content-hash helper. No Mongo / no LLM — pure logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from config import Settings
from models import AgentSpec, AppSpec, RunRequest
from runtime import (
    _digest_tool_result,
    _extract_audit_block,
    execute_run,
)


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


def _minimal_inputs(schema: dict) -> dict:
    if not schema or schema.get("type") != "object":
        return {}
    inputs: dict = {}
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        t = props.get(key, {}).get("type")
        inputs[key] = {"number": 1, "integer": 1, "boolean": False,
                       "array": [], "object": {}}.get(t, "x")
    return inputs


# ── _extract_audit_block ────────────────────────────────────────────────


def test_extract_audit_block_parses_structured_decision():
    reply = (
        "The claim is straightforward and within policy.\n\n"
        "```json\n"
        "{\n"
        '  "decision": "approve",\n'
        '  "reasoning": "Amount under the SOP threshold; no fraud signals.",\n'
        '  "citations": [\n'
        '    {"type": "past_case", "ref": "case_77", "detail": "0.93 similar"},\n'
        '    {"type": "policy", "ref": "SOP-4.2", "detail": "auto-approve <5000"}\n'
        "  ]\n"
        "}\n"
        "```"
    )
    (human, decision, reasoning, citations, precedents,
     clauses) = _extract_audit_block(reply)

    assert decision == "approve"
    assert precedents == []  # no cited_precedents key → empty, never None
    assert "SOP threshold" in reasoning
    assert len(citations) == 2
    assert citations[0]["ref"] == "case_77"
    # The fenced block is stripped from the human-facing text.
    assert "```" not in human
    assert "straightforward and within policy" in human


def test_extract_audit_block_falls_back_when_no_block():
    (human, decision, reasoning, citations, precedents,
     clauses) = _extract_audit_block(
        "Just a plain answer, no JSON block."
    )
    assert decision is None
    assert reasoning == "Just a plain answer, no JSON block."
    assert human == "Just a plain answer, no JSON block."
    assert citations == []
    assert precedents == []


def test_extract_audit_block_ignores_unrelated_json_fence():
    # A JSON fence that is NOT an audit block must not be mistaken for one:
    # no decision is extracted. (The truncation-recovery fallback strips a
    # TRAILING json fence from the reasoning fallback — so reasoning is the
    # prose only, while the human text keeps the full reply.)
    human, decision, reasoning, _, _p, _c = _extract_audit_block(
        'Here is some data:\n```json\n{"foo": 1}\n```'
    )
    assert decision is None
    assert reasoning == "Here is some data:"
    assert '{"foo": 1}' in human  # full reply preserved for the user


def test_extract_audit_block_empty_input():
    assert _extract_audit_block("") == ("", None, None, [], [], [])


# ── _digest_tool_result ─────────────────────────────────────────────────


def test_digest_tool_result_shapes():
    assert "error" in _digest_tool_result({"error": "boom"})
    assert _digest_tool_result({"samples": [1, 2, 3]}) == "3 sample(s)"
    assert _digest_tool_result({"results": [1]}) == "1 result(s)"
    assert _digest_tool_result({"rows": []}) == "0 row(s)"
    # Unknown shape → bounded JSON digest.
    assert len(_digest_tool_result({"k": "v" * 1000})) <= 240


# ── execute_run audit surface ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_response_carries_structured_audit_fields():
    app_spec, agent_spec = _load_specs()
    action = agent_spec.actions[0]
    action.approval_required = False
    inputs = _minimal_inputs(action.input_schema or {})
    req = RunRequest(action=action.name, inputs=inputs)

    async def _ok(**kw):
        return {
            "role": "assistant",
            "content": (
                "Decision rendered.\n"
                '```json\n{"decision": "refer", '
                '"reasoning": "Fraud signal present.", "citations": []}\n```'
            ),
            "_usage": {"prompt_tokens": 120, "completion_tokens": 30,
                       "total_tokens": 150},
            "_model": "test/model-x",
        }

    with patch("runtime._call_llm", new=_ok):
        resp = await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )

    assert resp.status == "completed"
    assert resp.decision == "refer"
    assert resp.reasoning == "Fraud signal present."
    assert resp.outputs["text"] == "Decision rendered."  # block stripped
    assert resp.model == "test/model-x"
    assert resp.usage.get("total_tokens") == 150
    # references is always present, with both evidence buckets.
    assert "few_shot_samples" in resp.references
    assert "tool_calls" in resp.references


@pytest.mark.asyncio
async def test_failed_run_still_carries_references():
    app_spec, agent_spec = _load_specs()
    action = agent_spec.actions[0]
    action.approval_required = False
    inputs = _minimal_inputs(action.input_schema or {})
    req = RunRequest(action=action.name, inputs=inputs)

    async def _boom(**kw):
        raise RuntimeError("inference offline")

    with patch("runtime._call_llm", new=_boom):
        resp = await execute_run(
            settings=_settings(),
            app_spec=app_spec,
            agent_spec=agent_spec,
            request=req,
        )

    assert resp.status == "failed"
    # The evidence buckets must be present and EMPTY on a failed run — the point
    # is that `references` is never absent, so a consumer can always distinguish
    # "retrieved nothing" from "we did not record what was retrieved".
    # Asserted key-by-key rather than as an exact dict: the envelope also
    # carries clause-memory stamps, and pinning the whole shape would make every
    # future addition look like a regression here.
    for k in ("few_shot_samples", "tool_calls"):
        assert resp.references[k] == []
    assert resp.references["retrieval_count"] == 0


# ── content hash ────────────────────────────────────────────────────────


def test_audit_content_hash_is_stable_and_excludes_self():
    from main import _audit_content_hash

    doc = {"a": 1, "b": "x", "created_at": "2026-05-20T00:00:00Z"}
    h1 = _audit_content_hash(doc)
    h2 = _audit_content_hash({**doc, "content_hash": "ignored", "_id": "oid"})
    assert h1 == h2  # content_hash / _id excluded
    assert _audit_content_hash({**doc, "a": 2}) != h1  # content change detected


def test_extract_audit_block_parses_cited_precedents():
    block = {
        "decision": "recover",
        "reasoning": "Matches precedent.",
        "citations": [],
        "cited_precedents": [
            {"decision_id": "dr_abc123", "relation": "similar", "note": "same tamper pattern"},
            {"decision_id": "dr_xyz9", "relation": "differs", "note": "valid tenant change"},
            {"relation": "similar", "note": "MISSING id -> dropped"},
            {"decision_id": "dr_bad", "relation": "nonsense", "note": "bad relation -> similar"},
        ],
    }
    reply = "Recommend recovery.\n```json\n" + json.dumps(block) + "\n```"
    _, decision, _, _, precedents, _clauses = _extract_audit_block(reply)
    assert decision == "recover"
    assert [p["decision_id"] for p in precedents] == ["dr_abc123", "dr_xyz9", "dr_bad"]
    assert precedents[1]["relation"] == "differs"
    assert precedents[2]["relation"] == "similar"  # invalid relation coerced
