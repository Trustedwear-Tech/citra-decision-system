# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
enterprise_mcp_client.py
========================
Layer 1 of the unified enterprise-MCP architecture.

Pure I/O + helper primitives for talking to:
  • the discovery-service (`/tools/available`) — to list dept-MCP tools
  • each dept-MCP server's `/query` endpoint — to fetch chunks

NO LLM calls live in this module. NO routing logic. The functions here are
called by services/enterprise_tools.py (Layer 2 — agentic tool-loop), which
in turn is used by chat, composer, presentation, and printable.

Design notes
------------
- Discovery results cached 5 min (TTL configurable via DISCOVERY_CACHE_TTL_SECONDS).
- Last-known-good fallback up to DISCOVERY_LKG_MAX_AGE_SECONDS so a discovery
  outage doesn't flatline enterprise search.
- Per-source timeout tiering (semantic / mongodb / structured), capped by
  MCP_CALL_HARD_CAP. Per-tool override via tool_def["query_timeout_seconds"]
  is honoured but always clamped.
- Per-tool circuit breaker via circuit_breaker.py so one slow MCP
  doesn't block the others on the same conversation turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from circuit_breaker import MCPUnavailableError, get_breaker

logger = logging.getLogger(__name__)


class MCPCallError(RuntimeError):
    """A dept-MCP ``/query`` call FAILED (timeout, HTTP error, transport error,
    or circuit-breaker open).

    Raised — never swallowed into ``[]`` — so the caller surfaces
    ``success=False`` with the real error to the LLM. A failed call is NOT the
    same as a successful call that returned zero rows: conflating the two lets
    the model treat "the source didn't answer" as "the source has no such data"
    and then fabricate the missing figures. Fail loud (see RULE #1).
    """


class DiscoveryUnavailableError(RuntimeError):
    """The discovery-service is UNREACHABLE and there is no usable last-known-good
    cache to serve.

    Raised — never swallowed into ``[]`` — so the caller does NOT silently
    proceed with *zero* enterprise tools (which makes the chat answer from
    general knowledge as if the org had no data). A discovery OUTAGE is not the
    same as a scope that genuinely has no sources: the former must surface so the
    user is told their data couldn't be reached; the latter returns ``[]``. The
    last-known-good cache (within ``DISCOVERY_LKG_MAX_AGE_SECONDS``) is still
    served on a transient blip — only a real, uncacheable outage raises.
    """


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def discovery_service_url() -> Optional[str]:
    """URL of the discovery-service for this deployment scope."""
    return os.getenv("DISCOVERY_SERVICE_URL")  # e.g. http://dept-discovery:9000


def service_api_key() -> str:
    """Shared service API key forwarded as `Authorization: Bearer …` to dept-MCPs."""
    return os.getenv("SERVICE_API_KEY", "")


# Tiered per-request timeouts for dept MCP /query calls.
#
# These sources are LLM-BACKED: the dept-MCP runs its OWN NL→SQL / NL→query
# planner (an LLM) WITH a self-correction retry loop on every call, so a single
# /query can legitimately take 60-90s. The old 30s default silently timed out
# real queries mid-plan — the BSPHCL "weather=5" hallucination began as a 30s
# ReadTimeout that got swallowed into an empty result. Give the planner room;
# the per-tool circuit breaker still trips after repeated genuine failures, and
# the failure is now surfaced loud (MCPCallError) rather than hidden. All values
# overridable via env so prod can tune per source without a redeploy.
_MCP_TIMEOUT_TIERS: Dict[str, float] = {
    "semantic": float(os.getenv("MCP_TIMEOUT_SEMANTIC", "30")),      # vector search + rerank
    "mongodb": float(os.getenv("MCP_TIMEOUT_MONGODB", "60")),        # NL→Mongo planner (LLM)
    "structured": float(os.getenv("MCP_TIMEOUT_STRUCTURED", "90")),  # NL→SQL planner + retries (LLM)
}
_MCP_TIMEOUT_DEFAULT = float(os.getenv("MCP_TIMEOUT_DEFAULT", "60"))


def mcp_call_hard_cap() -> float:
    # Ceiling for any single dept-MCP /query call. Raised from 60→120 so the
    # structured (NL→SQL + retry) tier isn't clamped back below its 90s budget.
    return float(os.getenv("MCP_CALL_HARD_CAP", "120"))


