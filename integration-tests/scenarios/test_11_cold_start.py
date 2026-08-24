# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Cold-start graceful degradation.

Scenario: a freshly-published Smart App declares a ``neighbor_samples``
tool, but the wf_refresh_history workflow hasn't run yet (or the Milvus
collection is empty). The runtime must NOT silently behave as if no
few-shot grounding was ever requested — the agent should know it's
operating without samples and tell the BA so.

The contract (runtime.py:_FEWSHOT_COLDSTART_NOTE):
  • If a neighbor_samples tool is registered AND every Milvus query
    returns empty, ``_prefetch_few_shot_blocks`` returns the cold-start
    note instead of empty string.
  • Empty string is reserved for the case where no neighbor_samples
    tool was ever registered (the cheap, no-grounding path).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "smart-app-service"))


pytestmark = pytest.mark.asyncio


def _agent_with_neighbors_tool():
    from models import AgentSpec  # noqa: E402
    return AgentSpec(
        agent_id="agent_cold_start",
        name="Cold Start Triage",
        system_prompt="You triage claims grounded in past examples.",
        tools_v2=[{
            "kind": "neighbor_samples",
            "name": "canonical",
            "collection": "claims_history",
            "mode": "canonical",
            "top_k": 5,
        }],
        actions=[],
    )


async def test_cold_start_returns_note_when_milvus_empty():
    from runtime import (  # noqa: E402
        _prefetch_few_shot_blocks, _FEWSHOT_COLDSTART_NOTE,
    )

    agent = _agent_with_neighbors_tool()

    # Mock the dispatcher to consistently return zero samples.
    empty_response = {"samples": []}
    with patch(
        "tools_v2_dispatch._query_neighbor_samples",
        new=AsyncMock(side_effect=lambda **kw: empty_response),
    ):
        block = await _prefetch_few_shot_blocks(
            agent_spec=agent, inputs={"claim_amount": 1200},
        )

    assert block == _FEWSHOT_COLDSTART_NOTE, (
        "cold-start path must inject the explicit note, not empty string — "
        "otherwise the agent never tells the BA grounding is missing."
    )


async def test_cold_start_returns_note_when_milvus_raises():
    """Same path: Milvus down / unconfigured → still inject the note."""
    from runtime import (  # noqa: E402
        _prefetch_few_shot_blocks, _FEWSHOT_COLDSTART_NOTE,
    )

    agent = _agent_with_neighbors_tool()

    async def _raise(**kwargs):
        raise RuntimeError("milvus connection refused")

    with patch(
        "tools_v2_dispatch._query_neighbor_samples",
        new=AsyncMock(side_effect=_raise),
    ):
        block = await _prefetch_few_shot_blocks(
            agent_spec=agent, inputs={"claim_amount": 1200},
        )
    assert block == _FEWSHOT_COLDSTART_NOTE


async def test_no_neighbor_tool_returns_empty_string():
    """Sanity contrast: no tool → no note (the cheap path is preserved)."""
    from runtime import _prefetch_few_shot_blocks  # noqa: E402
    from models import AgentSpec  # noqa: E402

    agent = AgentSpec(
        agent_id="agent_no_neighbor",
        name="Plain Agent",
        system_prompt="You triage claims.",
        tools_v2=[],
        actions=[],
    )
    block = await _prefetch_few_shot_blocks(
        agent_spec=agent, inputs={"claim_amount": 800},
    )
    assert block == ""
