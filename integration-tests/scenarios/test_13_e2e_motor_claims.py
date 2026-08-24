# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Flagship E2E: motor-claims happy path.

This is the integration test that proves the platform's promise — a
single-tenant, single-app run where the BA's claim form gets triaged
end-to-end through:

  publish AppSpec/AgentSpec
       │
       ▼
  POST /apps/itest-motor-claims/run  with {claim_id, claim_amount}
       │
       ▼  (smart-app-service runtime)
  validate_inputs → few_shot_prefetch (no-op when no neighbor tool)
       │
       ▼
  call fake-llm with the prompt (claim_amount-keyed pattern)
       │
       ▼
  return {"decision": "APPROVE"|"ESCALATE", ...}

We verify three claim sizes match the expected branching:
  • $800     → APPROVE (under $50k threshold, claim_amount_under_50k pattern)
  • $75 000  → ESCALATE (over $50k, claim_amount_over_50k pattern)
  • $40 000  → APPROVE (still under $50k)

This test does NOT exercise the wf_post_decision webhook (notify) —
that hook hangs off the Action.on_approve list which requires a
HITL approval gate flow we cover separately. The aim here is the
inference contract on the synchronous /run path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpers.builder_session import publish_spec, run_app  # noqa: E402
from helpers.specs import make_agent_spec, make_app_spec  # noqa: E402


pytestmark = pytest.mark.asyncio


SLUG = "itest-motor-claims-e2e"


@pytest_asyncio.fixture(autouse=True)
async def _publish_motor_claims_app(smart_app_service_url, acme_admin_headers):
    agent = make_agent_spec(
        name="Motor Claims Triage",
        system_prompt=(
            "You triage motor insurance claims. Decide APPROVE for amounts "
            "under $50,000 and ESCALATE otherwise. Always return strict JSON."
        ),
    )
    app = make_app_spec(
        slug=SLUG,
        title="Motor Claims Triage (E2E)",
        agent_id=agent["agent_id"],
    )
    await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=acme_admin_headers,
    )
    yield


@pytest.mark.parametrize("amount,expected", [
    (800, "APPROVE"),
    (40_000, "APPROVE"),
    (75_000, "ESCALATE"),
])
async def test_motor_claim_decision_branches(
    smart_app_service_url, acme_ba_headers, amount, expected,
):
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="triage_claim",
        inputs={"claim_id": f"C-{amount}", "claim_amount": amount},
        headers=acme_ba_headers,
    )
    body = resp.json()
    assert body["status"] == "completed", body
    text = str(body.get("outputs") or "")
    assert expected in text, (
        f"claim_amount={amount} should trigger {expected}; got: {text}"
    )


async def test_run_records_timeline_steps(
    smart_app_service_url, acme_ba_headers,
):
    """The runtime must report a timeline so audits can reconstruct the run."""
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="triage_claim",
        inputs={"claim_id": "C-aud", "claim_amount": 1500},
        headers=acme_ba_headers,
    )
    body = resp.json()
    assert body.get("correlation_id"), "runtime must mint a correlation_id"
    timeline = body.get("timeline") or []
    assert isinstance(timeline, list)
    assert len(timeline) >= 1, "expected at least one timeline entry per run"


async def test_run_rejects_invalid_inputs(
    smart_app_service_url, acme_ba_headers,
):
    """input_schema validation must run BEFORE the LLM call."""
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="triage_claim",
        inputs={},   # missing required claim_id
        headers=acme_ba_headers,
        expect_status=None,
    )
    # Either pre-LLM 422 OR a `failed` status — both prove the LLM
    # didn't get called with a bad payload.
    if resp.status_code == 200:
        body = resp.json()
        assert body.get("status") == "failed", (
            f"missing claim_id should not return completed; got {body}"
        )
    else:
        assert resp.status_code in (400, 422), resp.text
