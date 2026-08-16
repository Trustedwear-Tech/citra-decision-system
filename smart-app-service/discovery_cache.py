"""Shared cache for resolving ``source_id`` -> dept-MCP coordinates.

Both the MCP and RAG proxies need to translate a logical ``source_id`` into a
concrete ``query_endpoint`` (and per-tool ``api_key`` / ``query_timeout_seconds``).
Hitting discovery on every tool call would add 50-100ms of latency per LLM step,
so we cache the lookup with a TTL.

Backed by the **shared cache Redis** (not an in-process dict): with N replicas an
in-process cache meant a `POST /internal/discovery/invalidate` only cleared the
replica that received it, so other replicas kept serving a rotated/removed
endpoint until their own TTL expired. With Redis, an invalidate is seen by every
replica at once. Fail-OPEN: any Redis error degrades to a live discovery fetch
(never breaks resolution), mirroring the platform's other best-effort caches.

Resolution requires a Citra JWT (discovery filters by org/dept/role from the JWT
claims), so the caller forwards the user's JWT into the lookup; the cache is
keyed per (stable-hashed JWT, source_id) to preserve per-user visibility.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_KEY_PREFIX = "smartapp:disco:"
_redis_warned = False


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class ResolvedSource:
    source_id: str
    query_endpoint: str
    api_key: Optional[str]
    source_type: Optional[str]
    query_timeout_seconds: Optional[float]


def _cache():
    from citra_cache import get_cache_manager

    return get_cache_manager()


def _warn_once(op: str, exc: Exception) -> None:
    global _redis_warned
    if not _redis_warned:
        _redis_warned = True
        logger.warning(
            "discovery_cache: Redis %s failed (%s) — resolution now hits discovery "
            "on every call until Redis recovers; logged once.", op, exc,
        )


def _cache_key(user_jwt: str, source_id: str) -> str:
    # Stable hash (sha256, NOT Python's per-process-salted hash()) so the key is
    # identical across replicas; never log the JWT itself.
    h = hashlib.sha256(user_jwt.encode()).hexdigest()[:16]
    return f"{_KEY_PREFIX}{h}:{source_id}"


def _get_cached(key: str) -> Optional[ResolvedSource]:
    try:
        raw = _cache().get(key)
    except Exception as exc:  # noqa: BLE001 — fail-open
        _warn_once("get", exc)
        return None
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return ResolvedSource(**d)
    except Exception:  # noqa: BLE001 — corrupt entry → treat as miss
        return None


def _set_cached(key: str, rs: ResolvedSource, ttl: int) -> None:
    try:
        _cache().setex(key, ttl, json.dumps(asdict(rs)))
    except Exception as exc:  # noqa: BLE001 — fail-open
        _warn_once("setex", exc)


async def resolve_source(
    *,
    discovery_url: str,
    user_jwt: str,
    source_id: str,
    cache_ttl_seconds: int = 300,
) -> ResolvedSource:
    """Resolve ``source_id`` to its dept-MCP query coordinates.

    Raises ``DiscoveryError`` if discovery is unreachable or the requesting user
    has no visibility on ``source_id``.
    """
    if not discovery_url:
        raise DiscoveryError("no_discovery_url", "DISCOVERY_SERVICE_URL is unset", 503)
    if not user_jwt:
        raise DiscoveryError("no_user_jwt", "X-User-JWT header required", 401)

    key = _cache_key(user_jwt, source_id)
    hit = _get_cached(key)
    if hit is not None:
        return hit

    url = discovery_url.rstrip("/") + "/tools/available"
    try:
        # Pooled client — avoids a fresh TCP+TLS handshake per cold lookup.
        from http_client import get_http_client
        resp = await get_http_client().get(
            url, headers={"Authorization": f"Bearer {user_jwt}"}, timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise DiscoveryError("discovery_unreachable", str(e), 502) from e

    if resp.status_code == 401:
        raise DiscoveryError("discovery_unauthorised", "discovery rejected the user JWT", 401)
    if resp.status_code >= 400:
        raise DiscoveryError("discovery_error", f"discovery returned {resp.status_code}", 502)

    try:
        tools = resp.json()
    except ValueError as e:
        raise DiscoveryError("discovery_bad_json", str(e), 502) from e

    # Cache EVERY visible source from this one fetch (keyed per user+source),
    # not just the requested one — a dashboard/agent touching several sources
    # then warms in a single round-trip instead of one fetch per cold source.
    requested: Optional[ResolvedSource] = None
    saw_requested = False
    for tool in tools or []:
        sid = tool.get("source_id")
        if not sid:
            continue
        if sid == source_id:
            saw_requested = True
        endpoint = tool.get("query_endpoint") or ""
        stype = (tool.get("source_type") or "").lower()
        # Semantic (RAG) sources publish an EMPTY query_endpoint BY DESIGN — they
        # are answered by the Citra-Service platform reader (/semantic/search),
        # not a dept-MCP (RAG short-circuit). Resolve them anyway (carrying
        # source_type) so callers can branch on it and short-circuit to the reader;
        # skipping them here made every semantic PANEL 502
        # ("source … has no query_endpoint"). A STRUCTURED source with no endpoint
        # is genuinely unresolvable, so it is still skipped.
        if not endpoint and stype != "semantic":
            continue
        rs = ResolvedSource(
            source_id=sid,
            query_endpoint=endpoint,
            # Discovery NEVER returns an api_key (it stores only api_key_hash),
            # so this is always None and callers fall through to
            # settings.mcp_service_api_key — which is the intended auth anyway.
            # Kept as an explicit None (not tool.get) so nobody re-grows a
            # dependency on a field the registry cannot supply.
            api_key=None,
            source_type=tool.get("source_type"),
            query_timeout_seconds=tool.get("query_timeout_seconds"),
        )
        _set_cached(_cache_key(user_jwt, sid), rs, cache_ttl_seconds)
        if sid == source_id:
            requested = rs

    if requested is not None:
        return requested
    if saw_requested:
        raise DiscoveryError("discovery_no_endpoint", f"source {source_id} has no query_endpoint", 502)
    raise DiscoveryError("source_not_found", f"source_id={source_id} not visible to user", 404)


# Lua: SCAN + DEL every key matching ARGV[1] (already prefix-qualified). Avoids
# the O(N)-blocking KEYS command. Returns the number of keys deleted.
_INVALIDATE_LUA = (
    "local cur = '0' local n = 0 "
    "repeat "
    "  local r = redis.call('SCAN', cur, 'MATCH', ARGV[1], 'COUNT', 200) "
    "  cur = r[1] "
    "  for _, k in ipairs(r[2]) do redis.call('DEL', k); n = n + 1 end "
    "until cur == '0' "
    "return n"
)


def clear_cache() -> None:
    """Test helper — drop all discovery cache entries (across all users)."""
    prefix = os.getenv("REDIS_KEY_PREFIX", "")
    try:
        _cache().eval(_INVALIDATE_LUA, 0, f"{prefix}{_KEY_PREFIX}*")
    except Exception as exc:  # noqa: BLE001
        _warn_once("clear_cache", exc)


def invalidate_source(source_id: str) -> int:
    """Drop every cached entry for ``source_id`` (across all users), on EVERY
    replica at once (the keys live in shared Redis).

    Called by ``POST /smart-app/internal/discovery/invalidate`` when discovery /
    dept-MCP signals a source was removed or its endpoint rotated. Returns the
    number of entries dropped.
    """
    if not source_id:
        return 0
    prefix = os.getenv("REDIS_KEY_PREFIX", "")
    pattern = f"{prefix}{_KEY_PREFIX}*:{source_id}"
    try:
        return int(_cache().eval(_INVALIDATE_LUA, 0, pattern) or 0)
    except Exception as exc:  # noqa: BLE001 — fail-open (entries TTL out anyway)
        _warn_once("invalidate_source", exc)
        return 0
