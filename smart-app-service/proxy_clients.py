# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""MCP / RAG proxy clients.

The runtime LLM (and the builder pod) never holds raw service API keys.
It calls ``/smart-app/internal/{mcp|rag}`` with an HMAC bearer; this module
forwards the call to the right upstream service.

Trust model
-----------
- The HMAC bearer's ``bindings`` claim authorises *which* concrete
  ``(source_id, tool_name)`` the runtime may call. The internal_routes layer
  enforces this BEFORE we get here.
- Downstream identity is the user JWT (``X-User-JWT`` on the proxy
  request). MCP /query uses a service API key for service-to-service auth
  AND ``X-User-JWT`` for visibility filtering.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from config import Settings
from discovery_cache import DiscoveryError, ResolvedSource, resolve_source
from http_client import get_http_client
from env_context import current_env

logger = logging.getLogger(__name__)


class ProxyError(Exception):
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# MCP / RAG (both call dept-MCP POST /query)
# ---------------------------------------------------------------------------


def _is_service_token(token: str) -> bool:
    """True if a JWT carries a service scope (no verifiable end-user identity).

    Service-scoped callers (e.g. ``citra-app-runtime``, which drives the
    runtime chat / panels) hold no end-user token a dept-MCP can verify.
    The payload is read WITHOUT signature verification — it only decides
    header shaping, never an auth decision.
    """
    try:
        import base64
        import json as _j

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = _j.loads(base64.urlsafe_b64decode(payload.encode()))
        return str(claims.get("scope", "")).startswith("citra-app-runtime")
    except Exception:  # noqa: BLE001
        return False


