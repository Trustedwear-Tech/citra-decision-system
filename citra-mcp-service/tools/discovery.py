# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra_discovery_search — list department/source MCP tools available to
the caller.

discovery-service performs JWT-claim-based scoping (org_id + dept_ids +
roles), so we forward the caller's original Authorization header verbatim
and let discovery-service make the call. The MCP service does not
re-issue a token.

Session-scoped search cache
---------------------------
A single agent turn that picks the right enterprise source typically
re-runs the same `discovery_search(query=...)` on every follow-up turn,
which means the embedding + cosine-rank fan-out runs N times for the
same answer. To avoid that, we cache results in-process keyed by
``(session_id, query, top_k, tag, data_type)`` for
``DISCOVERY_CACHE_TTL_SECONDS`` (default 1800s = 30 min).

- Cache is per-process and per-session. No cross-tenant leakage:
  ``session_id`` comes from the caller's JWT (CallerContext) and is
  the same value the LLM uses for the multi-turn conversation.
- If the caller has no ``session_id`` claim, the cache is bypassed.
- Cache miss → call discovery-service → store result with timestamp.
- Cache hit → return stored payload with ``cached=True`` so the agent
  can see it was free.
- Capacity-bounded LRU eviction so a long-running process doesn't grow
  without bound.
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from threading import Lock
from typing import Any

import httpx

from auth import CallerContext
from config import get_config
from .registry import ToolSpec, register_tool

logger = logging.getLogger(__name__)


# ----- session-scoped discovery cache ---------------------------------------

_DISCOVERY_CACHE_TTL = int(os.getenv("DISCOVERY_CACHE_TTL_SECONDS", "1800"))
_DISCOVERY_CACHE_MAX_ENTRIES = int(os.getenv("DISCOVERY_CACHE_MAX_ENTRIES", "1024"))

# Key: (session_id, query, top_k, tag, data_type) — value: (expires_at, payload)
_DISCOVERY_CACHE: "OrderedDict[tuple[str, str, int, str, str], tuple[float, dict[str, Any]]]" = OrderedDict()
_DISCOVERY_CACHE_LOCK = Lock()


def _cache_key(session_id: str, args: dict[str, Any]) -> tuple[str, str, int, str, str]:
    return (
        session_id,
        (args.get("query") or "").strip(),
        int(args.get("top_k") or 0),
        str(args.get("tag") or ""),
        str(args.get("data_type") or ""),
    )


def _cache_get(key: tuple[str, str, int, str, str]) -> dict[str, Any] | None:
    now = time.time()
    with _DISCOVERY_CACHE_LOCK:
        entry = _DISCOVERY_CACHE.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at < now:
            _DISCOVERY_CACHE.pop(key, None)
            return None
        # touch for LRU
        _DISCOVERY_CACHE.move_to_end(key)
        return dict(payload)


def _cache_put(key: tuple[str, str, int, str, str], payload: dict[str, Any]) -> None:
    expires_at = time.time() + _DISCOVERY_CACHE_TTL
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE[key] = (expires_at, dict(payload))
        _DISCOVERY_CACHE.move_to_end(key)
        while len(_DISCOVERY_CACHE) > _DISCOVERY_CACHE_MAX_ENTRIES:
            _DISCOVERY_CACHE.popitem(last=False)


def discovery_cache_stats() -> dict[str, Any]:
    """For /health or admin endpoints if exposed later."""
    with _DISCOVERY_CACHE_LOCK:
        return {
            "entries": len(_DISCOVERY_CACHE),
            "max_entries": _DISCOVERY_CACHE_MAX_ENTRIES,
            "ttl_seconds": _DISCOVERY_CACHE_TTL,
        }


def _cache_find_tool(session_id: str, tool_name: str) -> dict[str, Any] | None:
    """Resolve a tool spec by name from this session's cached search results.

    ``citra_discovery_search`` already returns each tool's ``query_endpoint``
    and caches the payload per session, so ``citra_discovery_query`` can
    reuse that instead of re-downloading the whole ``/tools/available``
    catalog on every call. Scoping is preserved: the cached results came
    from a JWT-scoped ``/tools/search`` for this same ``session_id``.
    """
    if not session_id:
        return None
    now = time.time()
    with _DISCOVERY_CACHE_LOCK:
        for key, (expires_at, payload) in _DISCOVERY_CACHE.items():
            if key[0] != session_id or expires_at < now:
                continue
            for tool in payload.get("tools") or []:
                if str(tool.get("name") or "") == tool_name:
                    return dict(tool)
    return None


