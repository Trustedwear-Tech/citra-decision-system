# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Per-session call/token budget for the builder LLM proxy.

Counters keyed by bearer ``subject`` (e.g. ``build:bs_abc123``), held in the
shared **coordination** Redis tier (durable, noeviction) so the ceiling holds
ACROSS replicas — previously these were an in-process dict, so with N replicas
each enforced only its own slice and the real budget was N× the configured cap.

The proxy increments calls before forwarding and records tokens after the
upstream response, so a runaway prompt-injection loop is bounded by
``SMART_APP_LLM_PROXY_MAX_CALLS_PER_SESSION`` /
``SMART_APP_LLM_PROXY_MAX_TOKENS_PER_SESSION``.

Resilience: a budget guard must not be a hard dependency for serving. If Redis is
unavailable we **fail OPEN** (allow the call) but log at ERROR — the cost
exposure is bounded to the Redis-outage window, which is far better than 500-ing
every build turn. (Same degrade-open posture as ``llm_rate_limit``.)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Session budget key TTL — long enough to span a build session, refreshed on
# every reserve/record so an active session never expires mid-flight.
_TTL = max(60, int(os.getenv("SMART_APP_LLM_PROXY_BUDGET_TTL", "3600")))

# Atomic check-and-increment of the call counter. Returns {code, value}:
#   code >= 1 → new call count (allowed); -1 → calls cap hit; -2 → tokens cap hit.
_RESERVE_LUA = (
    "local calls = tonumber(redis.call('HGET', KEYS[1], 'calls') or '0') "
    "local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or '0') "
    "if calls >= tonumber(ARGV[1]) then return {-1, calls} end "
    "if ARGV[2] ~= '' and tokens >= tonumber(ARGV[2]) then return {-2, tokens} end "
    "local n = redis.call('HINCRBY', KEYS[1], 'calls', 1) "
    "redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3])) "
    "return {n, tokens}"
)
_RECORD_LUA = (
    "local t = redis.call('HINCRBY', KEYS[1], 'tokens', tonumber(ARGV[1])) "
    "redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) "
    "return t"
)


class BudgetExceeded(Exception):
    """Raised when a session exceeds its call or token budget."""

    def __init__(self, kind: str, used: int, limit: int) -> None:
        super().__init__(f"{kind} budget exceeded: {used}/{limit}")
        self.kind = kind
        self.used = used
        self.limit = limit


def _key(subject: str) -> str:
    return f"smartapp:llm_budget:{subject}"


def _coord():
    from citra_cache import get_coordination_manager

    return get_coordination_manager()


def reserve_call(subject: str, *, max_calls: int, max_tokens: int | None = None) -> int:
    """Pre-increment the call counter; raise BEFORE forwarding if the session
    would exceed the call cap, OR has already crossed the token cap.

    Returns the post-increment call count. Fails OPEN (returns 0) on a Redis
    outage, logging loudly — a budget guard must not 500 the build path.
    """
    try:
        res = _coord().eval(
            _RESERVE_LUA, 1, _key(subject),
            str(int(max_calls)), "" if max_tokens is None else str(int(max_tokens)), str(_TTL),
        )
    except Exception as e:  # noqa: BLE001 — coordination store down → degrade open + log loud
        logger.error(
            "LLM proxy budget DEGRADED (coordination Redis unavailable: %s) — "
            "allowing call for %s WITHOUT a budget ceiling; fix Redis to restore it",
            e, subject,
        )
        return 0
    code, value = int(res[0]), int(res[1])
    if code == -1:
        raise BudgetExceeded("calls", value, max_calls)
    if code == -2:
        raise BudgetExceeded("tokens", value, max_tokens or 0)
    return code


def record_tokens(subject: str, tokens: int, *, max_tokens: int) -> int:
    """Add tokens to the session counter; raise if it crosses the cap.

    The over-the-cap call still returns to the caller (we can't un-bill the
    upstream); we only refuse the *next* call. Fails OPEN on a Redis outage.
    """
    try:
        used = int(_coord().eval(_RECORD_LUA, 1, _key(subject), str(max(0, int(tokens or 0))), str(_TTL)))
    except Exception as e:  # noqa: BLE001 — degrade open + log loud
        logger.error(
            "LLM proxy budget DEGRADED (coordination Redis unavailable: %s) — "
            "token accounting skipped for %s", e, subject,
        )
        return 0
    if used > max_tokens:
        raise BudgetExceeded("tokens", used, max_tokens)
    return used


def snapshot(subject: str) -> tuple[int, int]:
    """Return (calls, tokens) for a session. Test/debug helper. (0, 0) on error.

    CacheManager exposes ``eval`` (not ``hget``), so read both fields in one Lua.
    """
    try:
        res = _coord().eval(
            "return {redis.call('HGET', KEYS[1], 'calls'), redis.call('HGET', KEYS[1], 'tokens')}",
            1, _key(subject),
        )
        return (int(res[0] or 0), int(res[1] or 0))
    except Exception:  # noqa: BLE001
        return (0, 0)


def reset(subject: str) -> None:
    """Drop the counter for a session (e.g. on session close)."""
    try:
        _coord().delete(_key(subject))
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM proxy budget reset failed for %s: %s", subject, e)