async def call_dept_mcp_query(
    *,
    settings: Settings,
    user_jwt: str,
    source_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Resolve source_id via discovery, POST {query_endpoint} with auth.

    ``body`` is forwarded verbatim with ``source_id`` injected so dept-MCP
    can route to the right backing collection. The dept-MCP contract is
    documented in dept-mcp-template-architecture.md.
    """
    if not settings.mcp_enabled:
        raise ProxyError(
            "mcp_disabled",
            "MCP proxy is not configured (set MCP_SERVICE_API_KEY + DISCOVERY_SERVICE_URL)",
            503,
        )

    try:
        resolved: ResolvedSource = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise ProxyError(e.code, str(e), e.status) from e

    # Build the dept-MCP request. We pass through the caller body with
    # source_id forced to the resolved value.
    forward_body = dict(body or {})
    forward_body["source_id"] = source_id

    api_key = resolved.api_key or settings.mcp_service_api_key
    if not api_key:
        raise ProxyError("mcp_no_api_key", "no api_key for source", 502)

    timeout = (
        resolved.query_timeout_seconds
        or settings.mcp_call_timeout_seconds
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Forward X-User-JWT only for a genuine end-user token. A service-scoped
    # caller (citra-app-runtime, driving the chat/panels) carries no user
    # identity the dept-MCP can verify — forwarding it makes the MCP reject
    # the call. The service API key above already authorises this
    # server-to-server request.
    if user_jwt and not _is_service_token(user_jwt):
        headers["X-User-JWT"] = user_jwt

    try:
        client = get_http_client()
        resp = await client.post(
            resolved.query_endpoint,
            json=forward_body,
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise ProxyError("mcp_unreachable", str(e), 502) from e

    if resp.status_code >= 500:
        raise ProxyError(
            "mcp_upstream_error",
            f"dept-MCP returned {resp.status_code}: {resp.text[:300]}",
            502,
        )
    if resp.status_code >= 400:
        raise ProxyError(
            "mcp_rejected",
            f"dept-MCP rejected request ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("mcp_bad_json", str(e), 502) from e


async def call_citra_semantic_search(
    *,
    settings: Settings,
    user_jwt: str,
    source_id: str,
    query: str,
    top_k: int = 25,
    dept_id: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    org_id: Optional[str] = None,
    on_behalf_of: Optional[str] = None,
    doc_path: Optional[str] = None,
    rag_collection: Optional[str] = None,
) -> Dict[str, Any]:
    """RAG short‑circuit: answer a ``kind=semantic`` source via the Citra‑Service
    platform reader (``POST /semantic/search``) — the dept‑MCP does NO RAG (pure
    disconnect). Returns ``{source_id, dept_id, count, chunks}``.

    Two auth modes:
    - **Interactive** — an end‑user JWT authorizes on the caller's dept membership
      (Citra‑Service verifies HS256 + enforces dept scope from ``dept_sources``).
    - **Service** — an agent/trigger run has no end‑user identity (or only a
      ``citra-app-runtime`` service token, which Citra‑Service can't authorize on).
      When ``org_id`` is given we mint a short‑lived org‑scoped semantic‑read token
      (``semantic_service``) so the read is authorized as a trusted platform
      service and the DATA is scoped to the source's own dept server‑side.
    Fail loud only when there is NEITHER a usable user JWT NOR an ``org_id`` to
    mint with — never a silent unauthorized read."""
    base = (getattr(settings, "citra_service_url", "") or "").rstrip("/")
    if not base:
        raise ProxyError("citra_url_missing", "CITRA_SERVICE_URL not configured", 503)
    effective_jwt = user_jwt if (user_jwt and not _is_service_token(user_jwt)) else None
    if effective_jwt is None and org_id:
        try:
            from citra_auth import mint_semantic_read_token
            effective_jwt = mint_semantic_read_token(
                org_id=org_id, on_behalf_of_user_id=on_behalf_of)
        except Exception as e:  # noqa: BLE001 — minting must never crash a read
            logger.warning("semantic service-token mint failed (org=%s): %s", org_id, e)
    if not effective_jwt:
        raise ProxyError(
            "semantic_no_identity",
            "semantic search needs an end-user JWT, or an org context to mint a "
            "service token",
            401,
        )
    payload: Dict[str, Any] = {"source_id": source_id, "query": query, "top_k": top_k}
    if dept_id:
        payload["dept_id"] = dept_id
    if filters:
        payload["filters"] = filters
    # doc_path ⇒ whole-document fetch (all sections of ONE doc, ordered) — Citra-
    # Service returns the complete document instead of top-k passages; query ignored.
    if doc_path:
        payload["doc_path"] = doc_path
    # The MCP-published Milvus collection — read it directly (no prefix derivation).
    if rag_collection:
        payload["rag_collection"] = rag_collection
    headers = {
        "Authorization": f"Bearer {effective_jwt}",
        "Content-Type": "application/json",
    }
    try:
        client = get_http_client()
        resp = await client.post(
            f"{base}/semantic/search",
            json=payload,
            headers=headers,
            timeout=settings.mcp_call_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise ProxyError("citra_unreachable", str(e), 502) from e

    if resp.status_code >= 500:
        raise ProxyError(
            "citra_upstream_error",
            f"Citra-Service semantic search returned {resp.status_code}: {resp.text[:300]}",
            502,
        )
    if resp.status_code >= 400:
        raise ProxyError(
            "semantic_rejected",
            f"semantic search rejected ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("citra_bad_json", str(e), 502) from e


async def call_dept_mcp_sample(
    *,
    settings: Settings,
    user_jwt: Optional[str],
    source_id: str,
    dataset_id: str,
    n: int = 3,
) -> Dict[str, Any]:
    """Pull sample rows from a dataset via the dept-MCP's purpose-built
    ``GET /datasets/{id}/sample`` (next to ``/query``). Unlike ``/query`` this
    needs NO NL query string, so an NL→SQL MCP no longer 422s on a query-less
    probe/sample. Returns the MCP's SampleResponse ``{id, rows, redacted,
    truncated}``."""
    try:
        resolved: ResolvedSource = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise ProxyError(e.code, str(e), e.status) from e

    api_key = resolved.api_key or settings.mcp_service_api_key
    if not api_key:
        raise ProxyError("mcp_no_api_key", "no api_key for source", 502)
    timeout = resolved.query_timeout_seconds or settings.mcp_call_timeout_seconds

    # The sample plane lives next to /query at /datasets/{id}/sample.
    qe = (resolved.query_endpoint or "").rstrip("/")
    base = qe[: -len("/query")] if qe.endswith("/query") else qe
    url = f"{base}/datasets/{dataset_id}/sample"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if user_jwt and not _is_service_token(user_jwt):
        headers["X-User-JWT"] = user_jwt

    try:
        client = get_http_client()
        resp = await client.get(
            url,
            params={"n": n, "source_id": source_id},
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise ProxyError("mcp_unreachable", str(e), 502) from e
    if resp.status_code >= 500:
        raise ProxyError(
            "mcp_upstream_error",
            f"dept-MCP returned {resp.status_code}: {resp.text[:300]}", 502,
        )
    if resp.status_code >= 400:
        raise ProxyError(
            "mcp_rejected",
            f"dept-MCP rejected sample ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("mcp_bad_json", str(e), 502) from e


async def call_dept_mcp_execute_action(
    *,
    settings: Settings,
    user_jwt: str,
    source_id: str,
    dataset_id: str,
    action_id: str,
    payload: Dict[str, Any],
    dry_run: bool = False,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a registered dept-MCP write action via ``POST /execute_action``.

    The dept-MCP exposes ``/execute_action`` next to ``/query``; it runs
    the catalogued ``write_actions`` entry (an UPDATE / INSERT) and audits
    every outcome. This is the write counterpart of ``call_dept_mcp_query``.
    """
    if not settings.mcp_enabled:
        raise ProxyError(
            "mcp_disabled",
            "MCP proxy is not configured (set MCP_SERVICE_API_KEY + DISCOVERY_SERVICE_URL)",
            503,
        )

    try:
        resolved: ResolvedSource = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise ProxyError(e.code, str(e), e.status) from e

    # discovery advertises the source's ``/query`` endpoint; the write
    # plane lives next to it at ``/execute_action``.
    qe = (resolved.query_endpoint or "").rstrip("/")
    base = qe[: -len("/query")] if qe.endswith("/query") else qe
    url = base + "/execute_action"

    api_key = resolved.api_key or settings.mcp_service_api_key
    if not api_key:
        raise ProxyError("mcp_no_api_key", "no api_key for source", 502)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if user_jwt and not _is_service_token(user_jwt):
        headers["X-User-JWT"] = user_jwt

    body: Dict[str, Any] = {
        "source_id": source_id,
        "dataset_id": dataset_id,
        "action_id": action_id,
        "payload": payload or {},
        "dry_run": dry_run,
    }
    if idempotency_key:
        body["idempotency_key"] = idempotency_key
    timeout = (
        resolved.query_timeout_seconds or settings.mcp_call_timeout_seconds
    )
    try:
        client = get_http_client()
        resp = await client.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise ProxyError("mcp_unreachable", str(e), 502) from e

    if resp.status_code >= 500:
        raise ProxyError(
            "mcp_upstream_error",
            f"dept-MCP returned {resp.status_code}: {resp.text[:300]}",
            502,
        )
    if resp.status_code >= 400:
        raise ProxyError(
            "mcp_rejected",
            f"dept-MCP rejected execute_action ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("mcp_bad_json", str(e), 502) from e


async def call_dept_mcp_read(
    *,
    settings: Settings,
    user_jwt: Optional[str],
    source_id: str,
    dataset_id: str,
    kind: str,
    query: Any,
    row_limit: int = 1,
) -> Dict[str, Any]:
    """Deterministic, structured read-back of a known record via the dept-MCP's
    ``POST /run_query`` (the STRUCTURED read plane — NOT the NL ``/query``
    planner). Used by the self-improving loop's Stage-4 outcome poller to answer
    "what is the current state of record X?" by key.

    ``kind`` MUST NOT be ``"semantic"`` — that falls back to the NL planner, and
    an outcome verdict must be deterministic and auditable, never gated behind an
    LLM. ``query`` is the per-kind structured filter the CALLER builds from the
    stored key (SELECT-by-key for sql, ``{entity, $filter}`` for odata, …); there
    is no NL interpretation. ``/run_query`` already enforces SELECT-only + the
    source PDP + audit, so this rides the existing read-plane governance.

    Returns the MCP's RunQueryResponse ``{rows, total, truncated, error, ...}``.
    """
    if kind == "semantic":
        raise ProxyError(
            "read_back_semantic_forbidden",
            "outcome read-back must be a structured read (kind != 'semantic'); "
            "the verdict cannot depend on the NL planner",
            400,
        )
    if not settings.mcp_enabled:
        raise ProxyError(
            "mcp_disabled",
            "MCP proxy is not configured (set MCP_SERVICE_API_KEY + DISCOVERY_SERVICE_URL)",
            503,
        )
    try:
        resolved: ResolvedSource = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise ProxyError(e.code, str(e), e.status) from e

    # discovery advertises the source's /query endpoint; the structured read
    # plane lives next to it at /run_query.
    qe = (resolved.query_endpoint or "").rstrip("/")
    base = qe[: -len("/query")] if qe.endswith("/query") else qe
    url = base + "/run_query"

    api_key = resolved.api_key or settings.mcp_service_api_key
    if not api_key:
        raise ProxyError("mcp_no_api_key", "no api_key for source", 502)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if user_jwt and not _is_service_token(user_jwt):
        headers["X-User-JWT"] = user_jwt

    body: Dict[str, Any] = {
        "source_id": source_id,
        "dataset_id": dataset_id,
        "kind": kind,
        "query": query,
        "row_limit": row_limit,
    }
    timeout = resolved.query_timeout_seconds or settings.mcp_call_timeout_seconds
    try:
        client = get_http_client()
        resp = await client.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise ProxyError("mcp_unreachable", str(e), 502) from e
    if resp.status_code >= 500:
        raise ProxyError(
            "mcp_upstream_error",
            f"dept-MCP returned {resp.status_code}: {resp.text[:300]}", 502,
        )
    if resp.status_code >= 400:
        raise ProxyError(
            "mcp_rejected",
            f"dept-MCP rejected run_query ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("mcp_bad_json", str(e), 502) from e


async def call_dept_mcp_resolve_media(
    *,
    settings: Settings,
    user_jwt: Optional[str],
    source_id: str,
    dataset_id: str,
    key_field: str,
    key_value: str,
    column: str,
) -> Dict[str, Any]:
    """Resolve a record's media column to a short-lived fetchable URL via the
    dept-MCP's ``POST /resolve_media``.

    The runtime passes ONLY the logical reference (dataset, key, column) — never a
    URL or bytes. The MCP reads the row by key under the caller's visibility, takes
    the column's NATIVE ref (``s3://…`` / bare key / ``http(s)://``), and mints a
    fresh presigned/passthrough URL. Source credentials + network reach live at the
    MCP boundary, so the runtime stays source-agnostic and the SoR never stores a
    baked (expiring) presigned URL.

    Returns the MCP's ResolveMediaResponse ``{url, mode, content_type, expires_at}``.
    """
    if not settings.mcp_enabled:
        raise ProxyError(
            "mcp_disabled",
            "MCP proxy is not configured (set MCP_SERVICE_API_KEY + DISCOVERY_SERVICE_URL)",
            503,
        )
    try:
        resolved: ResolvedSource = await resolve_source(
            discovery_url=settings.discovery_url_for(current_env()),
            user_jwt=user_jwt,
            source_id=source_id,
            cache_ttl_seconds=settings.discovery_cache_ttl_seconds,
        )
    except DiscoveryError as e:
        raise ProxyError(e.code, str(e), e.status) from e

    qe = (resolved.query_endpoint or "").rstrip("/")
    base = qe[: -len("/query")] if qe.endswith("/query") else qe
    url = base + "/resolve_media"

    api_key = resolved.api_key or settings.mcp_service_api_key
    if not api_key:
        raise ProxyError("mcp_no_api_key", "no api_key for source", 502)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if user_jwt and not _is_service_token(user_jwt):
        headers["X-User-JWT"] = user_jwt

    body: Dict[str, Any] = {
        "source_id": source_id,
        "dataset_id": dataset_id,
        "key_field": key_field,
        "key_value": key_value,
        "column": column,
    }
    timeout = resolved.query_timeout_seconds or settings.mcp_call_timeout_seconds
    try:
        client = get_http_client()
        resp = await client.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise ProxyError("mcp_unreachable", str(e), 502) from e
    if resp.status_code >= 500:
        raise ProxyError(
            "mcp_upstream_error",
            f"dept-MCP returned {resp.status_code}: {resp.text[:300]}", 502,
        )
    if resp.status_code >= 400:
        raise ProxyError(
            "mcp_rejected",
            f"dept-MCP rejected resolve_media ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("mcp_bad_json", str(e), 502) from e


async def list_discovery_sources(
    *, settings: Settings, user_jwt: str
) -> list[Dict[str, Any]]:
    """Return ``[{source_id, name, description, source_type, ...}, ...]``.

    Calls discovery-service ``GET /tools/available`` with the BA's JWT
    so org/dept visibility filtering applies. ``source_type`` lets the
    builder distinguish MCP-style action sources from RAG corpora
    (``semantic``).
    """
    if not settings.mcp_enabled:
        raise ProxyError("mcp_disabled", "MCP proxy not configured", 503)
    if not user_jwt:
        raise ProxyError("no_user_jwt", "user JWT required", 401)

    url = settings.discovery_url_for(current_env()).rstrip("/") + "/tools/available"
    headers = {"Authorization": f"Bearer {user_jwt}"}

    try:
        client = get_http_client()
        resp = await client.get(url, headers=headers, timeout=settings.mcp_call_timeout_seconds)
    except httpx.HTTPError as e:
        raise ProxyError("discovery_unreachable", str(e), 502) from e

    if resp.status_code >= 400:
        raise ProxyError(
            "discovery_rejected",
            f"discovery rejected ({resp.status_code}): {resp.text[:300]}",
            resp.status_code if resp.status_code < 500 else 502,
        )
    try:
        items = resp.json()
    except ValueError as e:
        raise ProxyError("discovery_bad_json", str(e), 502) from e
    if not isinstance(items, list):
        return []
    out: list[Dict[str, Any]] = []
    for s in items:
        if not isinstance(s, dict):
            continue
        out.append(
            {
                "source_id": s.get("source_id"),
                "name": s.get("name"),
                "description": s.get("description"),
                "source_type": s.get("source_type"),
                "tags": s.get("tags") or [],
                "data_types": s.get("data_types") or [],
                "taxonomy": s.get("taxonomy"),
            }
        )
    return out


async def run_code_exec(
    *,
    settings: Settings,
    user_jwt: str,
    script: str,
    output_filename: str,
    input_files: Optional[list] = None,
    app_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Proxy a single sandbox script run to Citra-Service.

    Returns the upstream payload verbatim::

        {
          success: bool,
          stdout: str,
          stderr: str,
          output_files: [
            {filename, download_url, size, content_type},
            ...
          ],
        }

    Sandbox-side failures (script error, validation reject, timeout)
    arrive as ``success=false`` with stderr populated — they are NOT
    raised. Only network / 5xx errors raise ``ProxyError`` so the
    runtime LLM can read stderr and iterate.
    """
    if not settings.code_exec_enabled:
        raise ProxyError("code_exec_disabled", "code-exec proxy not configured", 503)
    if not user_jwt:
        raise ProxyError("no_user_jwt", "user JWT required", 401)
    if not script or not script.strip():
        raise ProxyError("bad_args", "script is required", 400)
    if not output_filename or not output_filename.strip():
        raise ProxyError("bad_args", "output_filename is required", 400)

    url = (
        settings.code_exec_service_url.rstrip("/") + "/internal/code-exec/run"
    )
    headers = {"Authorization": f"Bearer {user_jwt}"}
    body: Dict[str, Any] = {
        "script": script,
        "output_filename": output_filename,
        "input_files": input_files or [],
    }
    if app_slug:
        body["app_slug"] = app_slug

    try:
        client = get_http_client()
        resp = await client.post(url, json=body, headers=headers, timeout=settings.code_exec_call_timeout_seconds)
    except httpx.HTTPError as e:
        raise ProxyError("code_exec_unreachable", str(e), 502) from e

    if resp.status_code >= 500:
        raise ProxyError(
            "code_exec_upstream_error",
            f"code-exec upstream error ({resp.status_code}): {resp.text[:300]}",
            502,
        )
    if resp.status_code >= 400:
        # 4xx is a contract violation by the dispatcher — surface as such
        # rather than letting the LLM see it as a recoverable script
        # error.
        raise ProxyError(
            "code_exec_rejected",
            f"code-exec rejected ({resp.status_code}): {resp.text[:300]}",
            resp.status_code,
        )

    try:
        return resp.json()
    except ValueError as e:
        raise ProxyError("code_exec_bad_json", str(e), 502) from e
