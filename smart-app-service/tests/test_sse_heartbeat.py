# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for sse.run_with_heartbeat — the keepalive wrapper that stops the
published-app agent endpoints from 504-ing on long turns.

Tests are plain sync functions that drive the async generator via asyncio.run,
so no pytest-asyncio plugin is required.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sse import run_with_heartbeat  # noqa: E402


class _FakeRateLimit(Exception):
    pass


def _drain(gen) -> list[bytes]:
    async def _run() -> list[bytes]:
        out: list[bytes] = []
        async for chunk in gen:
            out.append(chunk)
        return out

    return asyncio.run(_run())


def _events(chunks: list[bytes]) -> list[dict]:
    evs: list[dict] = []
    for chunk in chunks:
        for line in chunk.decode("utf-8").split("\n"):
            if line.startswith("data:"):
                evs.append(json.loads(line[5:].strip()))
    return evs


def test_status_then_heartbeats_then_done():
    async def work():
        await asyncio.sleep(0.15)
        return {"reply": "hi", "tool_calls": 2}

    evs = _events(_drain(run_with_heartbeat(work, heartbeat_s=0.03)))
    assert evs[0]["type"] == "status"  # immediate first byte
    assert any(e["type"] == "heartbeat" for e in evs)  # kept alive while running
    assert evs[-1] == {"type": "done", "result": {"reply": "hi", "tool_calls": 2}}


def test_httpexception_maps_to_its_status():
    async def work():
        raise HTTPException(status_code=410, detail="app is archived")

    evs = _events(_drain(run_with_heartbeat(work, heartbeat_s=1)))
    last = evs[-1]
    assert last["type"] == "error"
    assert last["status"] == 410
    assert "archived" in last["message"]


def test_rate_limit_maps_to_429():
    async def work():
        raise _FakeRateLimit("slow down")

    evs = _events(
        _drain(run_with_heartbeat(work, heartbeat_s=1, rate_limit_exc=_FakeRateLimit))
    )
    last = evs[-1]
    assert last["type"] == "error"
    assert last["status"] == 429


def test_generic_exception_maps_to_502():
    async def work():
        raise ValueError("boom")

    evs = _events(_drain(run_with_heartbeat(work, heartbeat_s=1)))
    last = evs[-1]
    assert last["type"] == "error"
    assert last["status"] == 502
    assert "boom" in last["message"]


def test_chart_blocks_survive_the_stream():
    """A dashboard-narrator reply with chart `blocks` must arrive intact in the
    terminal `done` event — the SSE keepalive changes the transport, NOT the
    response shape, so HeroBriefCopilot still renders the chart exactly as
    before. Mirrors what readAgentStream does on the client (ignore status/
    heartbeat frames; take done.result)."""
    chart_result = {
        "reply": "Recovery is concentrated in two divisions.",
        "tool_calls": 1,
        "blocks": [
            {
                "type": "chart",
                "spec": {
                    "chart_type": "bar",
                    "x": "division",
                    "y": "case_id",
                    "aggregation": "count",
                },
                "data": [
                    {"division": "North", "case_id": 12},
                    {"division": "South", "case_id": 30},
                ],
            }
        ],
    }

    async def work():
        return chart_result

    evs = _events(_drain(run_with_heartbeat(work, heartbeat_s=1)))
    done = evs[-1]
    assert done["type"] == "done"
    # Whole ChatResponse round-trips — reply + nested chart spec + data.
    assert done["result"] == chart_result
    block = done["result"]["blocks"][0]
    assert block["type"] == "chart"
    assert block["spec"]["chart_type"] == "bar"
    assert block["data"][1] == {"division": "South", "case_id": 30}


def test_no_done_after_error():
    async def work():
        raise ValueError("boom")

    evs = _events(_drain(run_with_heartbeat(work, heartbeat_s=1)))
    assert not any(e["type"] == "done" for e in evs)  # terminal is error, not done
