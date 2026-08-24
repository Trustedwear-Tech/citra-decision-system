# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Discovery-service + dept-MCP client for the in-sandbox agent.

Usage from a script the agent writes/executes::

    from citra_toolkit import discovery
    tools = discovery.list_tools()
    hits = discovery.query_tool("sales-mandi-bihar", "Q1 sales by district")
    bulk = discovery.query_many(["sales-mandi-bihar", "weather-imd"], "rainfall")

CLI (handy when the agent uses OpenClaw's ``exec`` tool)::

    python -m citra_toolkit.discovery list
    python -m citra_toolkit.discovery query <tool_name> "<question>" [--top 10]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

from ._proxy import proxy_url
from .client import http_client, scoped_token

# ----------------------------- catalog cache --------------------------------
_CACHE_TTL = float(os.getenv("CITRA_DISCOVERY_CACHE_TTL_SECONDS", "300"))
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# Per-call timeout for a dept-MCP /query. These MCPs run their OWN LLM-backed
# NL→SQL / NL→query planner WITH a self-correction retry loop, so a single
# query legitimately takes 60-90s; the old 30s default cut real queries off
# mid-plan. Env-overridable; mirrors the Citra-Service MCP timeout bump
# (MCP_TIMEOUT_STRUCTURED). A failed/slow call raises here (query_tool) or is
# error-marked (query_many) — visible to the agent, never silently empty.
_MCP_QUERY_TIMEOUT = float(os.getenv("CITRA_MCP_QUERY_TIMEOUT_SECONDS", "90"))


def _discovery_url() -> str:
    url = os.getenv("DISCOVERY_SERVICE_URL", "").rstrip("/")
    if not url:
        raise RuntimeError(
            "DISCOVERY_SERVICE_URL is not set inside the sandbox; "
            "no enterprise tools are reachable from this turn"
        )
    return url


# ------------------------------- list ---------------------------------------
def list_tools(*, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return the org-visible tool catalog.

    Each entry contains at least ``name``, ``description``, ``query_endpoint``,
    ``source_id`` and (optionally) ``tags`` / ``data_types``. The
    discovery-service applies org/dept/role visibility filtering server-side,
    so the agent never sees tools it isn't allowed to call.
    """
    cache_key = "default"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and not force_refresh and (now - cached[0]) < _CACHE_TTL:
        return list(cached[1])

    base = _discovery_url()
    with http_client(timeout=10.0) as c:
        r = c.get(f"{base}/tools/available")
        r.raise_for_status()
        tools = r.json()
    if not isinstance(tools, list):
        raise RuntimeError(f"discovery-service returned non-list payload: {type(tools).__name__}")
    _cache[cache_key] = (now, tools)
    return list(tools)


# ------------------------------- search -------------------------------------
def search(query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
    """Rank dept-MCPs by query relevance.

    Embeds the query + every MCP description (cached server-side),
    cosine-ranks, optionally cross-encoder reranks the top candidates,
    and returns the most relevant tools for ``query``.

    Each item: ``{tool_id, name, description, tags, data_types, score,
    query_endpoint, source_id}``. Pair with ``query_tool(name, query)``
    or ``query_many([names], query)`` to invoke the chosen MCP(s).

    Use this whenever the user's question might be answered by an
    enterprise system (HR / Finance / CRM / Ops / regulator data /
    governed dept tooling). It's the search-by-intent surface; for
    enumeration use ``list_tools()``.
    """
    if not query or not query.strip():
        return []
    body = {"query": query, "top_k": int(top_k)}
    with http_client(timeout=20.0) as c:
        r = c.post(proxy_url("discovery/search"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    return list(payload.get("items") or [])


def find_tool(name: str) -> dict[str, Any]:
    for t in list_tools():
        if t.get("name") == name:
            return t
    raise KeyError(f"tool '{name}' not found in this org's catalog")


# ------------------------------- query --------------------------------------
def query_tool(
    name: str,
    query: str,
    *,
    max_results: int = 10,
    timeout: float = _MCP_QUERY_TIMEOUT,
) -> dict[str, Any]:
    """POST ``{query, source_id, max_results}`` to a single tool's endpoint.

    Returns the dept MCP's raw payload, normally ``{"results": [...]}``.
    """
    tool = find_tool(name)
    endpoint = tool.get("query_endpoint")
    if not endpoint:
        raise RuntimeError(f"tool '{name}' has no query_endpoint")
    body = {
        "query": query,
        "source_id": tool.get("source_id"),
        "max_results": int(max_results),
    }
    with http_client(timeout=timeout) as c:
        r = c.post(endpoint, json=body)
        r.raise_for_status()
        payload = r.json() or {}
    # Mirror vault.search: surface chunk text under both ``text`` and
    # ``content`` so LLM-generated Python that reaches for either key
    # works. MCP results vary in shape (some emit ``text``, some
    # ``content``, some ``snippet``); we copy whichever one populated
    # to the others so all three keys point at the same string.
    results = payload.get("results")
    if isinstance(results, list):
        for r_item in results:
            if not isinstance(r_item, dict):
                continue
            t = (
                r_item.get("text")
                or r_item.get("content")
                or r_item.get("snippet")
                or ""
            )
            if t:
                r_item.setdefault("text", t)
                r_item.setdefault("content", t)
    return payload


def query_many(
    names: list[str],
    query: str,
    *,
    max_results: int = 10,
    timeout: float = _MCP_QUERY_TIMEOUT,
) -> dict[str, dict[str, Any]]:
    """Sequentially query several tools, swallow per-tool errors.

    The agent typically wants partial results rather than a hard fail when
    one MCP is down. Errors are returned as ``{"error": "..."}`` per tool.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        try:
            out[name] = query_tool(name, query, max_results=max_results, timeout=timeout)
        except (httpx.HTTPError, RuntimeError, KeyError) as e:
            out[name] = {"error": str(e), "results": []}
    return out


# --------------------------------- CLI --------------------------------------
def _cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="citra_toolkit.discovery")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="print the visible tool catalog as JSON")

    pq = sub.add_parser("query", help="query a single tool")
    pq.add_argument("name")
    pq.add_argument("query")
    pq.add_argument("--top", type=int, default=10)

    pm = sub.add_parser("query-many", help="query several tools (csv names)")
    pm.add_argument("names", help="comma-separated tool names")
    pm.add_argument("query")
    pm.add_argument("--top", type=int, default=10)

    args = p.parse_args(argv)
    # Touch token early so failures are obvious.
    _ = scoped_token()

    if args.cmd == "list":
        print(json.dumps(list_tools(), indent=2))
        return 0
    if args.cmd == "query":
        print(json.dumps(query_tool(args.name, args.query, max_results=args.top), indent=2))
        return 0
    if args.cmd == "query-many":
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        print(json.dumps(query_many(names, args.query, max_results=args.top), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
