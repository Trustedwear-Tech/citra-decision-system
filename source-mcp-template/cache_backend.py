# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Cache backend for the dept-MCP plan / few-shot / count / embedding caches.

Two backends behind ONE Redis-subset interface (``get``, ``setex``,
``set(nx, ex)``, ``expire``, ``incr``, ``lpush``, ``ltrim``, ``lrange``) so
``plan_cache`` is backend-agnostic — every cache in that module works unchanged
on either:

  • **Redis** — platform-side / multi-replica: the cache is SHARED across
    instances (one introspection warms all replicas, one leader wins the
    registration write).
  • **in-process** — the CUSTOMER-side single-instance MCP: NO Redis dependency
    in the customer estate, so the MCP stays infra-stateless (nothing to run or
    operate besides the process). Per-process, thread-safe, and **bounded**
    (TTL + LRU max-entries) so a long-running MCP never leaks memory.

Why this exists: customer-side the MCP has no Redis, so ``plan_cache`` used to
fail-open to *no cache at all* — every NL→SQL query then paid full planner
latency with no reuse. The in-process backend restores the plan/few-shot/count
reuse WITHOUT requiring Redis in the customer estate.

Backend selection (``make_cache_client``):
  1. ``CACHE_BACKEND`` env, if set — ``redis`` | ``memory`` — wins.
  2. else AUTO: ``redis`` when ``REDIS_HOST`` is EXPLICITLY set (platform),
     ``memory`` otherwise. The customer-side MCP ships no ``REDIS_HOST`` ⇒
     in-process automatically, no flag to remember.

In-process is per-process: with >1 uvicorn worker each worker keeps its own
cache (they warm independently — correct, just less sharing), and the leader
lock degrades to the pre-Redis "every instance registers" behaviour, exactly as
when Redis is absent today.
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, List, Optional, Tuple


def _max_entries() -> int:
    try:
        return max(64, int(os.getenv("CACHE_MAX_ENTRIES", "5000")))
    except (TypeError, ValueError):
        return 5000


class InProcessCache:
    """Bounded, thread-safe, TTL-aware in-process cache exposing the small Redis
    command subset ``plan_cache`` uses (``decode_responses=True`` semantics:
    string values in, string values out; list values for the few-shot ring).

    LRU-bounded to ``max_entries`` total keys — on overflow the least-recently
    used key is dropped (Redis relied on TTL eviction; a process cache also needs
    a hard ceiling). TTLs are lazily enforced on access."""

    def __init__(self, max_entries: int = 5000) -> None:
        # key -> (value, expire_at_monotonic | None). value is str or List[str].
        self._store: "OrderedDict[str, Tuple[Any, Optional[float]]]" = OrderedDict()
        self._max = max(1, int(max_entries))
        self._lock = threading.Lock()

    # ── internals (caller holds the lock) ──────────────────────────────────────
    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _live(self, key: str) -> Any:
        """Return the live value for ``key`` (marking it MRU), pruning + returning
        None if it has expired or is absent."""
        item = self._store.get(key)
        if item is None:
            return None
        value, exp = item
        if exp is not None and exp <= self._now():
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def _evict(self) -> None:
        while len(self._store) > self._max:
            self._store.popitem(last=False)   # drop the LRU key

    def _put(self, key: str, value: Any, exp: Optional[float]) -> None:
        self._store[key] = (value, exp)
        self._store.move_to_end(key)
        self._evict()

    def _existing_exp(self, key: str) -> Optional[float]:
        item = self._store.get(key)
        return item[1] if item else None

    # ── Redis-subset API ───────────────────────────────────────────────────────
    def get(self, key: str) -> Optional[str]:
        with self._lock:
            v = self._live(key)
            return v if isinstance(v, str) else None

    def setex(self, key: str, ttl: int, value: Any) -> bool:
        with self._lock:
            self._put(key, str(value), self._now() + max(1, int(ttl)))
        return True

    def set(self, key: str, value: Any, nx: bool = False, ex: Optional[int] = None) -> Optional[bool]:
        with self._lock:
            if nx and self._live(key) is not None:
                return None                     # NX: key exists ⇒ acquire fails
            exp = self._now() + int(ex) if ex else None
            self._put(key, str(value), exp)
        return True

    def expire(self, key: str, ttl: int) -> bool:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return False
            self._store[key] = (item[0], self._now() + max(1, int(ttl)))
            self._store.move_to_end(key)
        return True

    def incr(self, key: str) -> int:
        with self._lock:
            v = self._live(key)
            n = (int(v) if v is not None else 0) + 1
            self._put(key, str(n), self._existing_exp(key))   # INCR preserves TTL
            return n

    def lpush(self, key: str, value: str) -> int:
        with self._lock:
            v = self._live(key)
            lst: List[str] = list(v) if isinstance(v, list) else []
            lst.insert(0, value)
            self._put(key, lst, self._existing_exp(key))
            return len(lst)

    def ltrim(self, key: str, start: int, end: int) -> bool:
        with self._lock:
            v = self._live(key)
            if not isinstance(v, list):
                return True
            hi = end if end >= 0 else len(v) + end
            self._put(key, v[start:hi + 1], self._existing_exp(key))   # Redis end is INCLUSIVE
        return True

    def lrange(self, key: str, start: int, end: int) -> List[str]:
        with self._lock:
            v = self._live(key)
            if not isinstance(v, list):
                return []
            hi = end if end >= 0 else len(v) + end
            return list(v[start:hi + 1])


def make_cache_client(logger: Any) -> Tuple[Optional[Any], str]:
    """Return ``(client, backend_name)`` for the plan/few-shot/etc. caches.

    ``backend_name`` is ``"redis"`` or ``"memory"``. Never raises — a Redis init
    failure when Redis was requested degrades to the in-process backend (the MCP
    still caches, just per-process) rather than to no cache."""
    backend = (os.getenv("CACHE_BACKEND", "") or "").strip().lower()
    if not backend:
        # AUTO: Redis only when a host is EXPLICITLY configured (platform side).
        backend = "redis" if os.environ.get("REDIS_HOST") else "memory"

    if backend == "redis":
        try:
            import redis  # type: ignore

            client = redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=int(os.getenv("REDIS_DB", "0")),
                password=os.getenv("REDIS_PASSWORD") or None,
                decode_responses=True,
                socket_timeout=1,
            )
            logger.info("plan_cache: Redis cache backend (%s:%s)",
                        os.getenv("REDIS_HOST", "localhost"), os.getenv("REDIS_PORT", "6379"))
            return client, "redis"
        except Exception as exc:  # noqa: BLE001 — degrade to in-process, never to no-cache
            logger.warning(
                "plan_cache: Redis backend requested but init failed (%s) — "
                "using in-process cache instead", exc,
            )
            return InProcessCache(_max_entries()), "memory"

    logger.info("plan_cache: in-process cache backend (no Redis; %d-entry LRU) — "
                "customer-side single-instance mode", _max_entries())
    return InProcessCache(_max_entries()), "memory"
