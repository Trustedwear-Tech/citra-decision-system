"""Unit tests for the PRODUCTION_SCALING_PLAN §1/§2 hardening.

Covers the source-mcp-template pieces:
  • §1 — query-embedding cache, dataset-selection cache, shared introspection
         cache, and the cold-key single-flight stampede guard.
  • §2 — registration leader lock (P3), deregister-on-shutdown gating (P1),
         and a leader-lock loser still tracked for heartbeat liveness.

No live Redis is needed: a dict-backed FakeRedis stands in (mirrors
test_count_cache.FakeRedis) and the fail-open paths are exercised with redis=None.
"""
from __future__ import annotations

import asyncio
import types

import plan_cache


class FakeRedis:
    """Dict-backed stand-in supporting the ops the new helpers use, including
    SET ... NX EX (the leader lock)."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.store[k] = str(v)
        self.ttls[k] = ttl

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = str(v)
        if ex is not None:
            self.ttls[k] = ex
        return True

    def expire(self, k, ttl):
        if k in self.store:
            self.ttls[k] = ttl
            return True
        return False


# ── §1 query-embedding cache ───────────────────────────────────────────────────


def test_query_embedding_cache_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    monkeypatch.setenv("EMBED_CACHE_ENABLED", "1")

    assert plan_cache.get_query_embedding("m1", 768, "how many meters") is None
    plan_cache.set_query_embedding("m1", 768, "how many meters", [0.1, 0.2, 0.3])
    assert plan_cache.get_query_embedding("m1", 768, "how many meters") == [0.1, 0.2, 0.3]

    # normalization: casing / whitespace / trailing '?' key the same vector
    assert plan_cache.get_query_embedding("m1", 768, "  How Many   Meters? ") == [0.1, 0.2, 0.3]

    # model + dim are part of the key — a different model never collides
    assert plan_cache.get_query_embedding("m2", 768, "how many meters") is None
    assert plan_cache.get_query_embedding("m1", 1024, "how many meters") is None


def test_query_embedding_cache_fail_open(monkeypatch):
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: None)
    monkeypatch.setenv("EMBED_CACHE_ENABLED", "1")
    assert plan_cache.get_query_embedding("m", 768, "q") is None
    plan_cache.set_query_embedding("m", 768, "q", [1.0])  # silent no-op


# ── §1 dataset-selection cache ──────────────────────────────────────────────────


def test_dataset_selection_cache_scope_and_fp(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    monkeypatch.setenv("DATASET_SELECT_CACHE_ENABLED", "1")

    assert plan_cache.get_dataset_selection("ALL", "fpA", "top tampers", 8) is None
    plan_cache.set_dataset_selection("ALL", "fpA", "top tampers", 8, ["db.t1", "db.t2"])
    assert plan_cache.get_dataset_selection("ALL", "fpA", "top tampers", 8) == ["db.t1", "db.t2"]

    # scope isolation — a within-source ranking never collides with the global one
    assert plan_cache.get_dataset_selection("billing", "fpA", "top tampers", 8) is None
    # fingerprint isolation — a schema/registry change (new fp) is a fresh key
    assert plan_cache.get_dataset_selection("ALL", "fpB", "top tampers", 8) is None
    # cap is part of the key
    assert plan_cache.get_dataset_selection("ALL", "fpA", "top tampers", 4) is None


# ── §1 shared introspection cache ───────────────────────────────────────────────


def test_introspect_cache_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    assert plan_cache.get_introspect("src", "ds1") is None
    plan_cache.set_introspect("src", "ds1", {"columns": [{"name": "id", "type": "int"}]})
    assert plan_cache.get_introspect("src", "ds1") == {"columns": [{"name": "id", "type": "int"}]}
    # per (source, dataset) keyed — different dataset misses
    assert plan_cache.get_introspect("src", "ds2") is None


# ── §2/P3 leader lock ───────────────────────────────────────────────────────────


def test_leader_lock_only_one_winner(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: fake)
    # First caller wins the NX lock; a concurrent second caller loses.
    assert plan_cache.try_acquire_lock("register:tool-x", 60) is True
    assert plan_cache.try_acquire_lock("register:tool-x", 60) is False
    # A different tool is independent.
    assert plan_cache.try_acquire_lock("register:tool-y", 60) is True


def test_leader_lock_fail_open_without_redis(monkeypatch):
    monkeypatch.setattr(plan_cache, "_get_redis", lambda: None)
    # No Redis ⇒ no coordination ⇒ everyone proceeds (legacy single-node).
    assert plan_cache.try_acquire_lock("register:tool-x", 60) is True
    assert plan_cache.try_acquire_lock("register:tool-x", 60) is True


# ── §1 single-flight ────────────────────────────────────────────────────────────


def test_single_flight_collapses_concurrent_callers():
    import query_planner

    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        await asyncio.sleep(0.02)   # hold the in-flight window open
        return ["result"]

    async def run():
        # Ten concurrent identical cold misses must share ONE factory run.
        results = await asyncio.gather(
            *[query_planner._single_flight("k1", factory) for _ in range(10)]
        )
        return results

    results = asyncio.run(run())
    assert all(r == ["result"] for r in results)
    assert calls["n"] == 1  # collapsed to a single execution
    assert query_planner._inflight == {}  # no leak


def test_single_flight_distinct_keys_run_independently():
    import query_planner

    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return calls["n"]

    async def run():
        return await asyncio.gather(
            query_planner._single_flight("a", factory),
            query_planner._single_flight("b", factory),
        )

    asyncio.run(run())
    assert calls["n"] == 2  # different keys ⇒ two runs


def test_single_flight_propagates_and_clears_on_error():
    import query_planner

    async def boom():
        raise RuntimeError("planner exploded")

    async def run():
        return await query_planner._single_flight("err", boom)

    try:
        asyncio.run(run())
        assert False, "expected the error to propagate"
    except RuntimeError as exc:
        assert "planner exploded" in str(exc)
    assert query_planner._inflight == {}  # entry cleared even on error


# ── §2 registration: leader lock + deregister gating ────────────────────────────


class _FakeResp:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAsyncClient:
    """Records every discovery call so a test can assert what was (not) sent."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        _FakeAsyncClient.calls.append(("POST", url))
        return _FakeResp(200)

    async def put(self, url, **k):
        _FakeAsyncClient.calls.append(("PUT", url))
        return _FakeResp(200)

    async def request(self, method, url, **k):
        _FakeAsyncClient.calls.append((method, url))
        return _FakeResp(200)