_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Natural-language question the agent is trying to answer. "
                "discovery-service embeds this and cosine-ranks all "
                "claim-visible tools, returning the top_k most relevant. "
                "Be specific — 'find anomalies in last month's claims' "
                "matches better than 'data'."
            ),
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top-ranked tools to return (1-50, default 10).",
            "minimum": 1,
            "maximum": 50,
        },
        "tag": {
            "type": "string",
            "description": "Optional pre-filter (case-insensitive substring match on tags).",
        },
        "data_type": {
            "type": "string",
            "description": "Optional pre-filter on data_type (e.g. 'tabular', 'documents').",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def _exec_discovery_search(
    args: dict[str, Any],
    caller: CallerContext,
) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.discovery_service_url:
        raise RuntimeError("DISCOVERY_SERVICE_URL is not configured on citra-mcp-service")

    auth_header = caller.auth_header
    if not auth_header:
        raise RuntimeError("missing Authorization header to forward to discovery-service")

    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")

    # Session-scoped cache: a no-op for callers without a session_id (e.g.
    # service-to-service callers). For the agent in action-chat-service,
    # session_id is set on the scoped token and stays constant for the
    # whole multi-turn chat — so identical follow-up searches hit the cache.
    cache_key = _cache_key(caller.session_id, args) if caller.session_id else None
    if cache_key is not None:
        hit = _cache_get(cache_key)
        if hit is not None:
            hit["cached"] = True
            logger.info(
                "[tool=citra_discovery_search] CACHE HIT user=%s session=%s q_len=%d returned=%d",
                caller.user_id, caller.session_id, len(query), hit.get("matched", 0),
            )
            return hit

    params: dict[str, Any] = {"q": query}
    if args.get("top_k"):
        params["top_k"] = int(args["top_k"])
    if args.get("tag"):
        params["tag"] = str(args["tag"])
    if args.get("data_type"):
        params["data_type"] = str(args["data_type"])

    started = time.time()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{cfg.discovery_service_url.rstrip('/')}/tools/search",
            headers={"Authorization": auth_header},
            params=params,
        )
        r.raise_for_status()
        data = r.json() or []
    if not isinstance(data, list):
        data = []

    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_discovery_search] user=%s session=%s q_len=%d returned=%d ms=%d",
        caller.user_id, caller.session_id, len(query), len(data), elapsed_ms,
    )
    payload = {
        "tools": data,
        "matched": len(data),
        "query": query,
        "elapsed_ms": elapsed_ms,
        "cached": False,
    }
    if cache_key is not None:
        _cache_put(cache_key, payload)
    return payload


register_tool(ToolSpec(
    name="citra_discovery_search",
    description=(
        "Find the most relevant department / source MCP tools for a "
        "natural-language query. discovery-service embeds the query and "
        "cosine-ranks all claim-visible tools, returning the top_k "
        "(default 10) with a `relevance_score`. Each result includes "
        "{name, description, query_endpoint, source_id, tags, data_types, "
        "source_type, taxonomy, relevance_score}. Call this FIRST to pick "
        "which source to invoke via citra_discovery_query — do NOT try to "
        "list everything and scan. "
        "Results are CACHED per session for 30 min keyed by (query, top_k, "
        "tag, data_type); the response sets `cached: true` on a cache hit so "
        "repeat calls in the same conversation are free."
    ),
    schema=_DISCOVERY_SCHEMA,
    executor=_exec_discovery_search,
))


