# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Shared helper for tools that forward to the user-data backend.

The MCP service is intentionally a thin protocol shell — the heavy
lifting (S3 scope, Milvus filters, OCR pipeline, LLM tier, DuckDB
sandbox) is owned by the single user-data backend. Today that backend
is action-chat-service's /actionchat/internal/* routes; tomorrow it
could be a dedicated citra-user-data-service.

Scoping happens by ``user_id`` (extracted from the JWT downstream).
The JWT ``scope`` claim is auth-only — it gates "is this a legitimate
sandbox caller?" via the allowlist in ``config.accepted_scopes``, it
does NOT route requests. A file uploaded by user U through any
sandbox is visible to any other sandbox running as user U.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from auth import CallerContext
from config import get_config

logger = logging.getLogger(__name__)


async def call_internal(
    path: str,
    body: dict[str, Any],
    caller: CallerContext,
    *,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Forward a JSON POST to the user-data backend.

    ``path`` is appended to ``CITRA_USER_DATA_BACKEND_URL``. The caller's
    Bearer is forwarded verbatim so the backend enforces user_id-based
    scoping using its own JWT validation.

    Default timeout is 25s — deliberately **shorter** than OpenClaw's MCP
    client request timeout. A slow backend (e.g. a cold Milvus vault
    search) must surface as a fast, clean MCP tool-error result the agent
    can react to — NOT a 60s hang that races OpenClaw's own timeout and
    leaves the turn wedged. If the backend is legitimately slow, fix the
    backend; don't widen this.
    """
    cfg = get_config()
    if not caller.auth_header:
        raise RuntimeError("missing Authorization header to forward")
    if not cfg.user_data_backend_url:
        raise RuntimeError(
            "CITRA_USER_DATA_BACKEND_URL is not configured on citra-mcp-service"
        )

    url = f"{cfg.user_data_backend_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": caller.auth_header,
        "Content-Type": "application/json",
    }
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            r = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as e:
        # Surface a clear, fast tool-error the agent can act on — e.g.
        # "vault search timed out, falling back to web". Far better than a
        # silent 60s hang.
        raise RuntimeError(
            f"backend timed out after {timeout_seconds:.0f}s "
            f"({path}) — the user-data backend is slow or unreachable"
        ) from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"upstream transport failed: {e}") from e

    elapsed_ms = int((time.time() - started) * 1000)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:  # noqa: BLE001
            detail = {"detail": r.text}
        raise RuntimeError(
            f"upstream {r.status_code}: "
            f"{detail.get('detail') if isinstance(detail, dict) else detail}"
        )

    try:
        data = r.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"upstream returned non-JSON body: {e}") from e
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("upstream_elapsed_ms", elapsed_ms)
    return data


async def call_smart_app_internal(
    path: str,
    body: dict[str, Any],
    caller: CallerContext,
    *,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Forward a JSON POST to smart-app-service's internal API.

    ``path`` is appended to ``SMART_APP_INTERNAL_URL`` (e.g. ``"ocr"`` ->
    ``http://smart-app-service:9100/smart-app/internal/ocr``).

    Authentication differs from :func:`call_internal` in a way that matters:
    that API rejects the caller's MCP JWT outright. It requires an INTERNAL
    BEARER signed with SMART_APP_INTERNAL_SIGNING_KEY whose ``tools`` claim
    names the specific route being invoked (``vision_ocr`` for /ocr) — a cost
    gate, not just an identity check.

    This service holds no signing key and mints nothing. It relays the bearer
    the caller sent as ``X-Smart-App-Internal``; the builder pod already has
    one and passes it through its MCP client headers. A caller that sends no
    such header cannot use this path, and is told exactly that rather than
    getting a 401 from a service it has never heard of.
    """
    cfg = get_config()
    if not cfg.smart_app_internal_url:
        raise RuntimeError(
            "SMART_APP_INTERNAL_URL is not configured on citra-mcp-service — "
            "set it to smart-app-service's internal API root, e.g. "
            "http://smart-app-service:9100/smart-app/internal"
        )
    bearer = (caller.internal_bearer or "").strip()
    if not bearer:
        raise RuntimeError(
            "this tool needs smart-app-service's internal API, but the caller "
            "sent no X-Smart-App-Internal header. The builder pod relays its "
            "SMART_APP_INTERNAL_SECRET there (see the sandbox openclaw config "
            "overlay); citra-mcp-service cannot mint one."
        )
    if not bearer.lower().startswith("bearer "):
        bearer = f"Bearer {bearer}"

    url = f"{cfg.smart_app_internal_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Authorization": bearer, "Content-Type": "application/json"}
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            r = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"smart-app-service timed out after {timeout_seconds:.0f}s ({path})"
        ) from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"smart-app-service transport failed: {e}") from e

    elapsed_ms = int((time.time() - started) * 1000)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:  # noqa: BLE001
            detail = {"detail": r.text}
        raise RuntimeError(
            f"smart-app-service {r.status_code}: "
            f"{detail.get('detail') if isinstance(detail, dict) else detail}"
        )

    try:
        data = r.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"smart-app-service returned non-JSON body: {e}") from e
    if not isinstance(data, dict):
        data = {"result": data}
    data.setdefault("upstream_elapsed_ms", elapsed_ms)
    return data
