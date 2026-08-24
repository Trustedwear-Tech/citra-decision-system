# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the publish-time tool source-id resolution check.

Catches the most common BA mistake: a typo'd source_id on an mcp/rag tool
that would otherwise sail through validation and fail at runtime.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_validators import validate_tool_sources_resolvable  # noqa: E402
from models import AgentSpec, McpTool, RagTool, NeighborSamplesTool  # noqa: E402
from config import Settings  # noqa: E402
from discovery_cache import DiscoveryError  # noqa: E402


def _agent_with_tools(*tools) -> AgentSpec:
    return AgentSpec(
        spec_version="v0",
        agent_id="test-agent",
        name="Test Agent",
        system_prompt="be useful",
        tools_v2=list(tools),
    )


@pytest.fixture
def settings():
    s = Settings()
    s.discovery_service_url = "http://discovery:8080"
    return s


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tools_returns_empty(settings):
    agent = _agent_with_tools()
    result = await validate_tool_sources_resolvable(
        agent, settings=settings, auth_header="Bearer abc"
    )
    assert result == []


@pytest.mark.asyncio
async def test_no_mcp_or_rag_tools_returns_empty(settings):
    """neighbor_samples / other tool kinds aren't checked here."""
    agent = _agent_with_tools(
        NeighborSamplesTool(
            name="neighbors", collection="samples_x", mode="canonical"
        ),
    )
    result = await validate_tool_sources_resolvable(
        agent, settings=settings, auth_header="Bearer abc"
    )
    assert result == []


@pytest.mark.asyncio
async def test_resolvable_mcp_tool_passes(settings):
    agent = _agent_with_tools(
        McpTool(name="claims_lookup", source_id="sap_claims", tool_name="get_claim"),
    )
    with patch("publish_validators.resolve_source", new=AsyncMock(return_value=None)):
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header="Bearer abc"
        )
    assert result == []


@pytest.mark.asyncio
async def test_dedup_same_source_across_tools(settings):
    """Same source_id used by multiple tools resolves once."""
    agent = _agent_with_tools(
        McpTool(name="t1", source_id="sap_claims", tool_name="a"),
        McpTool(name="t2", source_id="sap_claims", tool_name="b"),
        RagTool(name="t3", source_id="other_src"),
    )
    call_count = {"n": 0}

    async def fake_resolve(**kwargs):
        call_count["n"] += 1
        return None

    with patch("publish_validators.resolve_source", new=fake_resolve):
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header="Bearer abc"
        )
    # 2 unique kind+source_id combos: mcp:sap_claims, rag:other_src
    assert call_count["n"] == 2
    assert result == []


# ---------------------------------------------------------------------------
# Negative cases — block publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unresolvable_source_blocks_publish(settings):
    agent = _agent_with_tools(
        McpTool(name="claims_lookup", source_id="typo_claims", tool_name="get_claim"),
    )

    async def fake_resolve(**kwargs):
        raise DiscoveryError("source_not_found", "not found", 404)

    with patch("publish_validators.resolve_source", new=fake_resolve):
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header="Bearer abc"
        )
    assert len(result) == 1
    assert result[0]["source_id"] == "typo_claims"
    assert result[0]["reason"] == "source_not_found"
    assert result[0]["kind"] == "mcp"


@pytest.mark.asyncio
async def test_no_endpoint_blocks_publish(settings):
    """A registered source with no query_endpoint also blocks."""
    agent = _agent_with_tools(
        RagTool(name="policies_search", source_id="broken_src"),
    )

    async def fake_resolve(**kwargs):
        raise DiscoveryError(
            "discovery_no_endpoint", "no endpoint registered", 502
        )

    with patch("publish_validators.resolve_source", new=fake_resolve):
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header="Bearer abc"
        )
    assert len(result) == 1
    assert result[0]["reason"] == "discovery_no_endpoint"


# ---------------------------------------------------------------------------
# Skip-but-don't-block paths (infrastructure issues)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_unreachable_does_not_block(settings):
    """Network blip / 502 is infra issue — allow publish."""
    agent = _agent_with_tools(
        McpTool(name="t1", source_id="sap_claims", tool_name="get"),
    )

    async def fake_resolve(**kwargs):
        raise DiscoveryError("discovery_unreachable", "connection reset", 502)

    with patch("publish_validators.resolve_source", new=fake_resolve):
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header="Bearer abc"
        )
    # Infra issue is logged, not blocked.
    assert result == []


@pytest.mark.asyncio
async def test_no_jwt_skips_check(settings):
    """Service-to-service publish without a user JWT skips the check."""
    agent = _agent_with_tools(
        McpTool(name="t1", source_id="sap_claims", tool_name="get"),
    )
    # Should NOT call resolve_source at all.
    with patch("publish_validators.resolve_source", new=AsyncMock()) as mock:
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header=""
        )
    assert mock.call_count == 0
    assert result == []


@pytest.mark.asyncio
async def test_no_discovery_url_skips_check(settings):
    """Deployment without discovery configured = skip the check."""
    settings.discovery_service_url = ""
    agent = _agent_with_tools(
        McpTool(name="t1", source_id="sap_claims", tool_name="get"),
    )
    with patch("publish_validators.resolve_source", new=AsyncMock()) as mock:
        result = await validate_tool_sources_resolvable(
            agent, settings=settings, auth_header="Bearer abc"
        )
    assert mock.call_count == 0
    assert result == []
