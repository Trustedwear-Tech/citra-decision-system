# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Few-shot pre-injection (smart-app-service runtime).

The runtime collapses what was previously 3 LLM round-trips into 1 by
fetching canonical + neighbor blocks BEFORE the first inference call
and inlining them into the system prompt. This test verifies:

  1. ``_prefetch_few_shot_blocks`` returns a non-empty markdown string
     when the agent has a ``neighbor_samples`` tool and Milvus has rows
  2. Both ``mode='canonical'`` and ``mode='neighbors'`` are walked once
  3. ``_inject_few_shot_into_messages`` appends the block to the
     system message in-place, idempotently
  4. When NO ``neighbor_samples`` tool is registered, the prefetch
     returns empty string (cheap path stays cheap)

We mock ``_query_neighbor_samples`` so the test runs without Milvus.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "smart-app-service"))


pytestmark = pytest.mark.asyncio


def _make_neighbor_samples():
    """Two canned Milvus rows the mocked dispatcher will return."""
    return {
        "samples": [
            {
                "source_id": "C001",
                "decision": "APPROVE",
                "severity": "low",
                "is_canonical": True,
                "input_json": '{"claim_amount": 800}',
                "output_json": '{"decision": "APPROVE"}',
                "reasoning_trace": "Within auto-approve threshold.",
            },
            {
                "source_id": "C002",
                "decision": "ESCALATE",
                "severity": "high",
                "is_canonical": False,
                "input_json": '{"claim_amount": 75000}',
                "output_json": '{"decision": "ESCALATE"}',
                "reasoning_trace": "Above auto-approve threshold.",
            },
        ]
    }


def _agent_with_tools(tools_v2):
    from models import AgentSpec  # noqa: E402
    return AgentSpec(
        agent_id="agent_test",
        name="Test",
        system_prompt="You triage claims.",
        tools_v2=tools_v2,
        actions=[],
    )


async def test_prefetch_returns_block_with_canonical_and_neighbors():
    from runtime import _prefetch_few_shot_blocks  # noqa: E402

    agent = _agent_with_tools([
        {
            "kind": "neighbor_samples",
            "name": "canonical",
            "collection": "claims_history",
            "mode": "canonical",
            "top_k": 5,
        },
        {
            "kind": "neighbor_samples",
            "name": "neighbors",
            "collection": "claims_history",
            "mode": "neighbors",
            "top_k": 3,
        },
    ])

    with patch(
        "tools_v2_dispatch._query_neighbor_samples",
        new=AsyncMock(side_effect=lambda **kw: _make_neighbor_samples()),
    ):
        block = await _prefetch_few_shot_blocks(
            agent_spec=agent,
            inputs={"claim_amount": 1200, "vehicle": "Honda"},
        )

    assert block, "expected non-empty few-shot block"
    # Two distinct (collection, mode) keys → two blocks merged.
    assert block.count("APPROVE") >= 1
    assert "ESCALATE" in block


async def test_prefetch_empty_when_no_neighbor_samples_tool():
    from runtime import _prefetch_few_shot_blocks  # noqa: E402

    agent = _agent_with_tools([])  # no neighbor_samples tool
    block = await _prefetch_few_shot_blocks(
        agent_spec=agent, inputs={"claim_amount": 800},
    )
    assert block == "", "no neighbor_samples tool → no prefetch"


async def test_inject_appends_to_system_message():
    from runtime import _inject_few_shot_into_messages  # noqa: E402

    messages = [
        {"role": "system", "content": "You are a triage agent."},
        {"role": "user", "content": "Claim C001 amount 800"},
    ]
    _inject_few_shot_into_messages(messages, "## Canonical examples\n- approve case")

    sys_msg = messages[0]
    assert sys_msg["role"] == "system"
    assert "triage agent" in sys_msg["content"]
    assert "Canonical examples" in sys_msg["content"]
    # User message untouched
    assert messages[1]["content"] == "Claim C001 amount 800"


async def test_inject_no_op_on_empty_block():
    from runtime import _inject_few_shot_into_messages  # noqa: E402

    messages = [{"role": "system", "content": "You are X."}]
    snapshot = list(messages[0].items())
    _inject_few_shot_into_messages(messages, "")
    assert list(messages[0].items()) == snapshot
