#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Centralized Cache Manager — Redis only.
No local in-memory fallback: all instances share the same Redis,
and errors propagate immediately so callers can handle them.
"""

import logging
import os
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
from contextlib import contextmanager
import uuid

import redis
from redis.connection import ConnectionPool

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Centralized cache manager — Redis only, no local fallback.
    Errors propagate to callers so issues are visible immediately.
    Periodically retries connection if Redis goes down.

    Key prefixing:
      Set REDIS_KEY_PREFIX env var (e.g. "prod:", "test:", "dev:") to namespace
      all keys.  This prevents cross-environment collisions when multiple
      environments share the same Redis instance/DB.
    """

    def __init__(self, tier: str = "cache"):
        # tier ∈ {"cache", "coordination"}:
        #   • "cache"        — the LRU, no-persistence cache tier (REDIS_*).
        #   • "coordination" — durable, noeviction tier for locks / leases /
        #     counters / idempotency keys. An LRU cache tier can EVICT a
        #     not-yet-expired lock or lease under memory pressure (and a restart
        #     wipes it with no persistence), which silently breaks the invariant
        #     the lock was protecting (duplicate cron fires, orphaned sandboxes).
        #     Coordination state must NOT ride the cache tier.
        self._tier = tier
        self.redis_client: Optional[redis.Redis] = None
        self.use_redis = False
        self.connection_pool: Optional[ConnectionPool] = None
        self._redis_last_check: Optional[float] = None
        self._redis_check_interval = 60  # seconds between reconnect attempts
        self._key_prefix: str = os.getenv("REDIS_KEY_PREFIX", "")
        self._connect_to_redis()

    def _resolve_params(self) -> Dict[str, Any]:
        """Return (host, port, db, username, password, ssl) for this tier.

        Cache tier reads REDIS_*. Coordination tier prefers a dedicated
        COORDINATION_REDIS_*, then the durable QUEUE_REDIS_* (the queue tier is
        already AOF + noeviction), and only as a last resort the cache REDIS_*
        — which it WARNs about, because coordination on an evict-prone tier is a
        latent correctness bug, not just an HA gap.
        """
        if self._tier == "coordination":
            host = (os.getenv("COORDINATION_REDIS_HOST")
                    or os.getenv("QUEUE_REDIS_HOST")
                    or os.getenv("REDIS_HOST"))
            if not host:
                raise RuntimeError(
                    "coordination Redis host not set; set COORDINATION_REDIS_HOST "
                    "(or QUEUE_REDIS_HOST / REDIS_HOST). Refusing to default to localhost."
                )
            dedicated = bool(os.getenv("COORDINATION_REDIS_HOST") or os.getenv("QUEUE_REDIS_HOST"))
            if not dedicated:
                logger.warning(
                    "⚠️ coordination Redis is falling back to the CACHE tier "
                    "(REDIS_HOST). Locks/leases/counters can be LRU-evicted or "
                    "wiped on restart there — duplicate cron fires / orphaned "
                    "sessions. Point COORDINATION_REDIS_* (or QUEUE_REDIS_*) at a "
                    "noeviction + persistent Redis."
                )
            # Default to a distinct DB so a fallback onto a shared host doesn't
            # collide with cache (db 0) keys; an explicit COORDINATION_REDIS_DB wins.
            db = int(os.getenv("COORDINATION_REDIS_DB")
                     or os.getenv("QUEUE_REDIS_DB")
                     or "3")
            username = os.getenv("COORDINATION_REDIS_USERNAME") or os.getenv("QUEUE_REDIS_USERNAME") or os.getenv("REDIS_USERNAME")
            password = os.getenv("COORDINATION_REDIS_PASSWORD") or os.getenv("QUEUE_REDIS_PASSWORD") or os.getenv("REDIS_PASSWORD")
            port = int(os.getenv("COORDINATION_REDIS_PORT") or os.getenv("QUEUE_REDIS_PORT") or os.getenv("REDIS_PORT", "6379"))
            ssl = os.getenv("COORDINATION_REDIS_SSL", os.getenv("REDIS_SSL", "false"))
            return {"host": host, "port": port, "db": db, "username": username,
                    "password": password, "ssl": ssl}

        # cache tier (default) — REQUIRED, no localhost fallback (a wrong default
        # would bleed cache across environments).
        host = os.getenv("REDIS_HOST")
        if not host:
            raise RuntimeError(
                "REDIS_HOST is not set; refusing to default to localhost. "
                "Set REDIS_HOST explicitly."
            )
        return {
            "host": host,
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "db": int(os.getenv("REDIS_DB", "0")),
            "username": os.getenv("REDIS_USERNAME"),
            "password": os.getenv("REDIS_PASSWORD"),
            "ssl": os.getenv("REDIS_SSL", "false"),
        }

    def _connect_to_redis(self):
        """Establish Redis connection. Raises on failure at startup."""
        logger.info("🔌 Connecting to Redis (%s tier)...", self._tier)

        pool_max_connections = int(os.getenv("REDIS_CONNECTION_POOL_MAX_CONNECTIONS", "50"))
        resolved = self._resolve_params()

        pool_params = {
            'host': resolved["host"],
            'port': resolved["port"],
            'db': resolved["db"],
            'max_connections': pool_max_connections,
            'socket_connect_timeout': int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
            'socket_timeout': int(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
            'retry_on_timeout': True,
            'health_check_interval': 30
        }

        if resolved.get("username"):
            pool_params['username'] = resolved["username"]

        if resolved.get("password"):
            pool_params['password'] = resolved["password"]

        if str(resolved.get("ssl", "false")).lower() == "true":
            from redis.connection import SSLConnection
            pool_params['connection_class'] = SSLConnection
            pool_params['ssl_cert_reqs'] = None

        self.connection_pool = ConnectionPool(**pool_params)
        self.redis_client = redis.Redis(
            connection_pool=self.connection_pool,
            decode_responses=True
        )

        # Test connection — let it raise if Redis is unreachable
        self.redis_client.ping()
        self.use_redis = True
        self._redis_last_check = time.time()
        logger.info("✅ Redis %s tier connected (db=%s)", self._tier, resolved["db"])
        logger.info(f"🏊 Connection pool size: {pool_max_connections}")
        if self._key_prefix:
            logger.info(f"🔑 Redis key prefix: {self._key_prefix}")

    def _pk(self, key: str) -> str:
        """Return the prefixed key. No-op when prefix is empty."""
        return f"{self._key_prefix}{key}" if self._key_prefix else key

    def _ensure_connected(self):
        """Re-establish connection if Redis was temporarily down."""
        if self.use_redis:
            return
        if (self._redis_last_check is None
                or time.time() - self._redis_last_check > self._redis_check_interval):
            logger.info("🔄 Attempting to reconnect to Redis...")
            try:
                self._connect_to_redis()
            except Exception as e:
                self._redis_last_check = time.time()
                raise ConnectionError(f"Redis reconnection failed: {e}") from e
        else:
            raise ConnectionError("Redis is unavailable (next retry in <60 s)")

    def _execute(self, operation: str, *args, **kwargs):
        """Run a Redis operation. Raises on failure — no silent fallback."""
        self._ensure_connected()
        try:
            return getattr(self.redis_client, operation)(*args, **kwargs)
        except redis.ConnectionError as e:
            logger.error(f"❌ Redis connection lost during '{operation}': {e}")
            self.use_redis = False
            self._redis_last_check = time.time()
            raise
        except redis.RedisError as e:
            logger.error(f"❌ Redis operation '{operation}' failed: {e}")
            raise
    
    # ── public API ────────────────────────────────────────────────

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set a key with optional expiration"""
        if ex:
            return self._execute('setex', self._pk(key), ex, value)
        return self._execute('set', self._pk(key), value)

    def setex(self, key: str, time: int, value: str) -> bool:
        """Set a key with expiration"""
        return self._execute('setex', self._pk(key), time, value)

    def get(self, key: str) -> Optional[str]:
        """Get a key's value"""
        return self._execute('get', self._pk(key))

    def delete(self, *keys: str) -> int:
        """Delete one or more keys"""
        return self._execute('delete', *(self._pk(k) for k in keys))

    def exists(self, key: str) -> bool:
        """Check if key exists"""
        return bool(self._execute('exists', self._pk(key)))

    def keys(self, pattern: str) -> List[str]:
        """Get keys matching pattern"""
        return self._execute('keys', self._pk(pattern))

    def ping(self) -> bool:
        """Health check"""
        try:
            return self._execute('ping')
        except Exception:
            return False

    def eval(self, script: str, numkeys: int, *args):
        """Evaluate Lua script"""
        return self._execute('eval', script, numkeys, *args)

    @contextmanager
    def lock(self, resource_id: str, timeout: Optional[int] = None):
        """Distributed lock via Redis SET NX EX."""
        lock_key = self._pk(f"lock:{resource_id}")
        lock_value = str(uuid.uuid4())
        lock_timeout = timeout or 30

        acquired = False
        try:
            acquired = self._execute('set', lock_key, lock_value, nx=True, ex=lock_timeout)
            yield bool(acquired)
        finally:
            if acquired:
                lua_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                try:
                    self._execute('eval', lua_script, 1, lock_key, lock_value)
                except Exception as e:
                    logger.warning(f"Failed to release lock '{resource_id}': {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics and health info"""
        stats = {
            "cache_type": "redis",
            "redis_connected": self.use_redis,
            "timestamp": datetime.now().isoformat()
        }
        if self.use_redis and self.connection_pool:
            try:
                stats["redis_pool"] = {
                    "max_connections": self.connection_pool.max_connections,
                    "created_connections": self.connection_pool._created_connections,
                }
            except Exception:
                pass
        return stats

    def set_nx(self, key: str, value: str, ex: int) -> bool:
        """Atomic SET NX EX — returns True if key was set (lock acquired).

        This is the async-safe alternative to the ``lock()`` context manager.
        Use ``delete_if_owner()`` to release the lock after work is done.
        """
        result = self._execute('set', self._pk(key), value, nx=True, ex=ex)
        return bool(result)

    def delete_if_owner(self, key: str, expected_value: str) -> bool:
        """Delete key only if current value matches expected_value (CAS delete).

        Uses a Lua script for atomicity. Returns True if the key was deleted.
        """
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            result = self._execute('eval', lua, 1, self._pk(key), expected_value)
            return result == 1
        except Exception as e:
            logger.warning(f"Failed to CAS-delete key '{key}': {e}")
            return False

    def publish(self, channel: str, message: str) -> int:
        """Publish to Redis pub/sub. Returns subscriber count."""
        return self._execute('publish', self._pk(channel), message)


# Global instances — the cache tier and the coordination tier are separate
# singletons (different Redis target), so coordination state never rides the
# evict-prone cache tier.
_cache_manager: Optional[CacheManager] = None
_coordination_manager: Optional[CacheManager] = None

def get_cache_manager() -> CacheManager:
    """Get the global cache-tier manager (LRU, no-persistence Redis)."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager(tier="cache")
    return _cache_manager


def get_coordination_manager() -> CacheManager:
    """Get the global COORDINATION-tier manager — for locks, leases, rate-limit
    counters, idempotency keys. Backed by a durable, noeviction Redis
    (COORDINATION_REDIS_* → QUEUE_REDIS_* → REDIS_*; see CacheManager). Same API
    as the cache manager, different (durable) target — so an LRU eviction or a
    cache-tier flush can never drop a lock/lease/counter."""
    global _coordination_manager
    if _coordination_manager is None:
        _coordination_manager = CacheManager(tier="coordination")
    return _coordination_manager


def get_cache_client():
    """Get Redis client (backward-compat shim)."""
    return get_cache_manager().redis_client


# Module-level lazy proxy so `from citra_cache import cache` works as a
# drop-in replacement for the legacy `from cache_manager import cache`.
# Resolves to the singleton CacheManager on first attribute access.
class _LazyCache:
    def __getattr__(self, name):
        return getattr(get_cache_manager(), name)


cache = _LazyCache()
