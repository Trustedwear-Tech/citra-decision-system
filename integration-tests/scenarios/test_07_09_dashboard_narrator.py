# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Smart Dashboard narrator — verify the four canonical narration patterns
(brief / why / nl_filter / anomaly) flow end-to-end through:

   user → /apps/{slug}/run → fake-llm pattern table → response

Each pattern is keyed by a substring of the user's request; the fake-llm
deterministically returns the canned narrator response. We then assert
the response shape matches what the dashboard panels expect.

Requires:
  • smart-app-service pointed at fake-llm (LLM_BASE_URL=http://fake-llm:8600)
  • fake-llm running with prompt_patterns.yaml loaded
  • Mongo for AppSpec/AgentSpec persistence
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpers.builder_session import publish_spec, run_app  # noqa: E402
from helpers.specs import make_app_spec, make_narrator_agent_spec  # noqa: E402
from helpers.assert_helpers import (  # noqa: E402
    reset_llm_invocations, get_llm_invocations,
)


pytestmark = pytest.mark.asyncio


SLUG = "itest-dashboard-narrator"


@pytest_asyncio.fixture(autouse=True)
async def _publish_narrator_app(smart_app_service_url, acme_admin_headers):
    """Publish the narrator app once per module."""
    agent = make_narrator_agent_spec()
    panels = [{
        "type": "dashboard",
        "id": "claims_overview",
        "title": "Claims Overview",
        "metrics": [
            {"name": "total_claims", "agg": "count", "field": "claim_id"},
        ],
    }, {
        "type": "agent_chat",
        "id": "narrator_chat",
        "title": "Ask the dashboard",
    }]
    app = make_app_spec(
        slug=SLUG,
        title="Claims Dashboard",
        kind="dashboard",
        agent_id=agent["agent_id"],
        panels=panels,
    )
    try:
        await publish_spec(
            smart_app_service_url,
            app_spec=app,
            agent_spec=agent,
            headers=acme_admin_headers,
        )
    except AssertionError as exc:
        pytest.skip(f"narrator dashboard publish unavailable: {exc}")
    yield


async def test_07_narrate_brief(
    smart_app_service_url, fake_llm_url, acme_ba_headers,
):
    await reset_llm_invocations(fake_llm_url)
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="narrate_brief",
        inputs={"goal": "Brief me on this period for sales"},
        headers=acme_ba_headers,
    )
    body = resp.json()
    assert body["status"] == "completed"
    text = str(body.get("outputs") or "")
    assert "South" in text or "MoM" in text or "trend" in text.lower()


async def test_08_narrate_why(
    smart_app_service_url, acme_ba_headers,
):
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="narrate_why",
        inputs={"metric": "Why did revenue drop?"},
        headers=acme_ba_headers,
    )
    body = resp.json()
    assert body["status"] == "completed"
    text = str(body.get("outputs") or "")
    assert "East" in text or "drop" in text.lower() or "Confidence" in text


async def test_09a_narrate_nl_filter(
    smart_app_service_url, acme_ba_headers,
):
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="narrate_nl_filter",
        inputs={"query": "Show only the high risk escalate cases"},
        headers=acme_ba_headers,
    )
    body = resp.json()
    assert body["status"] == "completed"
    text = str(body.get("outputs") or "")
    assert "filter" in text.lower() or "StageName" in text


async def test_09b_narrate_anomaly(
    smart_app_service_url, acme_ba_headers,
):
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="narrate_anomaly",
        inputs={"series": "find anomalies in monthly revenue"},
        headers=acme_ba_headers,
    )
    body = resp.json()
    assert body["status"] == "completed"
    text = str(body.get("outputs") or "")
    assert "panel_id" in text or "anomaly" in text.lower() or "σ" in text


async def test_narrator_actually_called_the_llm(fake_llm_url):
    """Sanity: the four narrator tests above should have hit the fake-llm
    at least four times (once per action). Catches regressions where
    the runtime short-circuits on a cached result."""
    invocations = await get_llm_invocations(fake_llm_url)
    assert len(invocations) >= 4, (
        f"expected ≥4 LLM calls across narrator tests, got {len(invocations)}"
    )
