# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""User-files tools — list / get_url / get_bytes / put.

All four forward to action-chat-service's S3-backed proxy. The MCP layer
never touches buckets directly; the upstream enforces per-user prefix
scope, user-uploads vs outputs separation, and personal-data toggles.
"""
from __future__ import annotations

from typing import Any

from auth import CallerContext
from ._forward import call_internal
from .registry import ToolSpec, register_tool


# ---------------------------------------------------------------- files_list
_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prefix": {
            "type": "string",
            "description": "Relative-to-user prefix (e.g. 'personal/<folder_id>/documents/').",
        },
        "limit": {
            "type": "integer",
            "description": "Page size (1-500, default 100).",
            "minimum": 1,
            "maximum": 500,
        },
        "include_derived": {
            "type": "boolean",
            "description": (
                "If true, return everything under the user prefix "
                "(includes derived assets and agent outputs). Default false."
            ),
        },
    },
    "additionalProperties": False,
}


async def _exec_list(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if args.get("prefix") is not None:
        body["prefix"] = str(args["prefix"])
    if args.get("limit"):
        body["limit"] = int(args["limit"])
    if args.get("include_derived"):
        body["include_derived"] = True
    return await call_internal("actionchat/internal/files/list", body, caller)


register_tool(ToolSpec(
    name="citra_files_list",
    description=(
        "List the caller's uploaded files. Returns {items: [{key, size, "
        "last_modified, etag}], prefix, bucket, truncated}. By default "
        "only real user uploads are returned — pass include_derived to "
        "see agent-generated outputs and cached assets too."
    ),
    schema=_LIST_SCHEMA,
    executor=_exec_list,
    allowed_scopes=("action-sandbox",),
))


# ----------------------------------------------------------- files_get_url
_GET_URL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Full S3 key from citra_files_list.",
        },
        "expiry_seconds": {
            "type": "integer",
            "description": "Presigned URL TTL (60-3600s, default 1800).",
            "minimum": 60,
            "maximum": 3600,
        },
    },
    "required": ["key"],
    "additionalProperties": False,
}


async def _exec_get_url(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    key = (args.get("key") or "").strip()
    if not key:
        raise ValueError("key is required")
    body: dict[str, Any] = {"key": key, "inline": False}
    if args.get("expiry_seconds"):
        body["expiry_seconds"] = int(args["expiry_seconds"])
    return await call_internal("actionchat/internal/files/get", body, caller)


register_tool(ToolSpec(
    name="citra_files_get_url",
    description=(
        "Get a presigned download URL for a file in the caller's vault. "
        "Returns {key, size, content_type, presigned_url, expires_in}. "
        "Use for large files or when you need to embed the URL in output."
    ),
    schema=_GET_URL_SCHEMA,
    executor=_exec_get_url,
    allowed_scopes=("action-sandbox",),
))


# --------------------------------------------------------- files_get_bytes
_GET_BYTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "key": {
            "type": "string",
            "description": "Full S3 key from citra_files_list.",
        },
    },
    "required": ["key"],
    "additionalProperties": False,
}


async def _exec_get_bytes(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    key = (args.get("key") or "").strip()
    if not key:
        raise ValueError("key is required")
    return await call_internal(
        "actionchat/internal/files/get",
        {"key": key, "inline": True},
        caller,
        # Larger timeout to cover base64 payloads up to a few MB
        timeout_seconds=90.0,
    )


register_tool(ToolSpec(
    name="citra_files_get_bytes",
    description=(
        "Inline-download a file as base64. Returns {key, size, "
        "content_type, content_b64}. Use for small files (<8MB) you need "
        "to parse directly; for larger files use citra_files_get_url."
    ),
    schema=_GET_BYTES_SCHEMA,
    executor=_exec_get_bytes,
    allowed_scopes=("action-sandbox",),
))


# ----------------------------------------------------------------- files_put
_PUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "description": "Basename (no slashes, no leading dot).",
        },
        "content_b64": {
            "type": "string",
            "description": "Base64-encoded file bytes.",
        },
        "content_type": {
            "type": "string",
            "description": "MIME type; defaults to application/octet-stream.",
        },
        "category": {
            "type": "string",
            "description": "Output subfolder (default 'outputs'). 'reports', 'charts', etc.",
        },
    },
    "required": ["filename", "content_b64"],
    "additionalProperties": False,
}


async def _exec_put(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    filename = (args.get("filename") or "").strip()
    content_b64 = args.get("content_b64") or ""
    if not filename:
        raise ValueError("filename is required")
    if not isinstance(content_b64, str) or not content_b64:
        raise ValueError("content_b64 is required")
    body: dict[str, Any] = {"filename": filename, "content_b64": content_b64}
    if args.get("content_type"):
        body["content_type"] = str(args["content_type"])
    if args.get("category"):
        body["category"] = str(args["category"])
    return await call_internal(
        "actionchat/internal/files/put", body, caller, timeout_seconds=90.0
    )


register_tool(ToolSpec(
    name="citra_files_put",
    allowed_scopes=("action-sandbox",),
    description=(
        "Write an agent-generated artifact to the caller's outputs bucket. "
        "Server picks the path: <env>/users/<uid>/actionchat/outputs/"
        "<category>/<filename>. Returns {key, size, content_type, "
        "presigned_url}. Cannot write to the user-uploads bucket."
    ),
    schema=_PUT_SCHEMA,
    executor=_exec_put,
))
