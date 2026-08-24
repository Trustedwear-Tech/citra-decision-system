# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra_embed — wrap an OpenAI-compatible embeddings endpoint.

Talks to any OpenAI-compatible /v1/embeddings route (OpenAI for the
cloud deployment; the self-hosted inference-service on-prem). The
MCP service holds its own API key (CITRA_MCP_EMBED_API_KEY) so the
sandbox never sees credentials.
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


_EMBED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "texts": {
            "type": "array",
            "description": "Strings to embed (max 256 items, 32KB each).",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 256,
        },
        "model": {
            "type": "string",
            "description": "Override default embedding model (optional).",
        },
    },
    "required": ["texts"],
    "additionalProperties": False,
}


_MAX_TEXT_BYTES = 32 * 1024


async def _exec_embed(args: dict[str, Any], caller: CallerContext) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.embed_base_url:
        raise RuntimeError("EMBED_BASE_URL is not configured on citra-mcp-service")

    texts_raw = args.get("texts") or []
    if not isinstance(texts_raw, list) or not texts_raw:
        raise ValueError("texts must be a non-empty array of strings")
    texts: list[str] = []
    for t in texts_raw:
        if not isinstance(t, str):
            raise ValueError("each item in texts must be a string")
        if len(t.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ValueError("text item exceeds 32KB limit")
        texts.append(t)

    model = (args.get("model") or cfg.embed_model or "").strip()
    if not model:
        raise RuntimeError("no embedding model configured (EMBED_MODEL)")

    headers = {"Content-Type": "application/json"}
    if cfg.embed_api_key:
        headers["Authorization"] = f"Bearer {cfg.embed_api_key}"

    started = time.time()
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{cfg.embed_base_url.rstrip('/')}/v1/embeddings",
            headers=headers,
            json={"model": model, "input": texts},
        )
        r.raise_for_status()
        data = r.json() or {}

    items = data.get("data") or []
    embeddings: list[list[float]] = [item.get("embedding") or [] for item in items]
    dim = len(embeddings[0]) if embeddings else 0
    elapsed_ms = int((time.time() - started) * 1000)
    logger.info(
        "[tool=citra_embed] user=%s n=%d dim=%d ms=%d",
        caller.user_id, len(texts), dim, elapsed_ms,
    )
    return {
        "embeddings": embeddings,
        "model": data.get("model") or model,
        "dim": dim,
        "elapsed_ms": elapsed_ms,
    }


register_tool(ToolSpec(
    name="citra_embed",
    description=(
        "Embed text(s) into vectors using the configured Citra embedding "
        "model. Returns {embeddings: [[float]], model, dim}. Use for "
        "ad-hoc similarity / clustering inside a sandbox session — for "
        "ingestion, prefer the upstream vector route."
    ),
    schema=_EMBED_SCHEMA,
    executor=_exec_embed,
))