def resolve_mcp_timeout(tool_def: Optional[Dict[str, Any]]) -> float:
    """Pick a per-request timeout using source_type tier + optional override."""
    hard_cap = mcp_call_hard_cap()
    if not tool_def:
        return min(_MCP_TIMEOUT_DEFAULT, hard_cap)
    override = tool_def.get("query_timeout_seconds")
    if override:
        try:
            return min(float(override), hard_cap)
        except (TypeError, ValueError):
            pass
    source_type = (tool_def.get("source_type") or "").lower()
    tier_default = _MCP_TIMEOUT_TIERS.get(source_type, _MCP_TIMEOUT_DEFAULT)
    env_flat = os.getenv("MCP_CALL_TIMEOUT_SECONDS")
    if env_flat and source_type not in _MCP_TIMEOUT_TIERS:
        try:
            tier_default = float(env_flat)
        except ValueError:
            pass
    return min(tier_default, hard_cap)


def enterprise_max_chunks() -> int:
    """Hard cap on total enterprise chunks merged into a final answer."""
    return int(os.getenv("ENTERPRISE_MAX_CHUNKS", "20"))


# ---------------------------------------------------------------------------
# Discovery cache — avoids a round-trip on every query
# ---------------------------------------------------------------------------

_DISCOVERY_CACHE_TTL = float(os.getenv("DISCOVERY_CACHE_TTL_SECONDS", "300"))
_DISCOVERY_LKG_MAX_AGE = float(os.getenv("DISCOVERY_LKG_MAX_AGE_SECONDS", "3600"))

# Dict key: (org_id, dept_ids_tuple, roles_tuple)  →  (timestamp, tool_defs)
_discovery_cache: Dict[Tuple, Tuple[float, List[Dict[str, Any]]]] = {}


# ---------------------------------------------------------------------------
# Discovery — fetch MCP tool definitions from the discovery service
# ---------------------------------------------------------------------------

async def discover_tools(
    org_id: Optional[str],
    dept_ids: List[str],
    roles: List[str],
    jwt_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query the discovery service for available MCP tools.
    Returns a list of tool-definition dicts (name, description, query_endpoint, …).
    Returns [] if discovery service is not configured or unreachable with no
    last-known-good cache to fall back to.

    Auth: passes Authorization: Bearer {jwt_token} so the discovery service
    can apply server-side visibility filtering using the JWT claims.

    HA: on transient discovery-service outages we serve the last successful
    response up to DISCOVERY_LKG_MAX_AGE so a single discovery replica failure
    doesn't flatline enterprise search org-wide.
    """
    base_url = discovery_service_url()
    if not base_url:
        logger.debug("ℹ️ [ENT_MCP] DISCOVERY_SERVICE_URL not set — skipping discovery")
        return []

    cache_key = (
        org_id,
        tuple(sorted(dept_ids or [])),
        tuple(sorted(roles or [])),
    )
    now = time.monotonic()
    cached = _discovery_cache.get(cache_key)
    if cached and (now - cached[0]) < _DISCOVERY_CACHE_TTL:
        logger.debug(f"ℹ️ [ENT_MCP] Discovery cache hit (age={(now - cached[0]):.0f}s)")
        return cached[1]

    headers: Dict[str, str] = {}
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/tools/available", headers=headers)
            resp.raise_for_status()
            tools = resp.json()
            _discovery_cache[cache_key] = (now, tools)
            try:
                from metrics import note_discovery_refresh
                note_discovery_refresh()
            except Exception:
                pass
            logger.info(
                f"✅ [ENT_MCP] Discovery returned {len(tools)} tools "
                f"(org={org_id}, dept_ids={dept_ids}, roles={roles})"
            )
            return tools
    except Exception as exc:
        if cached:
            age = now - cached[0]
            if age < _DISCOVERY_LKG_MAX_AGE:
                # Transient blip — serve last-known-good (NOT a degraded result;
                # same tools the user had moments ago). This is the intended HA path.
                logger.warning(
                    f"⚠️ [ENT_MCP] Discovery unreachable ({exc}); "
                    f"serving last-known-good cache (age={age:.0f}s, "
                    f"lkg_max={_DISCOVERY_LKG_MAX_AGE:.0f}s)"
                )
                return cached[1]
            logger.error(
                f"❌ [ENT_MCP] Discovery unreachable ({exc}); "
                f"cache too stale to serve (age={age:.0f}s > {_DISCOVERY_LKG_MAX_AGE:.0f}s)"
            )
            raise DiscoveryUnavailableError(
                f"discovery-service unreachable and last-known-good cache too stale "
                f"(age={age:.0f}s > {_DISCOVERY_LKG_MAX_AGE:.0f}s): {exc}"
            ) from exc
        # No cache and discovery is down → fail loud. Returning [] here would
        # silently strip ALL enterprise tools from the turn (RULE #1 violation).
        logger.error(f"❌ [ENT_MCP] Discovery service unreachable and no cache: {exc}")
        raise DiscoveryUnavailableError(
            f"discovery-service unreachable and no last-known-good cache: {exc}"
        ) from exc


async def get_source_scope(
    source_id: str, jwt_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Server-authoritative scope for ONE source from the DISCOVERY registry —
    the replacement for the retired central ``dept_sources`` lookup used by
    ``/semantic/search``. Returns ``{source_id, source_type, org_id, dept_id,
    public_within_org, rag_collection}`` or ``None`` when the source isn't
    actively registered OR discovery can't be reached.

    Fail-CLOSED: an authz-scoping lookup that fails must NOT grant access, so a
    404/outage returns ``None`` (⇒ the caller denies with 403) — logged loud so
    an outage is visible, never silently treated as "no such source"."""
    base_url = discovery_service_url()
    if not base_url:
        logger.error("❌ [ENT_MCP] DISCOVERY_SERVICE_URL not set — cannot resolve source scope")
        return None
    headers: Dict[str, str] = {}
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/tools/source-scope/{source_id}", headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001 — deny on any failure (fail-closed), logged
        logger.error(f"❌ [ENT_MCP] source-scope lookup failed for {source_id!r}: {exc}")
        return None


async def register_source(payload: Dict[str, Any], api_key: str) -> None:
    """Register (or re-register) ONE source with the discovery registry — used by
    platform-native semantic sources (dept SOP libraries) that publish directly
    instead of via an MCP boot. Mirrors the MCP's ``POST /tools/register``
    (``X-API-Key`` header). Fails LOUD (raises) — a library must not appear
    created if it isn't discoverable."""
    base_url = discovery_service_url()
    if not base_url:
        raise RuntimeError("DISCOVERY_SERVICE_URL not set — cannot register source")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{base_url}/tools/register", json=payload, headers={"X-API-Key": api_key})
        resp.raise_for_status()


async def deregister_source(tool_id: str, api_key: str) -> None:
    """Soft-deregister ONE source (``active=False``) from the discovery registry.
    Best-effort + loud: the library record/vectors are removed regardless, so a
    deregister miss is logged but not raised (avoids orphaning the delete)."""
    base_url = discovery_service_url()
    if not base_url:
        logger.error("❌ [ENT_MCP] DISCOVERY_SERVICE_URL not set — cannot deregister %s", tool_id)
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                "DELETE", f"{base_url}/tools/{tool_id}", json={"api_key": api_key})
            if resp.status_code == 404:
                return  # already gone
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — the library delete proceeds regardless
        logger.error(f"❌ [ENT_MCP] deregister failed for {tool_id!r}: {exc}")


