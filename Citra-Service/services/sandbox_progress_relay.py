# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Bridge between :func:`services.code_executor.execute_code` (which accepts a
``progress_cb`` async callback) and an SSE event generator that wants to
``yield`` :class:`StreamEvent` instances.

Usage from inside an ``async def stream():`` generator::

    async with sandbox_progress_relay() as (progress_cb, drain):
        exec_task = asyncio.create_task(execute_code(..., progress_cb=progress_cb))
        async for evt in drain(exec_task):
            yield evt
        result = await exec_task

The relay forwards each ``progress_cb`` event as a
``StreamEvent(TOOL_STATUS, {"tool": "execute_code", **payload})`` so the UI
can show download / container / stdout progress in real time instead of
sitting blank for the whole sandbox round-trip.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Tuple

from streaming_response import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[Dict[str, Any]], Awaitable[None]]
DrainFn = Callable[[asyncio.Task], AsyncIterator[StreamEvent]]


@asynccontextmanager
async def sandbox_progress_relay() -> AsyncIterator[Tuple[ProgressCallback, DrainFn]]:
    queue: "asyncio.Queue[Dict[str, Any] | None]" = asyncio.Queue(maxsize=256)

    async def progress_cb(evt: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(evt)
        except asyncio.QueueFull:
            # Don't block sandbox progress on a slow consumer.
            logger.debug("sandbox_progress_relay: queue full, dropping event")

    async def drain(exec_task: asyncio.Task) -> AsyncIterator[StreamEvent]:
        while True:
            if exec_task.done() and queue.empty():
                return
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if evt is None:
                return
            yield StreamEvent(
                StreamEventType.TOOL_STATUS,
                {"tool": "execute_code", **evt},
            )

    try:
        yield progress_cb, drain
    finally:
        # Sentinel so any in-progress drain() loop can exit cleanly if the
        # caller didn't consume to completion.
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
