"""
Dept MCP Server — Live REST API engine
========================================
Handles ``type=rest_api`` sources by invoking the upstream HTTP endpoint
**at query time** and returning the response body as a single ChunkResult.
No Milvus collection is involved — this is a pure passthrough.

Source schema consumed (from ``dept_sources``)::

    type: rest_api
    connection:
      base_url:        "https://api.example.com/weather/{{location}}"
      method:          "GET" | "POST"
      headers:         { "X-Custom": "value" }
      query_template:  { "lang": "en" }
      body_template:   { "city": "{{location}}" }
      response_path:   "data.results"        # optional dotted-path extractor
      timeout_seconds: 15
      auth:
        type:        "bearer" | "api_key" | "none"
        env_prefix:  "AGRI_WEATHER"          # creds resolved from env
    options:
      invocation_template:  |
        Free-text hint to the LLM describing how to translate a user
        question into request params (placeholders {{like_this}}).
      rate_limit_rpm:  60                    # token-bucket cap per source

Security:
  * SSRF guard blocks loopback, link-local, and private IP ranges unless
    the operator explicitly opts in via ``connection.allow_private = true``.
  * Credentials are read from env at request time; never persisted in Mongo.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
from collections import deque
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from config import get_settings
from models import ChunkResult
from planners._llm import call_json_llm
from router import get_source

logger = logging.getLogger(__name__)


# ─── Per-source rate limiting ─────────────────────────────────────────
#
# Simple in-process token bucket keyed by source_id. Default 60 req/min
# (one per second average, burst = 1 minute's worth). Operator can override
# via ``options.rate_limit_rpm``. Multi-replica deployments share nothing
# here — a small over-burst per replica is acceptable.

_RATE_WINDOW_SECONDS = 60.0
_rate_history: Dict[str, deque] = {}
_rate_lock = asyncio.Lock()


async def _rate_check(source_id: str, rpm: int) -> bool:
    if rpm <= 0:
        return True
    now = time.monotonic()
    async with _rate_lock:
        bucket = _rate_history.setdefault(source_id, deque())
        cutoff = now - _RATE_WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= rpm:
            return False
        bucket.append(now)
        return True


# ─── SSRF guard ──────────────────────────────────────────────────────


def _is_private_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True  # fail-closed
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _ssrf_check(url: str, allow_private: bool) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"unsupported scheme: {parsed.scheme!r}"
    host = parsed.hostname or ""
    if not host:
        return "missing host"
    if not allow_private and _is_private_address(host):
        return f"refusing to call private/loopback host: {host!r}"
    return None


# ─── Auth resolver ───────────────────────────────────────────────────


def _resolve_auth(conn: Dict[str, Any]) -> Dict[str, str]:
    """Return any extra headers derived from ``connection.auth``."""
    auth = (conn or {}).get("auth") or {}
    kind = (auth.get("type") or "none").lower()
    env_prefix = (auth.get("env_prefix") or "").strip()
    if not env_prefix or kind == "none":
        return {}

    if kind == "bearer":
        token = os.environ.get(f"{env_prefix}_TOKEN") or os.environ.get(f"{env_prefix}_API_KEY")
        if token:
            return {"Authorization": f"Bearer {token}"}
    elif kind == "api_key":
        key = os.environ.get(f"{env_prefix}_API_KEY")
        if key:
            header_name = auth.get("header_name", "X-API-Key")
            return {header_name: key}
    elif kind == "basic":
        user = os.environ.get(f"{env_prefix}_USER")
        pwd  = os.environ.get(f"{env_prefix}_PASSWORD")
        if user and pwd:
            import base64
            encoded = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
    return {}


# ─── LLM request crafter ─────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You translate a user's natural-language question into a single REST "
    "API request for the given upstream service. Return JSON ONLY with "
    "keys: path_overrides (object — substitutions for {{placeholders}} in "
    "the URL/body), query_params (object), body_overrides (object). Use "
    "the operator's invocation hint and the source description to fill "
    "placeholders accurately. Prefer empty objects over invented values."
)


async def _craft_request(
    *, query: str, source: Dict[str, Any], timeout_seconds: float
) -> Dict[str, Any]:
    invocation = (source.get("options") or {}).get("invocation_template") or ""
    description = source.get("description") or ""
    base_url = (source.get("connection") or {}).get("base_url") or ""
    method = ((source.get("connection") or {}).get("method") or "GET").upper()

    user = (
        f"Source: {source.get('name')}\n"
        f"Description: {description}\n"
        f"Method: {method}\n"
        f"URL template: {base_url}\n"
        f"Invocation hint:\n{invocation}\n\n"
        f"User question: {query}"
    )
    plan = await call_json_llm(
        system=_SYSTEM_PROMPT,
        user=user,
        timeout_seconds=timeout_seconds,
        max_tokens=4000,
    )
    return plan or {}


# ─── Template interpolation ─────────────────────────────────────────


def _interpolate(template: Any, subs: Dict[str, Any]) -> Any:
    """Replace ``{{name}}`` placeholders in strings recursively."""
    if isinstance(template, str):
        out = template
        for k, v in subs.items():
            out = out.replace("{{" + str(k) + "}}", str(v))
        return out
    if isinstance(template, dict):
        return {k: _interpolate(v, subs) for k, v in template.items()}
    if isinstance(template, list):
        return [_interpolate(item, subs) for item in template]
    return template


# ─── Response extraction ────────────────────────────────────────────


def _extract_path(payload: Any, dotted_path: str) -> Any:
    if not dotted_path:
        return payload
    cur = payload
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _coerce_text(payload: Any, max_len: int = 8000) -> str:
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            text = str(payload)
    if len(text) > max_len:
        text = text[:max_len] + "\n... [truncated]"
    return text


# ─── Public entrypoint ──────────────────────────────────────────────


async def search_api(
    query: str,
    source_id: str,
    max_results: int = 5,
) -> List[ChunkResult]:
    """Live-call the upstream API for a rest_api source and return one ChunkResult."""
    source = get_source(source_id)
    if not source:
        logger.warning(f"⚠️ [API] Unknown source_id: {source_id!r}")
        return []
    if source.get("type") != "rest_api":
        logger.warning(f"⚠️ [API] {source_id!r} is not a rest_api source")
        return []

    conn = source.get("connection") or {}
    options = source.get("options") or {}

    base_url = conn.get("base_url") or conn.get("url")
    if not base_url:
        logger.error(f"❌ [API] {source_id!r} missing connection.base_url")
        return []

    method  = (conn.get("method") or "GET").upper()
    timeout = float(conn.get("timeout_seconds") or 15)
    rpm     = int(options.get("rate_limit_rpm", 60))
    allow_private = bool(conn.get("allow_private", False))

    # 1) Rate limit
    if not await _rate_check(source_id, rpm):
        logger.warning(f"⚠️ [API] rate limit exceeded for {source_id!r} ({rpm} rpm)")
        return [ChunkResult(
            text="Upstream API rate limit exceeded; please retry shortly.",
            score=0.0, source=source_id,
            metadata={"error": "rate_limited", "rpm": rpm},
        )]

    # 2) LLM crafts the request
    plan = await _craft_request(query=query, source=source, timeout_seconds=min(timeout, 8.0))
    path_overrides = plan.get("path_overrides") or {}
    query_params   = {**(conn.get("query_template") or {}), **(plan.get("query_params") or {})}
    body_overrides = plan.get("body_overrides") or {}

    # 3) Interpolate placeholders
    subs = {**path_overrides}
    url = _interpolate(base_url, subs)
    body = None
    if method in {"POST", "PUT", "PATCH"}:
        body = _interpolate(conn.get("body_template") or {}, subs)
        if isinstance(body, dict):
            body.update(body_overrides)

    # 4) SSRF guard (after interpolation — final URL only)
    err = _ssrf_check(url, allow_private=allow_private)
    if err:
        logger.warning(f"⚠️ [API] SSRF guard blocked {source_id!r}: {err}")
        return [ChunkResult(text=f"Upstream blocked by safety policy: {err}",
                            score=0.0, source=source_id, metadata={"error": "ssrf_blocked"})]

    # 5) Auth headers
    headers = {**(conn.get("headers") or {}), **_resolve_auth(conn)}
    headers.setdefault("Accept", "application/json")

    # 6) Fire the request
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.request(method, url, params=query_params, json=body, headers=headers)
        status = resp.status_code
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text
    except Exception as exc:
        logger.error(f"❌ [API] upstream call failed for {source_id!r}: {exc}")
        return [ChunkResult(
            text=f"Upstream API call failed: {exc}",
            score=0.0, source=source_id, metadata={"error": "upstream_failure"},
        )]

    # 7) Extract + coerce
    extracted = _extract_path(payload, conn.get("response_path") or "")
    text = _coerce_text(extracted if extracted is not None else payload)

    return [ChunkResult(
        text=text,
        score=1.0,
        source=source_id,
        metadata={
            "status_code": status,
            "url": url,
            "method": method,
            "source_type": "rest_api",
        },
    )]
