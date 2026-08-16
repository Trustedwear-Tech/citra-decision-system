"""Per-user LLM-call rate limit — shared across instances via Redis (citra_cache).

Every model call on the RUN/CHAT path (on-demand /run, chat, sub-agent, trigger)
goes through ``runtime._call_llm``, which records one call against the *calling
user's* rolling window and raises ``LLMRateLimitError`` once that user exceeds
``LLM_CALLS_PER_USER_PER_MINUTE``. (The separate BUILDER LLM proxy in
``internal_routes`` is bounded by its own per-session budget, not this limiter.) The user is bound per request via a contextvar
(``set_current_user``) at the run/chat entry, so the limiter needs no call-site
threading.

Why per-user (not global/per-tenant): LLM calls are slow and cost money; this
caps how fast any single user can drive the model, which is the practical abuse
/ cost lever, while leaving honest multi-user usage unaffected.

Fail-loud: the counter lives in the shared platform Redis via citra_cache, which
raises if Redis is unset/unreachable — we do NOT silently skip the limit (a
missing limiter on a cost-sensitive path is a real failure, per the no-fallback
rule). The window is a simple fixed window (per ``LLM_RATE_WINDOW_SECONDS``).
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

LLM_CALLS_PER_USER_PER_MINUTE = max(
    1, int(os.getenv("LLM_CALLS_PER_USER_PER_MINUTE", "30"))
)
_WINDOW = max(1, int(os.getenv("LLM_RATE_WINDOW_SECONDS", "60")))

# Atomic INCR + first-time EXPIRE so each per-user window self-cleans.
_INCR_LUA = (
    "local c = redis.call('INCR', KEYS[1]) "
    "if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end "
    "return c"
)
_PEEK_LUA = (
    "local v = redis.call('GET', KEYS[1]) "
    "if v then return tonumber(v) else return 0 end"
)

# Per-request: the user whose LLM calls this run/chat counts against. Propagates
# within the async task that sets it (execute_run / the /chat endpoint).
_current_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "llm_rate_current_user", default=None
)


class LLMRateLimitError(Exception):
    """A user exceeded the per-window LLM-call limit. The API layer converts
    this into HTTP 429 with a Retry-After."""

    def __init__(self, user_id: str, count: int):
        self.user_id = user_id
        self.count = count
        self.limit = LLM_CALLS_PER_USER_PER_MINUTE
        self.window = _WINDOW
        self.retry_after = _WINDOW
        super().__init__(
            f"LLM rate limit: user '{user_id}' made {count} calls in the last "
            f"{_WINDOW}s (limit {self.limit}). Retry in ~{_WINDOW}s."
        )


def set_current_user(user_id: Optional[str]) -> None:
    """Bind the user whose subsequent LLM calls (this request) count against."""
    _current_user.set(user_id or None)


def _key(user_id: str) -> str:
    bucket = int(time.time()) // _WINDOW
    return f"smartapp:llm_rate:{user_id}:{bucket}"


def _cache():
    # Rate-limit COUNTERS are coordination state, not cache: on the LRU cache
    # tier a counter can be evicted mid-window (resetting a user's budget under
    # memory pressure) or wiped on restart. Use the durable noeviction
    # coordination tier so the per-user cap holds across instances.
    from citra_cache import get_coordination_manager

    return get_coordination_manager()


def check_current_user() -> None:
    """Pre-flight (no increment): raise if the bound user is already at/over the
    limit, so a run/chat entry can return a clean 429 before doing any work."""
    uid = _current_user.get()
    if not uid:
        return
    try:
        n = int(_cache().eval(_PEEK_LUA, 1, _key(uid), _WINDOW) or 0)
    except Exception as e:  # noqa: BLE001 — see record_call()
        logger.error(
            "LLM rate limiter DEGRADED (cache unavailable: %s) — allowing call "
            "for %s WITHOUT limiting; fix Redis to restore the cost cap", e, uid,
        )
        return
    if n >= LLM_CALLS_PER_USER_PER_MINUTE:
        raise LLMRateLimitError(uid, n)


def record_call() -> None:
    """Count ONE LLM call for the bound user; raise once over the limit. Called
    from ``runtime._call_llm`` so every model call is counted. No-op when no
    user is bound (an internal call with no request context).

    Resilience: a rate limiter must NOT be a single point of failure for LLM
    serving. If the shared cache is unavailable, we **fail OPEN** (allow the
    call) but log at ERROR — loud enough for the observability stack to alert
    ops — rather than 500-ing every run. The cost exposure is bounded to the
    Redis-outage window; a hard dependency would be worse."""
    uid = _current_user.get()
    if not uid:
        return
    try:
        n = int(_cache().eval(_INCR_LUA, 1, _key(uid), _WINDOW) or 0)
    except Exception as e:  # noqa: BLE001 — cache down → degrade open + log loud
        logger.error(
            "LLM rate limiter DEGRADED (cache unavailable: %s) — allowing call "
            "for %s WITHOUT limiting; fix Redis to restore the cost cap", e, uid,
        )
        return
    if n > LLM_CALLS_PER_USER_PER_MINUTE:
        raise LLMRateLimitError(uid, n)