# ---------------------------------------------------------------------------
# citra_discovery_query — execute a query against a discovered tool's
# query_endpoint. The agent calls citra_discovery_search first to pick a
# source, then calls this with the tool's name plus the source-specific
# query payload.
# ---------------------------------------------------------------------------
_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_name": {
            "type": "string",
            "description": "Name of the tool from citra_discovery_search.",
        },
        "args": {
            "type": "object",
            "description": (
                "Source-specific query payload forwarded verbatim to the "
                "tool's query_endpoint."
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "Override the tool's declared timeout (1-120).",
            "minimum": 1,
            "maximum": 120,
        },
    },
    "required": ["tool_name", "args"],
    "additionalProperties": False,
}


async def _exec_semantic_query(
    spec: dict[str, Any],
    tool_name: str,
    payload: dict[str, Any],
    caller: CallerContext,
    timeout: int,
    resolved_via: str,
) -> dict[str, Any]:
    """RAG short-circuit: query a semantic source via the Citra-Service platform
    reader (POST /semantic/search) — the dept-MCP serves no RAG. Maps the agent's
    RAG payload to the reader's contract:
      • ``doc_path`` ⇒ fetch the WHOLE document (all sections, ordered),
      • ``doc_types`` ⇒ a doc_type metadata filter,
    and forwards the caller's JWT so dept scoping / RLS holds at the reader."""
    cfg = get_config()
    if not cfg.citra_service_url:
        raise RuntimeError(
            "CITRA_SERVICE_URL is not configured — required to answer a semantic "
            "(RAG) source (the dept-MCP no longer serves RAG)."
        )
    source_id = spec.get("source_id")
    try:
        _tk = int(payload.get("top_k") or 8)
    except (TypeError, ValueError):
        _tk = 8
    body: dict[str, Any] = {
        "source_id": source_id,
        "query": str(payload.get("query") or ""),
        "top_k": max(1, min(_tk, 100)),
    }
    # The MCP-published Milvus collection (from discovery) — read it directly, no
    # prefix derivation on the platform side.
    if spec.get("rag_collection"):
        body["rag_collection"] = spec["rag_collection"]
    doc_path = payload.get("doc_path")
    if isinstance(doc_path, str) and doc_path.strip():
        body["doc_path"] = doc_path.strip()       # whole-document read
    doc_types = payload.get("doc_types")
    if isinstance(doc_types, list) and doc_types:
        body["filters"] = {"doc_type": [str(d) for d in doc_types if d]}
    headers = {
        "Authorization": caller.auth_header,       # forward the caller's JWT (dept scope)
        "Content-Type": "application/json",
    }
    started = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        rr = await client.post(
            f"{cfg.citra_service_url.rstrip('/')}/semantic/search",
            headers=headers,
            json=body,
        )
        if rr.status_code >= 400:
            try:
                detail = rr.json()
            except Exception:  # noqa: BLE001
                detail = {"detail": rr.text}
            raise RuntimeError(
                f"semantic search {rr.status_code}: "
                f"{detail.get('detail') if isinstance(detail, dict) else detail}"
            )
        try:
            data = rr.json()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"semantic search returned non-JSON body: {e}") from e
    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_discovery_query] user=%s tool=%s resolved_via=%s kind=semantic ms=%d",
        caller.user_id, tool_name, resolved_via, elapsed_ms,
    )
    return {
        "tool_name": tool_name,
        "source_id": source_id,
        "result": data,
        "elapsed_ms": elapsed_ms,
    }


