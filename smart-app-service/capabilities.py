# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Builder ↔ BA capabilities contract.

The discovery skill loads this at the start of an interview so the builder
agent can only design flows the platform actually supports. Anything the BA
asks for that isn't here is recorded in ``AppSpec.requirements_unmet[]``
rather than silently agreed to.

The values are intentionally conservative — flip a feature to ``True`` only
when the supporting code path is shipped, tested, and on by default.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import Settings
from env_context import current_env
from models import (
    CapabilitiesResponse,
    CapabilityLimits,
    PlatformFeatures,
)
from relevance_filter import needs_scope_narrowing, rerank_candidates

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# Single source of truth — flip flags here as features ship.
_DEFAULT_FEATURES = PlatformFeatures(
    hitl_approvals=True,
    audit_trail=True,
    schedules=True,             # S3 — schedule.cron + schedule.interval
    polling=True,               # S3 — poll
    webhooks_inbound=True,      # S2
    webhooks_outbound=False,    # not built
    outbound_email=False,       # not built — register a notification_mcp instead
    outbound_sms=False,
    pdf_generation=False,
    sub_agent_routing=True,     # S4
    enterprise_sharing=True,    # already shipped
)

_DEFAULT_LIMITS = CapabilityLimits(
    max_run_seconds=120,
    max_tool_calls_per_run=25,
    max_input_bytes=5_000_000,
    max_attachments=10,
    rag_top_k_max=50,
    # Advertised floors/ceilings — these MATCH what's actually enforced
    # (trigger interval floor in update_ai_trigger + publish + runtime clamp;
    # poll fan-out concurrency env-capped, BA cannot raise).
    min_poll_interval_seconds=int(
        os.getenv("MIN_TRIGGER_INTERVAL_SECONDS",
                  os.getenv("MIN_CRON_INTERVAL_SECONDS", "300"))
    ),
    min_cron_granularity_seconds=int(
        os.getenv("MIN_TRIGGER_INTERVAL_SECONDS",
                  os.getenv("MIN_CRON_INTERVAL_SECONDS", "300"))
    ),
    max_concurrent_runs_per_trigger=max(
        1, min(8, int(os.getenv("TRIGGER_POLL_CONCURRENCY", "1")))
    ),
    max_runs_per_tenant_per_minute=60,
    webhook_payload_max_bytes=1_000_000,
    sub_agent_max_depth=2,
)


async def _fetch_tools_available(
    settings: Settings, auth_header: Optional[str]
) -> Tuple[List[Dict[str, Any]], bool]:
    """Best-effort live tool list from discovery-service.

    Returns ``(tools, reachable)`` where ``tools`` is a list of dicts
    ``{name, description, server, source_id, tags}``. A network failure
    returns ``([], False)`` — the discovery skill must surface this to
    the BA so they don't design around an empty toolbox by accident.

    Discovery-service applies org/dept/role visibility filtering
    server-side from the JWT claims; what we get back is already scoped
    to the BA's RBAC.
    """
    url = f"{settings.discovery_url_for(current_env()).rstrip('/')}/tools/available"
    headers = {"Accept": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        # A discovery OUTAGE is an error, not info. The False second value
        # signals "undetermined" to the caller (NOT "genuinely empty").
        logger.error(
            "discovery-service unreachable for /capabilities: %s", e, exc_info=True
        )
        return [], False
    if resp.status_code >= 400:
        # Discovery 4xx/5xx is an error. The False second value signals
        # "undetermined" to the caller (NOT "genuinely empty").
        logger.error(
            "discovery-service /tools/available returned %s", resp.status_code
        )
        return [], False
    try:
        data = resp.json()
    except ValueError:
        return [], True
    items = data.get("tools") if isinstance(data, dict) else data
    out: List[Dict[str, Any]] = []
    for t in items or []:
        if isinstance(t, dict):
            name = t.get("name")
            if not name:
                continue
            server = t.get("server") or t.get("mcp") or t.get("source_id") or ""
            out.append(
                {
                    "name": str(name),
                    "server": str(server),
                    "source_id": str(t.get("source_id") or ""),
                    "description": str(t.get("description") or ""),
                    "tags": list(t.get("tags") or []),
                }
            )
        elif isinstance(t, str):
            out.append({"name": t, "server": "", "source_id": "", "description": "", "tags": []})
    return out, True


def _format_tool_id(t: Dict[str, Any]) -> str:
    name = t.get("name") or ""
    server = t.get("server") or ""
    return f"{server}.{name}" if server and name else (name or server)


def _tool_rerank_text(t: Dict[str, Any]) -> str:
    name = t.get("name") or ""
    server = t.get("server") or ""
    desc = t.get("description") or ""
    tags = ", ".join(t.get("tags") or [])
    return f"{server}.{name}\n{desc}\nTags: {tags}".strip()


async def get_capabilities(
    settings: Settings,
    auth_header: Optional[str] = None,
    *,
    question: Optional[str] = None,
) -> CapabilitiesResponse:
    """Return platform features + RBAC-filtered tool list.

    When ``question`` is supplied (free-text BA goal), the RBAC-visible
    tool list is reranked by relevance and capped at 10. This keeps the
    builder system prompt tight even for ``org_admin`` / ``super_admin``
    callers whose RBAC list spans the whole org. When no question is
    given, the list is returned in discovery-service order (capped at
    10).
    """
    tools, reachable = await _fetch_tools_available(settings, auth_header)
    total = len(tools)
    needs_scope = needs_scope_narrowing(tools)
    ranked = await rerank_candidates(
        settings=settings,
        question=question,
        candidates=tools,
        text_fn=_tool_rerank_text,
        top_k=10,
    )
    return CapabilitiesResponse(
        platform_features=_DEFAULT_FEATURES,
        limits=_DEFAULT_LIMITS,
        tools_available=[_format_tool_id(t) for t in ranked],
        discovery_reachable=reachable,
        tools_total=total,
        needs_scope=needs_scope,
    )
