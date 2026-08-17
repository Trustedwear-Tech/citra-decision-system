# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""In-process cache backend (customer-side MCP, no Redis).

Proves the InProcessCache implements the Redis subset plan_cache needs (with TTL
+ LRU bounds), the backend selector picks in-process when there's no REDIS_HOST,
and plan_cache's plan/few-shot/count/embedding caches all round-trip on it — so
the customer-side MCP gets NL→SQL reuse without a Redis dependency.
"""
from __future__ import annotations

import asyncio
import logging

import cache_backend
import plan_cache
from cache_backend import InProcessCache, make_cache_client

_LOG = logging.getLogger("test")
# NB: conftest's autouse _isolate_plan_cache_singleton resets plan_cache's cache
# client around every test, so the end-to-end tests below start empty and never
# leak into later tests.


# ── InProcessCache: Redis-subset semantics ──────────────────────────────────
def test_setex_get_and_miss():
    c = InProcessCache()
    assert c.get("k") is None
    c.setex("k", 100, "v")
    assert c.get("k") == "v"


def test_set_nx_is_acquire_once():
    c = InProcessCache()
    assert c.set("lock", "1", nx=True, ex=100) is True
    assert c.set("lock", "1", nx=True, ex=100) is None      # already held


def test_incr_counts_and_preserves_nothing_extra():
    c = InProcessCache()
    assert c.incr("dv") == 1
    assert c.incr("dv") == 2
    assert c.get("dv") == "2"                                # decode_responses → str


def test_list_ops_order_and_inclusive_trim():
    c = InProcessCache()
    c.lpush("ring", "a")
    c.lpush("ring", "b")
    c.lpush("ring", "c")                                     # newest first: [c, b, a]
    assert c.lrange("ring", 0, -1) == ["c", "b", "a"]
    c.ltrim("ring", 0, 1)                                    # keep 2 newest (inclusive end)
    assert c.lrange("ring", 0, -1) == ["c", "b"]


def test_ttl_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache_backend.time, "monotonic", lambda: clock["t"])
    c = InProcessCache()
    c.setex("k", 10, "v")
    assert c.get("k") == "v"
    clock["t"] += 11                                         # past the TTL
    assert c.get("k") is None


def test_lru_eviction_bounds_memory():
    c = InProcessCache(max_entries=2)
    c.setex("a", 100, "1")
    c.setex("b", 100, "2")
    assert c.get("a") == "1"                                 # touch a → a is MRU, b is LRU
    c.setex("c", 100, "3")                                   # overflow → evict LRU (b)
    assert c.get("b") is None
    assert c.get("a") == "1" and c.get("c") == "3"


def test_expire_slides_window(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(cache_backend.time, "monotonic", lambda: clock["t"])
    c = InProcessCache()
    c.setex("k", 10, "v")
    clock["t"] = 9
    assert c.expire("k", 10) is True                         # slide to now+10
    clock["t"] = 15
    assert c.get("k") == "v"                                 # would have died at 10 without the slide


# ── backend selection ────────────────────────────────────────────────────────
def test_selector_memory_when_no_redis_host(monkeypatch):
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    client, name = make_cache_client(_LOG)
    assert name == "memory" and isinstance(client, InProcessCache)


def test_selector_explicit_memory_overrides_redis_host(monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    monkeypatch.setenv("REDIS_HOST", "some-redis")
    client, name = make_cache_client(_LOG)
    assert name == "memory" and isinstance(client, InProcessCache)


# ── plan_cache end-to-end on the in-process backend ──────────────────────────
def _use_memory_backend(monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    monkeypatch.setenv("PLAN_CACHE_ENABLED", "1")
    monkeypatch.setenv("COUNT_CACHE_ENABLED", "1")
    monkeypatch.delenv("RERANKER_URL", raising=False)
    # reset the memoized client so it re-selects the in-process backend
    plan_cache._redis_client = None
    plan_cache._redis_init_done = False


def test_plan_cache_roundtrip_in_process(monkeypatch):
    _use_memory_backend(monkeypatch)
    src, fp = "billing", "fp1"
    q, sql = "how many unpaid invoices", "SELECT count(*) FROM invoices WHERE paid=0"
    assert plan_cache.get_plan(src, fp, q) is None           # cold miss
    plan_cache.set_plan(src, fp, q, sql)
    assert plan_cache.get_plan(src, fp, q) == sql            # hit — no planner LLM needed


def test_fewshot_ring_in_process(monkeypatch):
    _use_memory_backend(monkeypatch)
    src, fp = "billing", "fp1"
    plan_cache.push_example(src, fp, "q1", "SQL1")
    plan_cache.push_example(src, fp, "q2", "SQL2")
    ex = asyncio.run(plan_cache.get_examples(src, fp, "anything", k=5))
    # newest-first recency (no reranker configured), both pairs present
    assert ("q2", "SQL2") in ex and ("q1", "SQL1") in ex


def test_count_cache_invalidated_by_write_in_process(monkeypatch):
    _use_memory_backend(monkeypatch)
    src, csql = "billing", "SELECT count(*) FROM invoices"
    plan_cache.set_count(src, csql, 42)
    assert plan_cache.get_count(src, csql) == 42
    plan_cache.bump_data_version(src)                        # a write happened
    assert plan_cache.get_count(src, csql) is None           # stale estimate invalidated