async def _exec_discovery_query(
    args: dict[str, Any],
    caller: CallerContext,
) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.discovery_service_url:
        raise RuntimeError("DISCOVERY_SERVICE_URL is not configured")
    if not caller.auth_header:
        raise RuntimeError("missing Authorization header")

    tool_name = (args.get("tool_name") or "").strip()
    if not tool_name:
        raise ValueError("tool_name is required")
    payload = args.get("args")
    if not isinstance(payload, dict):
        raise ValueError("args must be an object")

    # Step 1: resolve the tool's query_endpoint. Prefer this session's
    # cached citra_discovery_search results — they already carry
    # query_endpoint, so no HTTP call is needed. Only fall back to a full
    # /tools/available listing on a genuine cache miss (expired entry, or
    # a tool_name the agent never discovered this session).
    spec = _cache_find_tool(caller.session_id, tool_name)
    resolved_via = "cache"
    if spec is None:
        resolved_via = "catalog"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{cfg.discovery_service_url.rstrip('/')}/tools/available",
                headers={"Authorization": caller.auth_header},
            )
            r.raise_for_status()
            tools = r.json() or []
        if not isinstance(tools, list):
            tools = []
        spec = next((t for t in tools if str(t.get("name") or "") == tool_name), None)
        if spec is None:
            raise ValueError(f"tool {tool_name!r} not found in discovery catalog")
    # Step 2: forward the user-supplied args to the tool's endpoint.
    declared_timeout = int(spec.get("query_timeout_seconds") or 30)
    timeout = int(args.get("timeout_seconds") or declared_timeout)
    timeout = max(1, min(timeout, 120))

    # RAG short-circuit: a `kind=semantic` source is answered by the Citra-Service
    # platform reader (Milvus direct), NEVER the dept-MCP (which serves no RAG and
    # would 404 "Unknown source"). Route it there instead of the query_endpoint.
    # Structured sources fall through to the dept-MCP as before.
    source_type = str(spec.get("source_type") or "").strip().lower()
    if source_type == "semantic":
        return await _exec_semantic_query(spec, tool_name, payload, caller, timeout, resolved_via)

    endpoint = (spec.get("query_endpoint") or "").strip()
    if not endpoint:
        raise RuntimeError(f"tool {tool_name!r} has no query_endpoint declared")

    # A dept-MCP gates its data plane on TWO credentials:
    #   • Authorization: Bearer <service api key>  — service-to-service auth
    #   • X-User-JWT: <end-user token>             — identity for visibility
    # So present the shared MCP service key as Authorization and forward the
    # caller's token as X-User-JWT. Forwarding the user JWT as Authorization
    # (the old behaviour) makes the MCP reject it with "Invalid API key".
    # If no service key is configured, fall back to the legacy single-header
    # form so non-keyed dev MCPs still work.
    user_token = (caller.auth_header or "").removeprefix("Bearer ").strip()
    if cfg.mcp_service_api_key:
        mcp_headers = {
            "Authorization": f"Bearer {cfg.mcp_service_api_key}",
            "X-User-JWT": user_token,
            "Content-Type": "application/json",
        }
    else:
        mcp_headers = {
            "Authorization": caller.auth_header,
            "Content-Type": "application/json",
        }

    started = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        rr = await client.post(
            endpoint,
            headers=mcp_headers,
            json=payload,
        )
        if rr.status_code >= 400:
            try:
                detail = rr.json()
            except Exception:  # noqa: BLE001
                detail = {"detail": rr.text}
            raise RuntimeError(
                f"source {rr.status_code}: "
                f"{detail.get('detail') if isinstance(detail, dict) else detail}"
            )
        try:
            data = rr.json()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"source returned non-JSON body: {e}") from e

    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_discovery_query] user=%s tool=%s resolved_via=%s ms=%d",
        caller.user_id, tool_name, resolved_via, elapsed_ms,
    )
    return {
        "tool_name": tool_name,
        "source_id": spec.get("source_id"),
        "result": data,
        "elapsed_ms": elapsed_ms,
    }


register_tool(ToolSpec(
    name="citra_discovery_query",
    description=(
        "Execute a query against a department / source tool discovered "
        "via citra_discovery_search. Pass the tool's `name` and the "
        "source-specific `args` payload. Returns {tool_name, source_id, "
        "result, elapsed_ms}. The MCP service forwards the caller's "
        "JWT verbatim so RLS / scope is preserved at the source. "
        "Routing is automatic by source kind: a STRUCTURED source is "
        "queried on the dept-MCP; a SEMANTIC (RAG) source is answered by "
        "the Citra platform reader (Milvus direct) — the dept-MCP serves "
        "no RAG. For a RAG source, `args` accepts {query, top_k, "
        "doc_types?, doc_path?}; pass `doc_path` (from a prior chunk's "
        "metadata.doc_path) to read an ENTIRE document instead of top-k passages."
    ),
    schema=_QUERY_SCHEMA,
    executor=_exec_discovery_query,
))
