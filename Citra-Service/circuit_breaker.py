# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Per-endpoint Circuit Breaker for MCP calls.

State machine (per tool_id):
    CLOSED     → normal; every call goes through
    OPEN       → fast-fail with MCPUnavailableError, no HTTP attempt
    HALF_OPEN  → allow a single trial request; success → CLOSED, failure → OPEN

Policy:
    - Open after CB_FAILURE_THRESHOLD consecutive failures (default 3)
    - Stay open for CB_OPEN_SECONDS (default 30s) before half-open trial
    - On trial failure, grow open window: 30 → 60 → 120 → 240, cap at 300s
    - Thread/async-safe via per-key asyncio.Lock
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
_OPEN_SECONDS_BASE = float(os.getenv("CB_OPEN_SECONDS", "30"))
_OPEN_SECONDS_CAP = float(os.getenv("CB_OPEN_SECONDS_CAP", "300"))

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class MCPUnavailableError(Exception):
    """Raised when the circuit is open (fast-fail, no HTTP attempt).

    This is a FAILURE, not an empty result — callers must surface it as an
    error to the LLM (see ``enterprise_mcp_client.call_tool``), never swallow
    it into ``[]``, or the model will mistake "source unavailable" for "source
    has no such data" and fabricate.
    """


class _EndpointState:
    __slots__ = ("state", "consecutive_failures", "open_until", "current_open_window", "lock")

    def __init__(self) -> None:
        self.state: str = CLOSED
        self.consecutive_failures: int = 0
        self.open_until: float = 0.0
        self.current_open_window: float = _OPEN_SECONDS_BASE
        self.lock = asyncio.Lock()


class CircuitBreaker:
    def __init__(self) -> None:
        self._endpoints: Dict[str, _EndpointState] = {}
        self._registry_lock = asyncio.Lock()

    async def _get(self, key: str) -> _EndpointState:
        st = self._endpoints.get(key)
        if st is not None:
            return st
        async with self._registry_lock:
            st = self._endpoints.get(key)
            if st is None:
                st = _EndpointState()
                self._endpoints[key] = st
            return st

    def snapshot(self) -> Dict[str, str]:
        return {k: v.state for k, v in self._endpoints.items()}

    async def call(self, key: str, func: Callable[[], Awaitable[Any]]) -> Any:
        st = await self._get(key)

        async with st.lock:
            now = time.monotonic()
            if st.state == OPEN:
                if now < st.open_until:
                    raise MCPUnavailableError(f"circuit open for {key}")
                st.state = HALF_OPEN
                logger.info(f"🔌 [CB] {key}: OPEN → HALF_OPEN (trial)")

        try:
            result = await func()
        except Exception as exc:
            async with st.lock:
                st.consecutive_failures += 1
                if st.state == HALF_OPEN or st.consecutive_failures >= _FAILURE_THRESHOLD:
                    # Grow the open window on repeated trips; reset on first trip.
                    if st.state == HALF_OPEN:
                        st.current_open_window = min(st.current_open_window * 2, _OPEN_SECONDS_CAP)
                    else:
                        st.current_open_window = _OPEN_SECONDS_BASE
                    st.state = OPEN
                    st.open_until = time.monotonic() + st.current_open_window
                    logger.warning(
                        f"🔌 [CB] {key}: → OPEN for {st.current_open_window:.0f}s "
                        f"({st.consecutive_failures} consecutive failures): {exc}"
                    )
            raise

        async with st.lock:
            if st.state in (OPEN, HALF_OPEN):
                logger.info(f"🔌 [CB] {key}: → CLOSED (recovered)")
            st.state = CLOSED
            st.consecutive_failures = 0
            st.current_open_window = _OPEN_SECONDS_BASE
        return result


_breaker: Optional[CircuitBreaker] = None


def get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker
