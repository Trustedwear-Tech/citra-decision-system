# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""DEPRECATED — NOT REGISTERED, PENDING DELETION.
tools/__init__.py deliberately does not import this module, so nothing
here reaches the registry — see the reason recorded at that import site,
and the "DEAD TOOL SURFACE" block in tools/registry.py. The
scope="action-sandbox" gate below is moot twice over: that scope has had
no minter since action-chat-service was removed from this repo.

citra_sql_duckdb — run a SELECT over a sandbox-supplied file.

Wraps action-chat-service /actionchat/internal/duckdb, which loads the
file into an in-memory DuckDB table named 'data' and runs the query.
The agent supplies the file bytes inline (base64) — this tool is for
ad-hoc analytics over an artifact the agent fetched / uploaded /
generated, not for warehouse queries over shared shards.
"""
from __future__ import annotations

from typing import Any

from auth import CallerContext
from ._forward import call_internal
from .registry import ToolSpec, register_tool


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": (
                "Single SELECT/WITH statement against the in-memory table "
                "named 'data'. e.g. SELECT col, count(*) FROM data GROUP BY col"
            ),
        },
        "content_b64": {
            "type": "string",
            "description": "Base64-encoded file bytes (xlsx / csv / parquet / json).",
        },
        "format": {
            "type": "string",
            "enum": ["xlsx", "csv", "parquet", "json"],
            "description": (
                "File format. If omitted and filename is given, inferred "
                "from the extension."
            ),
        },
        "filename": {
            "type": "string",
            "description": "Original filename (used for format inference and audit).",
        },
        "sheet": {
            "type": "string",
            "description": "Sheet name (xlsx only). Defaults to first sheet.",
        },
        "max_rows": {
            "type": "integer",
            "description": "Cap on returned rows (1-50000, default 1000).",
            "minimum": 1,
            "maximum": 50000,
        },
    },
    "required": ["sql", "content_b64"],
    "additionalProperties": False,
}


async def _exec(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    sql = (args.get("sql") or "").strip()
    if not sql:
        raise ValueError("sql is required")
    if not sql.lower().lstrip().startswith(("select", "with")):
        raise ValueError("only SELECT / WITH queries are allowed")
    content_b64 = args.get("content_b64") or ""
    if not isinstance(content_b64, str) or not content_b64:
        raise ValueError("content_b64 is required")

    body: dict[str, Any] = {"sql": sql, "content_b64": content_b64}
    if args.get("format"):
        body["format"] = str(args["format"])
    if args.get("filename"):
        body["filename"] = str(args["filename"])
    if args.get("sheet"):
        body["sheet"] = str(args["sheet"])
    if args.get("max_rows"):
        body["max_rows"] = int(args["max_rows"])
    return await call_internal(
        "actionchat/internal/duckdb", body, caller, timeout_seconds=120.0
    )


register_tool(ToolSpec(
    name="citra_sql_duckdb",
    description=(
        "Run an in-memory DuckDB SELECT over a sandbox-supplied file "
        "(xlsx / csv / parquet / json). The file bytes are base64 in the "
        "call — the table is named 'data'. Returns {columns, rows, "
        "truncated, elapsed_ms}. Read-only."
    ),
    schema=_SCHEMA,
    executor=_exec,
    # Operates on the agent's session file bytes via the action-chat
    # /internal/duckdb route — session-scoped, not appropriate for
    # smart-app builder.
    # DEPRECATED — unreachable; see this module's docstring.
    allowed_scopes=("action-sandbox",),
))
