# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra_vault_search — semantic search over the caller's user vault.

Forwards to action-chat-service ``/actionchat/internal/vault/search``,
which embeds the query, runs Milvus over **Citra-Service's main
collection** (the per-chunk index Citra built when the user uploaded
each file), joins the chunk prose from Mongo, reranks, and returns the
top-K chunks with filename + page citations. The MCP layer does not
embed or rerank locally.

NOTE: this is deliberately NOT ``/actionchat/internal/vector/search`` —
that route is the agent's *own* durable scratch store
(``actionchat_<env>_user_index``, written only via ``citra_toolkit.vector``)
and does not contain the user's uploaded files. ``citra_vault_search``
must search the user's real corpus, so it targets ``/vault/search``.
"""
from __future__ import annotations

from typing import Any

from auth import CallerContext
from ._forward import call_internal
from .registry import ToolSpec, register_tool


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural-language query. Will be embedded server-side.",
        },
        "k": {
            "type": "integer",
            "description": "Number of chunks to return (1-30, default 10).",
            "minimum": 1,
            "maximum": 30,
        },
        "rerank": {
            "type": "boolean",
            "description": "Apply the precision rerank pass (default true).",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def _exec(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    body: dict[str, Any] = {"query": query}
    # The backend names the count `top_k`; accept either spelling from the
    # caller (the retrieval skill documents `k`) and clamp to the route's
    # 1..30 range so an out-of-range value is a no-op rather than a 400.
    count = args.get("k") if args.get("k") is not None else args.get("top_k")
    if count is not None:
        body["top_k"] = max(1, min(int(count), 30))
    if args.get("rerank") is not None:
        body["rerank"] = bool(args["rerank"])
    # /vault/search embeds + Milvus-searches + joins Mongo chunk text +
    # makes a downstream rerank call that itself allows 30s. The default
    # 25s forward timeout is shorter than that sub-call, so a slow rerank
    # can never succeed — give this route headroom past the 30s rerank.
    return await call_internal(
        "actionchat/internal/vault/search", body, caller, timeout_seconds=40.0
    )


register_tool(ToolSpec(
    name="citra_vault_search",
    description=(
        "Semantic search over the caller's user vault — the unstructured "
        "files (PDFs, docs, notes, transcripts) Citra chunked and embedded "
        "at upload time. One call covers the whole vault; do NOT pre-filter "
        "to a single file. Returns {chunks: [{text, score, filename, key, "
        "kind, page_number, paragraph_number, document_id, chunk_index, "
        "chunk_id}], query}. Use to ground answers in the user's own "
        "documents before reaching for the web."
    ),
    schema=_SCHEMA,
    executor=_exec,
    # User vault is a personal/session resource — relevant only inside an
    # action-chat session. The smart-app builder is designing enterprise
    # software and should not poke the BA's personal vault.
    allowed_scopes=("action-sandbox",),
))
