"""Unit tests for the count-probe cache (size-estimate reuse, not data).

The cache stores ONLY the integer result of a `SELECT count(*)` probe, keyed per
(source_id, data_version, normalized count SQL). A write bumps the source's
data_version so every stale estimate is invalidated at once. No row/aggregate
data is ever cached — these tests assert the estimate-cache contract only.
"""
from __future__ import annotations

import plan_cache


class FakeRedis:
    """Minimal dict-backed stand-in for the bits of redis plan_cache uses."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.store[k] = str(v)
        self.ttls[k] = ttl

    def expire(self, k, ttl):
        if k in self.store:
            self.ttls[k] = ttl
            return True
        return False

    def ttl(self, k):
        return self.ttls.get(k, -1)

    def incr(self, k):
        n = int(self.store.get(k, 0)) + 1
        self.store[k] = str(n)
        return n


def test_plan_cache_sliding_ttl(monkeypatch):
    """A plan-cache hit must refresh (slide) the key's TTL back to the full
    window, so a hot query never re-plans."""
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    monkeypatch.setenv("PLAN_CACHE_ENABLED", "1")
    assert plan_cache._PLAN_TTL == 86400  # 24h default
    src, fp, q, sql = "billing", "fp1", "how many consumers", "SELECT count(*) FROM consumers"
    plan_cache.set_plan(src, fp, q, sql)
    key = plan_cache._plan_key(src, fp, q)
    fake.ttls[key] = 100  # simulate the window having decayed near expiry
    assert plan_cache.get_plan(src, fp, q) == sql
    assert fake.ttls[key] == plan_cache._PLAN_TTL  # slid back to the full 24h on the hit


def test_count_cache_roundtrip_normalization_and_invalidation(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    monkeypatch.setenv("COUNT_CACHE_ENABLED", "1")

    sql = 'SELECT count(*) AS n FROM "tamper_events" WHERE "meter_id" = \'X\''

    # miss → set → hit
    assert plan_cache.get_count("smart_meter", sql) is None
    plan_cache.set_count("smart_meter", sql, 48)
    assert plan_cache.get_count("smart_meter", sql) == 48

    # whitespace-only variation keys the same (normalized) → still a hit
    spaced = '  SELECT   count(*)  AS n   FROM "tamper_events"   WHERE "meter_id" = \'X\' '
    assert plan_cache.get_count("smart_meter", spaced) == 48

    # a write bumps the source version → the stale estimate is invalidated
    plan_cache.bump_data_version("smart_meter")
    assert plan_cache.get_count("smart_meter", sql) is None

    # re-set under the new version works
    plan_cache.set_count("smart_meter", sql, 50)
    assert plan_cache.get_count("smart_meter", sql) == 50

    # source isolation — same SQL under a different source never collides
    assert plan_cache.get_count("billing", sql) is None


def test_count_cache_fail_open_without_redis(monkeypatch):
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: None)
    monkeypatch.setenv("COUNT_CACHE_ENABLED", "1")
    assert plan_cache.get_count("s", "SELECT count(*) AS n FROM t") is None
    # set / bump must be silent no-ops, never raise, when redis is absent
    plan_cache.set_count("s", "SELECT count(*) AS n FROM t", 5)
    plan_cache.bump_data_version("s")


def test_count_cache_disabled_by_flag(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    monkeypatch.setenv("COUNT_CACHE_ENABLED", "0")
    plan_cache.set_count("s", "SELECT count(*) AS n FROM t", 5)
    assert plan_cache.get_count("s", "SELECT count(*) AS n FROM t") is None
