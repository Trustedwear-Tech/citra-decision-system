# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tool registry — central dispatch table for MCP tools/call."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Tool executor signature: (args, caller) → tool result payload (dict).
# The caller is the JWT-derived ``CallerContext`` so tools can scope to
# the user. Caller is imported lazily inside each tool module to keep
# this file dependency-free.
ToolExecutor = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]   # JSON Schema for the parameters object
    executor: ToolExecutor
    # JWT scopes allowed to call this tool. ``None`` means "open to every
    # scope in CITRA_MCP_ACCEPTED_SCOPES" — the default for generic tools
    # (web search, discovery, embed, rerank). Tools whose backing data is
    # USER-scoped (vault, files, ocr, sql_duckdb) restrict
    # to ``("action-sandbox",)`` because the smart-app builder is for
    # enterprise app design, not for poking the BA's personal files.
    allowed_scopes: tuple[str, ...] | None = None


TOOL_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> None:
    if spec.name in TOOL_REGISTRY:
        logger.warning("tool %r is being re-registered (overwriting)", spec.name)
    TOOL_REGISTRY[spec.name] = spec
    logger.info(
        "registered tool: %s (scopes=%s)",
        spec.name,
        "any" if spec.allowed_scopes is None else ",".join(spec.allowed_scopes),
    )


def resolve_tool(name: str) -> ToolSpec | None:
    return TOOL_REGISTRY.get(name)


def is_tool_allowed(spec: ToolSpec, scope: str) -> bool:
    """Return True when ``scope`` may discover / invoke ``spec``."""
    if spec.allowed_scopes is None:
        return True
    return scope in spec.allowed_scopes


def list_tool_schemas(scope: str | None = None) -> list[dict[str, Any]]:
    """Return the array that goes into the MCP ``tools/list`` response.

    When ``scope`` is provided, tools whose ``allowed_scopes`` exclude
    it are filtered out. When ``scope`` is None, every tool is returned
    (used for the unauthenticated ``/health`` endpoint listing).
    """
    out: list[dict[str, Any]] = []
    for spec in TOOL_REGISTRY.values():
        if scope is not None and not is_tool_allowed(spec, scope):
            continue
        out.append({
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.schema,
        })
    return out
