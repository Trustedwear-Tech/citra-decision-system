# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra_rerank — wrap reranker-service /rerank for OpenClaw sandboxes.

The reranker is an internal standalone service (no auth) reached over the
docker-compose network. We forward the caller-supplied (query, chunks,
top_k) and return the reranked list. Designed so retrieval pipelines in
the sandbox can boost recall by passing many cheap candidates and
letting the reranker pick the best top_k.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from auth import CallerContext
from config import get_config
from .registry import ToolSpec, register_tool

logger = logging.getLogger(__name__)


_RERANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query the chunks should be scored against.",
        },
        "chunks": {
            "type": "array",
            "description": (
                "Candidate chunks. Each item must have id + text; "
                "optional original score and metadata are carried through."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "score": {"type": "number"},
                    "metadata": {"type": "object"},
                },
                "required": ["id", "text"],
            },
            "minItems": 1,
            "maxItems": 200,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top results to return (1-50, default 10).",
            "minimum": 1,
            "maximum": 50,
        },
    },
    "required": ["query", "chunks"],
    "additionalProperties": False,
}


async def _exec_rerank(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.reranker_service_url:
        raise RuntimeError("RERANKER_SERVICE_URL is not configured on citra-mcp-service")

    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    chunks = args.get("chunks") or []
    if not chunks:
        raise ValueError("chunks must be a non-empty array")
    top_k = max(1, min(int(args.get("top_k") or 10), 50))

    payload = {"query": query, "chunks": chunks, "top_k": top_k}
    started = time.time()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{cfg.reranker_service_url.rstrip('/')}/rerank", json=payload)
        r.raise_for_status()
        data = r.json() or {}

    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_rerank] user=%s candidates=%d top_k=%d ms=%d",
        caller.user_id, len(chunks), top_k, elapsed_ms,
    )
    return {
        "reranked_chunks": data.get("reranked_chunks") or [],
        "model": data.get("model"),
        "elapsed_ms": elapsed_ms,
    }


register_tool(ToolSpec(
    name="citra_rerank",
    description=(
        "Rerank a candidate list against a query using the Citra cross-encoder. "
        "Use after a coarse retrieval step (vector search, BM25) to pick the "
        "best top_k. Input chunks need id + text; metadata and original score "
        "are preserved on the output."
    ),
    schema=_RERANK_SCHEMA,
    executor=_exec_rerank,
))
