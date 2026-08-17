# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Durability tests for the trigger poll path: per-row retry, dead-letter after
max attempts, success-only consume, and unchanged Run-now (legacy) semantics.

Isolates ``_process_new_poll_rows`` by monkeypatching ``_fire_and_persist`` so we
control success/failure per row without the full agent/MCP stack.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import trigger_runner as tr
from models import Trigger


def _poll_trigger() -> Trigger:
    return Trigger(
        id="t1", type="poll", action="act", dedup_key="id",
        input_template={"x": "$row.id"}, tool="src.tool", enabled=True,
    )


def test_failed_row_is_retried_not_consumed(monkeypatch):
    async def _fail(**kw):
        return {"fired": False, "reason": "boom"}
    monkeypatch.setattr(tr, "_fire_and_persist", _fail)
    attempts: dict = {}
    dlq: list = []

    async def _rec(**kw):
        if kw.get("fired_via") == "dead_letter":
            dlq.append(kw)

    # Attempt 1 + 2: not consumed (left for retry), no dead-letter.
    _, consumed = asyncio.run(tr._process_new_poll_rows(
        settings=None, app_spec=SimpleNamespace(slug="app1"), agent_spec=SimpleNamespace(),
        trigger=_poll_trigger(), rows=[{"id": "R1"}], seen=set(), max_rows=5, concurrency=1,
        pending_runs_col=None, app_doc={}, record_run=_rec, attempts=attempts, max_attempts=3))
    assert consumed == [] and attempts == {"R1": 1} and dlq == []

    asyncio.run(tr._process_new_poll_rows(
        settings=None, app_spec=SimpleNamespace(slug="app1"), agent_spec=SimpleNamespace(),
        trigger=_poll_trigger(), rows=[{"id": "R1"}], seen=set(), max_rows=5, concurrency=1,
        pending_runs_col=None, app_doc={}, record_run=_rec, attempts=attempts, max_attempts=3))
    assert attempts == {"R1": 2} and dlq == []

    # Attempt 3 == max: dead-lettered + consumed + counter cleared.
    _, consumed = asyncio.run(tr._process_new_poll_rows(
        settings=None, app_spec=SimpleNamespace(slug="app1"), agent_spec=SimpleNamespace(),
        trigger=_poll_trigger(), rows=[{"id": "R1"}], seen=set(), max_rows=5, concurrency=1,
        pending_runs_col=None, app_doc={}, record_run=_rec, attempts=attempts, max_attempts=3))
    assert consumed == ["R1"], "row consumed after giving up"
    assert "R1" not in attempts, "attempt counter cleared on dead-letter"
    assert len(dlq) == 1 and dlq[0]["fired_via"] == "dead_letter"
    assert "DEAD_LETTER" in dlq[0]["error"]


def test_success_consumes_and_clears_attempts(monkeypatch):
    async def _ok(**kw):
        return {"fired": True, "status": "pending_approval"}
    monkeypatch.setattr(tr, "_fire_and_persist", _ok)
    attempts = {"R1": 2}  # had prior transient failures
    _, consumed = asyncio.run(tr._process_new_poll_rows(
        settings=None, app_spec=SimpleNamespace(slug="app1"), agent_spec=SimpleNamespace(),
        trigger=_poll_trigger(), rows=[{"id": "R1"}], seen=set(), max_rows=5, concurrency=1,
        pending_runs_col=None, app_doc={}, attempts=attempts, max_attempts=3))
    assert consumed == ["R1"] and "R1" not in attempts


def test_legacy_runnow_consumes_even_on_failure(monkeypatch):
    # attempts=None → Run-now single-shot semantics: consume the attempted row
    # regardless of outcome (unchanged behavior; no auto-retry on the manual path).
    async def _fail(**kw):
        return {"fired": False, "reason": "x"}
    monkeypatch.setattr(tr, "_fire_and_persist", _fail)
    _, consumed = asyncio.run(tr._process_new_poll_rows(
        settings=None, app_spec=SimpleNamespace(slug="app1"), agent_spec=SimpleNamespace(),
        trigger=_poll_trigger(), rows=[{"id": "R1"}], seen=set(), max_rows=5, concurrency=1,
        pending_runs_col=None, app_doc={}, attempts=None))
    assert consumed == ["R1"]
