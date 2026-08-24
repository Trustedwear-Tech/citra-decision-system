# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""MCP HTTP endpoint — JSON-RPC 2.0 over POST /mcp.

Implements the four methods OpenClaw needs:

    initialize                  → server info + protocol version
    notifications/initialized   → no response (204)
    notifications/cancelled     → no response (204)
    tools/list                  → schemas from the central registry
    tools/call                  → dispatch by name, wrap result

The tool surface is in ``tools/*.py``; this file is the protocol shell.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from auth import require_caller
from tools import list_tool_schemas, resolve_tool
from tools.registry import is_tool_allowed

logger = logging.getLogger(__name__)
router = APIRouter()

# Both versions OpenClaw's bundled MCP loopback supports.
_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
_SERVER_INFO = {
    "name": "citra-mcp-service",
    "version": "1.0.0",
}


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = str(payload)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


@router.post("/mcp")
async def handle_mcp(request: Request):
    caller = require_caller(request)

    try:
        message = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON-RPC body")
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail="JSON-RPC envelope must be an object")

    method = message.get("method") or ""
    req_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    # Notifications carry no `id` and expect no response body.
    if method.startswith("notifications/"):
        return Response(status_code=204)

    if method == "initialize":
        client_version = str(params.get("protocolVersion") or "")
        chosen = client_version if client_version in _PROTOCOL_VERSIONS else _PROTOCOL_VERSIONS[0]
        return JSONResponse(_rpc_result(req_id, {
            "protocolVersion": chosen,
            "capabilities": {"tools": {}},
            "serverInfo": _SERVER_INFO,
        }))

    if method == "tools/list":
        # Scope-filter: a smart-app-builder caller never sees the user-
        # personal tool surface (vault / files / ocr / sql_duckdb); an
        # action-sandbox caller sees the full set. The
        # filter happens once here so OpenClaw's tool catalogue is
        # already scoped — the LLM can't even attempt to call a tool
        # it isn't allowed to use.
        return JSONResponse(_rpc_result(req_id, {
            "tools": list_tool_schemas(scope=caller.scope),
        }))

    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        tool_args = params.get("arguments") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        spec = resolve_tool(tool_name)
        if spec is None:
            return JSONResponse(_rpc_result(req_id, _tool_text_result(
                f"Tool not available: {tool_name!r}", is_error=True,
            )))
        # Defense in depth: even if a client somehow knows the tool name
        # without it appearing in their tools/list, the scope check
        # blocks the call.
        if not is_tool_allowed(spec, caller.scope):
            logger.warning(
                "[mcp] scope=%s blocked from calling tool=%s "
                "(allowed_scopes=%s)",
                caller.scope, tool_name, spec.allowed_scopes,
            )
            return JSONResponse(_rpc_result(req_id, _tool_text_result(
                f"Tool {tool_name!r} is not available for scope "
                f"{caller.scope!r}",
                is_error=True,
            )))

        try:
            result = await spec.executor(tool_args, caller)
            return JSONResponse(_rpc_result(req_id, _tool_text_result(result)))
        except ValueError as e:
            return JSONResponse(_rpc_result(req_id, _tool_text_result(
                str(e), is_error=True,
            )))
        except RuntimeError as e:
            logger.warning("[mcp] tool=%s runtime error: %s", tool_name, e)
            return JSONResponse(_rpc_result(req_id, _tool_text_result(
                str(e), is_error=True,
            )))
        except Exception as e:  # noqa: BLE001
            logger.exception("[mcp] tool=%s execution crashed", tool_name)
            return JSONResponse(_rpc_result(req_id, _tool_text_result(
                f"{tool_name} crashed: {type(e).__name__}: {e}",
                is_error=True,
            )))

    return JSONResponse(_rpc_error(req_id, -32601, f"Method not found: {method}"))


@router.get("/health")
async def health() -> dict[str, Any]:
    """Simple liveness probe — no auth required."""
    from tools import TOOL_REGISTRY
    return {
        "status": "ok",
        "service": "citra-mcp-service",
        "tool_count": len(TOOL_REGISTRY),
        "tools": sorted(TOOL_REGISTRY.keys()),
    }
