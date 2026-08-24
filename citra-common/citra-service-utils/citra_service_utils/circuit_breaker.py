# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Async-aware circuit breaker.

Three states:
  CLOSED  — calls pass through. Failures count toward the threshold.
  OPEN    — calls short-circuit immediately with CircuitBreakerOpen.
            After ``recovery_timeout`` seconds the breaker moves to
            HALF_OPEN.
  HALF_OPEN — first call after timeout is allowed through. If it
              succeeds → CLOSED (counters reset). If it fails → OPEN
              (timer restarts).

Thread-safe under asyncio. Each breaker instance is local to a
process — no Redis coordination — which is correct for "fail fast on
hung dependency" use cases. Cluster-wide outage detection is a
different problem (handled by health checks + load balancer).

Typical use::

    from citra_service_utils import CircuitBreaker, CircuitBreakerOpen

    sandbox_breaker = CircuitBreaker(
        name="action-sandbox-host",
        failure_threshold=3,
        recovery_timeout=30.0,
    )

    async def spawn_sandbox(...):
        async def _call():
            async with httpx.AsyncClient(timeout=90.0) as http:
                return await http.post(url, json=payload, ...)
        try:
            return await sandbox_breaker.call(_call)
        except CircuitBreakerOpen:
            raise SandboxUnavailable("circuit open — sandbox host repeatedly failing")
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class _State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is short-circuited because the breaker is OPEN."""

    def __init__(self, name: str, opened_at: float, recovery_at: float):
        self.name = name
        self.opened_at = opened_at
        self.recovery_at = recovery_at
        super().__init__(
            f"Circuit '{name}' is OPEN (opens-since={time.time() - opened_at:.1f}s, "
            f"recovers-in={max(0.0, recovery_at - time.time()):.1f}s)"
        )


class CircuitBreaker:
    """Per-process async circuit breaker.

    Parameters
    ----------
    name : str
        Human-readable name (used in logs + the OPEN exception).
    failure_threshold : int
        Consecutive failures before tripping. Default 3.
    recovery_timeout : float
        Seconds to stay OPEN before allowing a single trial call.
        Default 30.
    expected_exception : type or tuple
        Exception types that count as failures. Anything else is
        re-raised without affecting state. Default: ``Exception``.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        expected_exception: type | tuple = Exception,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._state: _State = _State.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────────────

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` through the breaker.

        Raises ``CircuitBreakerOpen`` immediately if state is OPEN and
        the recovery timeout hasn't elapsed.
        """
        await self._maybe_close_or_half_open()
        if self._state == _State.OPEN:
            raise CircuitBreakerOpen(
                self.name, self._opened_at, self._opened_at + self.recovery_timeout,
            )
        try:
            result = await fn()
        except self.expected_exception as exc:
            await self._record_failure(exc)
            raise
        await self._record_success()
        return result

    @property
    def state(self) -> str:
        return self._state.value

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    # ── Internal state transitions ─────────────────────────────────────

    async def _maybe_close_or_half_open(self) -> None:
        """Move OPEN → HALF_OPEN if the recovery window has elapsed."""
        if self._state != _State.OPEN:
            return
        if time.time() >= self._opened_at + self.recovery_timeout:
            async with self._lock:
                if self._state == _State.OPEN and time.time() >= self._opened_at + self.recovery_timeout:
                    logger.info(f"⚡ circuit '{self.name}' OPEN → HALF_OPEN (trial call permitted)")
                    self._state = _State.HALF_OPEN

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == _State.HALF_OPEN:
                logger.info(f"✅ circuit '{self.name}' HALF_OPEN → CLOSED (recovery confirmed)")
            elif self._consecutive_failures > 0:
                logger.info(
                    f"✅ circuit '{self.name}' recovered after "
                    f"{self._consecutive_failures} failure(s)"
                )
            self._state = _State.CLOSED
            self._consecutive_failures = 0

    async def _record_failure(self, exc: BaseException) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            logger.warning(
                f"⚠️ circuit '{self.name}' failure {self._consecutive_failures}/"
                f"{self.failure_threshold}: {exc!r}"
            )
            if self._state == _State.HALF_OPEN:
                # Trial failed — re-open immediately.
                self._state = _State.OPEN
                self._opened_at = time.time()
                logger.warning(f"🔴 circuit '{self.name}' HALF_OPEN → OPEN (trial failed)")
            elif self._consecutive_failures >= self.failure_threshold:
                self._state = _State.OPEN
                self._opened_at = time.time()
                logger.warning(
                    f"🔴 circuit '{self.name}' OPEN — {self._consecutive_failures} "
                    f"consecutive failures, recovery in {self.recovery_timeout}s"
                )
