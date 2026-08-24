# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Coordination-tier Redis resolution (PRODUCTION_SCALING_PLAN §3/G3).

Coordination state (locks, leases, rate-limit counters, idempotency keys) must
NOT ride the LRU/no-persistence cache tier — an eviction or restart there breaks
the invariant the key protects. ``CacheManager(tier="coordination")`` resolves to
a durable target: COORDINATION_REDIS_* → QUEUE_REDIS_* (the durable queue tier)
→ REDIS_* (cache tier, last resort + warned).

These tests exercise ``_resolve_params`` only — they build the instance via
``__new__`` so no live Redis connection is made.

Run under any service venv that has ``citra_cache`` installed, e.g.::

    smart-app-service/venv/Scripts/python -m pytest citra-cache/tests -q
"""
from __future__ import annotations

import pytest

from citra_cache.manager import CacheManager

_VARS = [
    "COORDINATION_REDIS_HOST", "QUEUE_REDIS_HOST", "REDIS_HOST",
    "COORDINATION_REDIS_DB", "QUEUE_REDIS_DB", "REDIS_DB",
    "COORDINATION_REDIS_PORT", "QUEUE_REDIS_PORT", "REDIS_PORT",
    "COORDINATION_REDIS_PASSWORD", "QUEUE_REDIS_PASSWORD", "REDIS_PASSWORD",
]


def _resolve(tier, env, monkeypatch):
    for k in _VARS:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    m = CacheManager.__new__(CacheManager)   # bypass __init__ → no connect/ping
    m._tier = tier
    return m._resolve_params()


def test_coordination_prefers_durable_queue_tier(monkeypatch):
    p = _resolve("coordination",
                 {"REDIS_HOST": "cache", "QUEUE_REDIS_HOST": "queue", "QUEUE_REDIS_DB": "1"},
                 monkeypatch)
    # Falls back to the durable queue Redis (not the cache tier) when no
    # dedicated coordination host is set.
    assert p["host"] == "queue"
    assert p["db"] == 1


def test_dedicated_coordination_host_wins(monkeypatch):
    p = _resolve("coordination",
                 {"REDIS_HOST": "cache", "QUEUE_REDIS_HOST": "queue",
                  "COORDINATION_REDIS_HOST": "coord"},
                 monkeypatch)
    assert p["host"] == "coord"
    assert p["db"] == 3   # default coordination DB (isolated from cache db 0)


def test_cache_tier_fallback_is_db_isolated(monkeypatch):
    # Only REDIS_* set ⇒ coordination lands on the cache host (logged WARNING),
    # but on a DISTINCT db so it doesn't collide with cache (db 0) keys.
    p = _resolve("coordination", {"REDIS_HOST": "cache"}, monkeypatch)
    assert p["host"] == "cache"
    assert p["db"] == 3


def test_cache_tier_unchanged(monkeypatch):
    p = _resolve("cache", {"REDIS_HOST": "cache", "REDIS_DB": "0"}, monkeypatch)
    assert p["host"] == "cache"
    assert p["db"] == 0


def test_coordination_requires_a_host(monkeypatch):
    with pytest.raises(RuntimeError):
        _resolve("coordination", {}, monkeypatch)
