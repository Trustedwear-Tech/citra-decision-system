# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Shared Embedding Helper
========================
Wraps calls to the configured OpenAI-compatible embeddings endpoint.
Used by both ingestion writers and RAG engines.

Two entry points:
    embed_query(text)    — direct, low-latency path for user queries
    embed_batch(texts)   — ingest path; routed through the per-dept
                           EmbedQueue worker (backpressure + 429 retry)
"""

import logging
from typing import List

import httpx

from config import get_settings

logger = logging.getLogger(__name__)


async def embed_texts_direct(texts: List[str]) -> List[List[float]]:
    """
    Low-level call to the embeddings endpoint. Used by:
      - the ingest EmbedQueue worker (serialised)
      - embed_query() for user-facing reads (direct)

    Raises on failure — callers are responsible for handling.
    """
    cfg = get_settings()
    url = f"{cfg.embedding_base_url.rstrip('/')}/embeddings"

    payload = {
        "model": cfg.embedding_model,
        "input": texts,
    }
    # When the Milvus collection was ingested at a non-native dim (e.g. 768),
    # the query embedding must request the same dim or the vectors won't
    # match. 0 = omit (native-dim models like self-hosted bge-m3).
    if getattr(cfg, "embedding_dimension", 0):
        payload["dimensions"] = cfg.embedding_dimension
    headers = {
        "Authorization": f"Bearer {cfg.embedding_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=cfg.embedding_timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # OpenAI-compatible response: {"data": [{"embedding": [...], "index": 0}, ...]}
    embeddings_raw = data.get("data", [])
    # Sort by index in case the API reorders them
    embeddings_raw.sort(key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in embeddings_raw]


# Back-compat alias — some callers still import embed_texts directly.
embed_texts = embed_texts_direct


async def embed_query(text: str) -> List[float]:
    """Embed a single query string. Bypasses the ingest queue for latency.

    §1 query-embedding cache: an embedding is a pure function of
    (model, dimension, text), so a repeated/canonical question reuses the cached
    vector and skips the embedding-service round-trip. Fail-open — any cache
    miss/error falls straight through to a live embed and the result is cached.
    """
    cfg = get_settings()
    model = getattr(cfg, "embedding_model", "") or ""
    dim = int(getattr(cfg, "embedding_dimension", 0) or 0)
    try:
        import plan_cache
        cached = plan_cache.get_query_embedding(model, dim, text)
        if cached:
            return cached
    except Exception:  # noqa: BLE001 — cache is best-effort, never block a query
        cached = None

    vectors = await embed_texts_direct([text])
    vec = vectors[0]

    try:
        import plan_cache
        plan_cache.set_query_embedding(model, dim, text, vec)
    except Exception:  # noqa: BLE001 — cache is best-effort
        pass
    return vec
