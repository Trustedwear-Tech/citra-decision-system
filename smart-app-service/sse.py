# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Server-Sent-Events helpers for keepalive streaming of long agent turns.

The published-app agent endpoints (``/apps/{slug}/chat``, ``/apps/{slug}/run``,
``/apps/{slug}/run/{cid}/approve``) run a blocking LLM tool-loop that can exceed
the gateway idle timeout (ALB ~60s, Cloudflare ~100s). Because they emit no
bytes until the turn finishes, the gateway sees an idle connection and returns
**504** — which is what users hit on a published app's copilot / brief.

``run_with_heartbeat`` wraps the EXISTING blocking coroutine in an SSE stream
that emits an immediate first byte + a heartbeat every ``heartbeat_s`` seconds
while the turn runs, then a terminal ``done`` (or ``error``) event carrying the
same JSON the caller already returned. Bytes flow the whole time, so no gateway
idle timer ever fires. The agent code itself (runtime.py) is unchanged.

SSE event shape mirrors ``relay_build_chat`` (the build-chat relay):
    data: {"type": "status", "text": "working"}        (immediate first byte)
    data: {"type": "heartbeat"}                          (every heartbeat_s)
    data: {"type": "done", "result": <jsonable>}         (success)
    data: {"type": "error", "status": N, "message": ...} (failure)

Errors are MAPPED to a terminal event, not raised: once the stream has started
the HTTP status is already 200, so the caller learns the real status from the
``error`` event's ``status`` field instead. Do all cheap validation (auth,
404/410, payload) BEFORE returning the StreamingResponse so those still produce
real HTTP status codes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Type

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

# Headers callers should set on the StreamingResponse alongside
# media_type="text/event-stream". X-Accel-Buffering disables proxy buffering so
# the heartbeats reach the client immediately.
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# Registry of detached agent turns still running (e.g. after a client disconnect,
# mid-commit). On graceful shutdown the lifespan awaits these via drain_inflight()
# so a rolling deploy doesn't abort an audited write. Tasks self-remove on done.
_inflight_turns: "set[asyncio.Task]" = set()


async def drain_inflight() -> None:
    """Await any in-flight agent turns (best-effort) so shutdown doesn't abort a
    commit. The lifespan wraps this in asyncio.wait_for(timeout=...)."""
    pending = [t for t in _inflight_turns if not t.done()]
    if not pending:
        return
    logger.info("draining %d in-flight agent turn(s) before shutdown", len(pending))
    await asyncio.gather(*pending, return_exceptions=True)


def sse_event(payload: dict[str, Any]) -> bytes:
    """Encode one SSE ``data:`` frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def run_with_heartbeat(
    make_coro: Callable[[], Awaitable[Any]],
    *,
    heartbeat_s: float = 12.0,
    rate_limit_exc: Optional[Type[BaseException]] = None,
) -> AsyncIterator[bytes]:
    """Run ``make_coro()`` to completion while keeping the SSE connection alive.

    ``rate_limit_exc`` (e.g. ``LLMRateLimitError``) is mapped to status 429;
    ``HTTPException`` to its own ``status_code``; anything else to 502 (logged).

    On client disconnect the SSE generator is cancelled, but the in-flight
    ``task`` is deliberately NOT cancelled: for /run and /approve it may be
    mid-commit, and aborting it would leave a partial write with no finalized
    audit row (violates RULE #1, "never commit unaudited"). The blocking
    endpoints don't abort a commit on disconnect either, so we match that — the
    turn runs to completion detached and ``_reap`` retrieves/logs its result so
    a failure is neither swallowed nor reported as "exception never retrieved".
    """
    task: asyncio.Task = asyncio.create_task(make_coro())
    # Track for graceful-shutdown draining (see drain_inflight) so a deploy that
    # lands mid-turn lets the commit + audit finish instead of being SIGKILLed.
    _inflight_turns.add(task)

    def _reap(t: "asyncio.Task") -> None:
        # Retrieve the result so a detached (post-disconnect) turn doesn't warn
        # "exception never retrieved". Idempotent with the in-band task.result()
        # below. HTTPException is expected control flow (404/410/...), not an error.
        _inflight_turns.discard(t)
        if t.cancelled():
            return
        try:
            t.result()
        except HTTPException:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("agent turn raised (detached / post-stream)")

    task.add_done_callback(_reap)

    # First byte immediately — the gateway never sees an idle connection.
    yield sse_event({"type": "status", "text": "working"})
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=heartbeat_s)
            if done:
                break
            yield sse_event({"type": "heartbeat"})
    except asyncio.CancelledError:
        # Client gone. Leave `task` running so any commit + audit finish.
        raise

    try:
        result = task.result()
    except HTTPException as exc:
        yield sse_event(
            {"type": "error", "status": exc.status_code, "message": str(exc.detail)}
        )
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — mapped to a terminal error event
        if rate_limit_exc is not None and isinstance(exc, rate_limit_exc):
            yield sse_event(
                {"type": "error", "status": 429, "message": str(exc) or "rate limited"}
            )
            return
        logger.exception("streamed agent turn failed")
        yield sse_event({"type": "error", "status": 502, "message": str(exc)})
        return

    yield sse_event({"type": "done", "result": jsonable_encoder(result)})
