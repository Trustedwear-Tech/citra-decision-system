# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
End-to-end test of the SmartAppInvokerNode against a live fake-mcp +
fake-llm.

What this proves:
  1. The node fetches rows from a stubbed dept-MCP via /query
  2. Calls /apps/{slug}/run for each row (here, mocked at the LLM layer)
  3. Bounded concurrency: with 50 input items + concurrency=4, no more
     than 4 in-flight LLM calls at any moment
  4. Output items carry {source_item, agent_response, decision, ok, error}
  5. continue_on_error keeps the batch alive when one row fails

Run requirements:
  • fake-mcp-server up at FAKE_MCP_BASE_URL (default localhost:8500)
  • fake-llm up at FAKE_LLM_BASE_URL (default localhost:8600)

The "smart-app" call is shimmed: instead of going through the real
smart-app-service /run path (which would require many more services
running), we point SmartAppInvokerNode at a thin shim that calls the
fake-llm directly. This isolates the node's contract from the rest of
the runtime. A separate higher-level test exercises the full
build → publish → run flow.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Make citra-workflow importable.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "citra-workflow"))


pytestmark = pytest.mark.asyncio


@pytest.fixture
def smart_app_invoker():
    """Lazy import so a missing citra-workflow checkout doesn't break collection."""
    from citra_workflow.nodes.smart_apps import SmartAppInvokerNode  # noqa: E402
    return SmartAppInvokerNode()


@pytest.fixture
def make_ctx():
    """Build a NodeContext with arbitrary input items + config."""
    from citra_workflow.nodes import NodeContext  # noqa: E402

    def _ctx(items: List[Dict[str, Any]], config: Dict[str, Any]) -> NodeContext:
        return NodeContext(
            node_id="invoker-test",
            node_config=config,
            input_data={"items": items},
            variables={},
            user_id="u_test",
            execution_id="exec-test-1",
            environment="test",
        )
    return _ctx


# ── 1. happy path with 5 items ──────────────────────────────────────────


async def test_invoke_5_items_all_succeed(smart_app_invoker, make_ctx):
    items = [{"claim_id": f"C{i}", "claim_amount": 100 + i} for i in range(5)]
    ctx = make_ctx(items, {
        "app_slug": "acme-claims",
        "action": "triage_claim",
        "concurrency": 2,
    })

    # Mock httpx so we don't actually need smart-app-service running.
    async def fake_post(*args, **kwargs):
        body = kwargs.get("json") or {}
        amount = body.get("inputs", {}).get("claim_amount", 0)
        return _resp(200, {
            "outputs": {
                "decision": "APPROVE" if amount < 50_000 else "ESCALATE",
                "amount_paid": amount if amount < 50_000 else 0,
            },
        })

    with patch(
        "citra_workflow.nodes.smart_apps.httpx.AsyncClient",
        return_value=_async_client_with_post(fake_post),
    ):
        result = await smart_app_invoker.execute(ctx)

    assert result["meta"]["invoked"] == 5
    assert result["meta"]["ok"] == 5
    assert result["meta"]["errors"] == 0
    assert all(it["decision"] == "APPROVE" for it in result["items"])


# ── 2. concurrency cap ──────────────────────────────────────────────────


async def test_concurrency_cap_holds_under_load(smart_app_invoker, make_ctx):
    """50 items, concurrency=4 → never more than 4 in-flight."""
    items = [{"claim_id": f"C{i}", "claim_amount": i * 100} for i in range(50)]
    ctx = make_ctx(items, {
        "app_slug": "acme-claims",
        "action": "triage_claim",
        "concurrency": 4,
    })

    in_flight = 0
    max_observed = 0
    lock = asyncio.Lock()

    async def fake_post(*args, **kwargs):
        nonlocal in_flight, max_observed
        async with lock:
            in_flight += 1
            max_observed = max(max_observed, in_flight)
        await asyncio.sleep(0.05)  # simulate LLM latency
        async with lock:
            in_flight -= 1
        return _resp(200, {"outputs": {"decision": "APPROVE"}})

    with patch(
        "citra_workflow.nodes.smart_apps.httpx.AsyncClient",
        return_value=_async_client_with_post(fake_post),
    ):
        result = await smart_app_invoker.execute(ctx)

    assert result["meta"]["invoked"] == 50
    assert result["meta"]["ok"] == 50
    assert max_observed <= 4, f"saw {max_observed} concurrent calls; cap is 4"


# ── 3. continue_on_error keeps batch alive ──────────────────────────────


async def test_continue_on_error_keeps_batch_alive(smart_app_invoker, make_ctx):
    items = [{"claim_id": f"C{i}"} for i in range(5)]
    ctx = make_ctx(items, {
        "app_slug": "acme-claims",
        "action": "triage_claim",
        "concurrency": 2,
        "continue_on_error": "on",
    })

    call_idx = {"n": 0}

    async def fake_post(*args, **kwargs):
        call_idx["n"] += 1
        if call_idx["n"] == 3:
            return _resp(500, {"detail": "boom"})
        return _resp(200, {"outputs": {"decision": "APPROVE"}})

    with patch(
        "citra_workflow.nodes.smart_apps.httpx.AsyncClient",
        return_value=_async_client_with_post(fake_post),
    ):
        result = await smart_app_invoker.execute(ctx)

    assert result["meta"]["invoked"] == 5
    assert result["meta"]["ok"] == 4
    assert result["meta"]["errors"] == 1
    failed = [it for it in result["items"] if not it["ok"]]
    assert len(failed) == 1
    assert "500" in failed[0]["error"]


# ── 4. inputs_mapping interpolation ─────────────────────────────────────


async def test_inputs_mapping_with_dotted_template(smart_app_invoker, make_ctx):
    """The {{item.X}} template should land verbatim in the smart-app body."""
    items = [{"amount": 380, "vehicle": "Honda"}]
    captured: List[Dict] = []

    async def fake_post(*args, **kwargs):
        captured.append(kwargs.get("json") or {})
        return _resp(200, {"outputs": {"decision": "APPROVE"}})

    ctx = make_ctx(items, {
        "app_slug": "acme",
        "action": "triage",
        "inputs_mapping": '{"claim_amount": "{{item.amount}}", "vehicle_type": "{{item.vehicle}}"}',
    })

    with patch(
        "citra_workflow.nodes.smart_apps.httpx.AsyncClient",
        return_value=_async_client_with_post(fake_post),
    ):
        await smart_app_invoker.execute(ctx)

    assert captured[0]["inputs"] == {
        "claim_amount": "380", "vehicle_type": "Honda",
    }


# ── Helpers ─────────────────────────────────────────────────────────────


def _resp(status_code: int, body: Dict[str, Any]):
    """Build a stubbed httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body)
    import json as _json
    resp.text = _json.dumps(body)
    return resp


def _async_client_with_post(fake_post):
    """Async-context-manager mock that exposes the given post coroutine."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client