# ---------------------------------------------------------------------------
# Semantic discovery — query-ranked tool selection
# ---------------------------------------------------------------------------

async def search_tools(
    query: str,
    *,
    top_k: int = 10,
    tag: Optional[str] = None,
    data_type: Optional[str] = None,
    jwt_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query-ranked discovery via discovery-service ``/tools/search``.

    discovery-service embeds the query once and cosine-ranks every
    claim-visible tool against the stored description embeddings. Returns
    up to ``top_k`` ToolDefinition dicts, each augmented with a
    ``relevance_score`` float, sorted by score descending.

    Replaces the legacy ``discover_tools + client-side reranker-service``
    path: ranking now happens server-side, against a single shared
    embedding index that's refreshed automatically when a dept MCP
    re-registers with a changed description.

    Fails open: any error returns ``[]`` so the LLM call doesn't block on
    a discovery-service outage.
    """
    base_url = discovery_service_url()
    if not base_url:
        return []

    q = (query or "").strip()
    if not q:
        return []

    headers: Dict[str, str] = {}
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"

    params: Dict[str, Any] = {"q": q, "top_k": int(top_k)}
    if tag:
        params["tag"] = tag
    if data_type:
        params["data_type"] = data_type

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/tools/search",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            tools = resp.json() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"⚠️ [ENT_MCP] /tools/search unreachable ({exc}); "
            f"caller will fall back to legacy /tools/available path"
        )
        return []

    if not isinstance(tools, list):
        return []
    logger.info(
        f"🔎 [ENT_MCP] /tools/search q={q[:60]!r} → {len(tools)} ranked tools"
    )
    return tools


# ---------------------------------------------------------------------------
# Dept MCP executor — POST to a registered MCP server's /query endpoint
# ---------------------------------------------------------------------------

async def call_tool(
    query_endpoint: str,
    query: str,
    source_id: str,
    api_key: str,
    tool_def: Optional[Dict[str, Any]] = None,
    tool_id: Optional[str] = None,
    user_jwt: Optional[str] = None,
    max_results: int = 10,
    doc_types: Optional[List[str]] = None,
    classification_max: Optional[str] = None,
    dataset_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    POST to a department MCP server's /query REST endpoint.

    The MCP server is responsible for retrieving data from its own Milvus/store
    namespace using source_id as the scope key. Citra never needs to know about
    collection names, folder_ids, or tenancy details.

    Request:  POST {query_endpoint}
              body = {query: str, source_id: str, max_results: int, ...}
              auth = Bearer {api_key}, X-User-JWT: {user_jwt}

    Response: {results: [{text, score, source, metadata}]}

    Resilience: wrapped in a per-tool circuit breaker. A genuine HTTP 200 with
    an empty ``results`` array returns ``[]`` (that legitimately means "no
    matching rows"). But a FAILURE — timeout, HTTP error, transport error, or
    an open circuit — raises :class:`MCPCallError` rather than returning ``[]``,
    so the caller reports ``success=False`` to the LLM instead of an empty set
    that looks like zero rows (which the model would then fabricate around).
    """
    body: Dict[str, Any] = {
        "query": query,
        "source_id": source_id,
        "max_results": max(1, int(max_results or 10)),
    }
    # Phase B3 — catalogue-keyed dataset pinning. When the LLM (or the
    # orchestrator) names specific datasets, the dept-mcp planner skips
    # the catalogue vector search and runs straight against those rows.
    if dataset_ids:
        cleaned_ids = [str(d).strip() for d in dataset_ids if isinstance(d, str) and str(d).strip()]
        if cleaned_ids:
            body["dataset_ids"] = cleaned_ids
    if doc_types:
        cleaned_dt = [str(d).strip() for d in doc_types if isinstance(d, str) and str(d).strip()]
        if cleaned_dt:
            body["doc_types"] = cleaned_dt
    if classification_max and isinstance(classification_max, str):
        body["classification_max"] = classification_max.strip()
    timeout = resolve_mcp_timeout(tool_def)
    breaker_key = tool_id or query_endpoint
    source_type = (tool_def or {}).get("source_type", "unknown")

    async def _do_post() -> List[Dict[str, Any]]:
        try:
            from metrics import observe_mcp_call
            ctx = observe_mcp_call(breaker_key, source_type)
        except Exception:
            from contextlib import nullcontext
            ctx = nullcontext()
        with ctx:
            async with httpx.AsyncClient(timeout=timeout) as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                if user_jwt:
                    headers["X-User-JWT"] = user_jwt
                resp = await client.post(query_endpoint, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                for r in results:
                    r.setdefault("source_type_retrieval", "enterprise_mcp")
                    r.setdefault("source_id", source_id)
                    r.setdefault("remote_endpoint", query_endpoint)
                logger.info(
                    f"✅ [ENT_MCP] MCP call → {query_endpoint} (source_id={source_id}, "
                    f"timeout={timeout:.0f}s): {len(results)} results"
                )
                return results

    try:
        return await get_breaker().call(breaker_key, _do_post)
    except MCPUnavailableError as exc:
        try:
            from metrics import note_mcp_circuit_open
            note_mcp_circuit_open(breaker_key)
        except Exception:
            pass
        logger.warning(
            f"⛔ [ENT_MCP] Circuit open for {breaker_key} (source_id={source_id}) — "
            f"failing loud so the caller surfaces success=False"
        )
        raise MCPCallError(
            f"source '{source_id}' is temporarily unavailable "
            f"(circuit breaker open after repeated failures)"
        ) from exc
    except httpx.TimeoutException as exc:
        # str(ReadTimeout) is empty — without the explicit timeout value the
        # error reads as a blank failure (exactly what hid this bug). Spell it out.
        logger.warning(
            f"⏱️ [ENT_MCP] MCP call TIMED OUT after {timeout:.0f}s "
            f"({query_endpoint}, source_id={source_id})"
        )
        raise MCPCallError(
            f"query to '{source_id}' timed out after {timeout:.0f}s — the data "
            f"source did not return results"
        ) from exc
    except httpx.HTTPStatusError as exc:
        body_excerpt = (getattr(exc.response, "text", "") or "")[:300]
        status = getattr(exc.response, "status_code", "?")
        logger.warning(
            f"⚠️ [ENT_MCP] MCP call HTTP {status} "
            f"({query_endpoint}, source_id={source_id}): {body_excerpt}"
        )
        raise MCPCallError(
            f"query to '{source_id}' failed with HTTP {status}: {body_excerpt}"
        ) from exc
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}".strip().rstrip(":").strip()
        logger.warning(
            f"⚠️ [ENT_MCP] MCP call failed ({query_endpoint}, source_id={source_id}): {detail}"
        )
        raise MCPCallError(f"query to '{source_id}' failed — {detail}") from exc


