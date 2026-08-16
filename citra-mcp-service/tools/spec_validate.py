# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""citra_spec_validate — authoritative AppSpec/AgentSpec validation, at MCP level.

The SmartApp builder authors AppSpec/AgentSpec JSON, but the Pydantic models +
JSON Schemas that define a *valid* spec live in smart-app-service, and the build
pod cannot import them. The JSON Schema files seeded into the pod catch
structural errors, but JSON Schema alone can't see cross-references — a dangling
``navigate.page`` target, a duplicate ``is_row_click`` column, a sub-agent
``tools_v2`` that isn't a subset of the parent, a dashboard page with no
``agent_id`` narrator. Today those only surface at
``/publish``, after the whole app is built — the build->publish->422 loop.

This tool proxies to smart-app-service's ``POST /builder/validate``, which runs
the IDENTICAL two-layer check ``/publish`` runs (JSON Schema + Pydantic) WITHOUT
persisting anything. So the builder can validate locally before the publish
round-trip. A ``passed:true`` result means publish won't reject on spec-shape
grounds.

Auth: the caller's Bearer is forwarded verbatim. The builder's scoped token
(scope=smart-app-builder) carries the user identity claims, which smart-app's
auth middleware validates the same way ``/publish`` does.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from auth import CallerContext
from config import get_config
from .registry import ToolSpec, register_tool

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "properties": {
        "app_spec": {
            "type": "object",
            "description": "The full AppSpec object to validate (REQUIRED).",
        },
        "agent_spec": {
            "type": "object",
            "description": (
                "The full AgentSpec object, if the app has an agent (optional)."
            ),
        },
    },
    "required": ["app_spec"],
    "additionalProperties": False,
}


async def _exec(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.smart_app_service_url:
        raise RuntimeError(
            "SMART_APP_SERVICE_URL is not configured on citra-mcp-service — "
            "citra_spec_validate cannot reach the spec validator"
        )
    if not caller.auth_header:
        raise RuntimeError("missing Authorization header to forward")

    app_spec = args.get("app_spec")
    if not isinstance(app_spec, dict):
        raise ValueError("app_spec is required and must be an object")
    body: dict[str, Any] = {"app_spec": app_spec}
    agent_spec = args.get("agent_spec")
    if isinstance(agent_spec, dict):
        body["agent_spec"] = agent_spec

    url = cfg.smart_app_service_url.rstrip("/") + "/builder/validate"
    headers = {
        "Authorization": caller.auth_header,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise RuntimeError(f"spec validator unreachable: {e}") from e

    if r.status_code == 422:
        # Validation FAILED — this is an expected, actionable outcome, not a
        # tool error. Return it structured so the agent reads `errors` and fixes
        # the spec (exactly as it would on a /publish 422), but BEFORE publish.
        try:
            detail = r.json().get("detail")
        except Exception:  # noqa: BLE001
            detail = r.text
        return {"passed": False, "errors": detail}
    if r.status_code >= 400:
        raise RuntimeError(f"spec validator error {r.status_code}: {r.text}")
    return {"passed": True}


register_tool(ToolSpec(
    name="citra_spec_validate",
    description=(
        "Validate a drafted AppSpec (+ optional AgentSpec) against the "
        "authoritative JSON Schema AND Pydantic models — the IDENTICAL two-layer "
        "check /publish runs — WITHOUT publishing. Catches cross-reference errors "
        "JSON Schema alone can't: a dangling navigate.page target, a duplicate "
        "is_row_click column, a sub-agent tool-subset "
        "violation, a dashboard page missing its agent_id narrator. Args: "
        "{app_spec (object, required), agent_spec (object, optional)}. Returns "
        "{passed:true} or {passed:false, errors:<detail>}. Run it after composing "
        "the AppSpec and before /publish — a passed result means publish won't "
        "reject on spec-shape grounds."
    ),
    schema=_SCHEMA,
    executor=_exec,
    # Builder-only: action-chat sandboxes don't author SmartApp specs.
    allowed_scopes=("smart-app-builder",),
))