def _fake_settings(**over):
    base = dict(
        discovery_url="http://discovery:9000",
        mcp_api_key="key",
        mcp_public_base_url="http://gateway/mcp",
        org_id="acme",
        dept_ids=["d1"],
        port=8090,
        heartbeat_interval_seconds=60,
        deregister_on_shutdown=False,
        register_leader_lock=True,
        resolved_instance_id="pod-1",
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def test_deregister_disabled_by_default(monkeypatch):
    import registration

    _FakeAsyncClient.calls = []
    registration._registered.clear()
    registration._registered["acme-d1-s1"] = {"tool_id": "acme-d1-s1", "name": "s1"}
    monkeypatch.setattr(registration, "get_settings", lambda: _fake_settings())
    monkeypatch.setattr(registration.httpx, "AsyncClient", _FakeAsyncClient)

    asyncio.run(registration.deregister_all())
    # Default OFF ⇒ no DELETE to discovery (the shared record must survive).
    assert _FakeAsyncClient.calls == []


def test_deregister_enabled_sends_delete(monkeypatch):
    import registration

    _FakeAsyncClient.calls = []
    registration._registered.clear()
    registration._registered["acme-d1-s1"] = {"tool_id": "acme-d1-s1", "name": "s1"}
    monkeypatch.setattr(
        registration, "get_settings", lambda: _fake_settings(deregister_on_shutdown=True)
    )
    monkeypatch.setattr(registration.httpx, "AsyncClient", _FakeAsyncClient)

    asyncio.run(registration.deregister_all())
    assert _FakeAsyncClient.calls == [("DELETE", "http://discovery:9000/tools/acme-d1-s1")]


def test_register_leader_lock_loser_still_tracked(monkeypatch):
    """A leader-lock loser skips the POST but is STILL tracked so it heartbeats
    (keeps the shared record warm) and can re-register on a 404."""
    import registration

    _FakeAsyncClient.calls = []
    registration._registered.clear()
    monkeypatch.setattr(registration, "get_settings", lambda: _fake_settings())
    monkeypatch.setattr(registration.httpx, "AsyncClient", _FakeAsyncClient)
    # Lose every lock.
    monkeypatch.setattr("plan_cache.try_acquire_lock", lambda *a, **k: False)

    sources = [{"source_id": "s1", "org_id": "acme", "dept_id": "d1", "type": "structured"}]
    asyncio.run(registration.register_all(sources))

    assert _FakeAsyncClient.calls == []          # loser did not POST
    assert "acme-d1-s1" in registration._registered  # but is tracked for heartbeat


def test_register_leader_winner_posts(monkeypatch):
    import registration

    _FakeAsyncClient.calls = []
    registration._registered.clear()
    monkeypatch.setattr(registration, "get_settings", lambda: _fake_settings())
    monkeypatch.setattr(registration.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("plan_cache.try_acquire_lock", lambda *a, **k: True)

    sources = [{"source_id": "s1", "org_id": "acme", "dept_id": "d1", "type": "structured"}]
    asyncio.run(registration.register_all(sources))

    assert ("POST", "http://discovery:9000/tools/register") in _FakeAsyncClient.calls
    assert "acme-d1-s1" in registration._registered
