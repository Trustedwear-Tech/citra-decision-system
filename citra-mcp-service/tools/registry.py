# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

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


# ---------------------------------------------------------------------------
# DEAD TOOL SURFACE — unreachable, pending deletion
# ---------------------------------------------------------------------------
# Eight tools cannot be called by anybody. They fall into two groups.
#
# (1) REGISTERED BUT SCOPE-GATED TO A SCOPE NOBODY MINTS —
#     citra_files_list / citra_files_get_url / citra_files_get_bytes /
#     citra_files_put / citra_vault_search / citra_ocr
#
#     Gated to ``allowed_scopes=("action-sandbox",)``. That scope was minted
#     only by action-chat-service, which has been REMOVED from this repo. The
#     one MCP caller left is the smart-app builder pod, whose token carries
#     ``scope="smart-app-builder"``, so ``list_tool_schemas`` hides them from
#     tools/list and ``is_tool_allowed`` refuses them on tools/call. They
#     cannot fail, because they cannot be reached. They would also not work
#     if reached: they forward to ``/actionchat/internal/*`` on
#     CITRA_USER_DATA_BACKEND_URL — action-chat-service's old port — which
#     nothing serves any more.
#
# (2) NOT IMPORTED AT ALL — citra_sql_duckdb, citra_image_generate
#
#     Never reach this registry: tools/__init__.py deliberately does not
#     import tools/sql.py or tools/image.py, for reasons that predate the
#     action-chat removal and are documented at those import sites (duckdb
#     ships in the sandbox image; inline base64 image blobs corrupted tool
#     calls). Scope is irrelevant to them.
#
# ALL EIGHT ARE KEPT ON PURPOSE, NOT OVERLOOKED: they are the reference
# implementation for a second sandbox consumer, should one appear (the
# sandbox base image is deliberately consumer-neutral for the same reason).
# DELETE THEM if none materialises — tools/files.py, vault.py, ocr.py,
# sql.py, image.py, and tools/_forward.py once citra_visual_review no longer
# uses it.
#
# Do NOT "fix" group (1) by widening its scope to smart-app-builder. The
# builder designs enterprise apps; it has no business reading the BA's
# personal files, and its own AGENTS.md tells the agent these tools are not
# part of its surface.


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]   # JSON Schema for the parameters object
    executor: ToolExecutor
    # JWT scopes allowed to call this tool. ``None`` means "open to every
    # scope in CITRA_MCP_ACCEPTED_SCOPES" — the default for generic tools
    # (web search, discovery, embed, rerank). Tools whose backing data is
    # USER-scoped (vault, files, ocr) restrict to ``("action-sandbox",)``
    # because the smart-app builder is for enterprise app design, not for
    # poking the BA's personal files. That scope is now dead — see the
    # DEAD TOOL SURFACE block above.
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
