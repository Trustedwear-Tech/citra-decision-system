# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
reranker.py

HTTP client for the Reranker Service microservice.
Sends (query, chunks) to the external cross-encoder reranker service
and returns re-scored chunks.

Env vars:
  ENABLE_RERANKER              – "true" to activate (default: true)
  RERANKER_SERVICE_URL         – base URL of the reranker service (default: http://localhost:7302)
  RERANKER_TOP_K_MULTIPLIER    – over-fetch multiplier for Milvus (default: 2)
  RERANKER_TIMEOUT             – HTTP timeout in seconds (default: 60)

Used by:
- llamaindex_query_engine.py: retrieve_personal_context()
- agentic_rag/context_merger.py: merge_and_rank_contexts()
"""

import logging
import os
import time
from typing import List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ENABLE_RERANKER = os.getenv("ENABLE_RERANKER", "true").lower() == "true"
RERANKER_SERVICE_URL = os.getenv("RERANKER_SERVICE_URL", "http://localhost:7302").rstrip("/")
RERANKER_TOP_K_MULTIPLIER = int(os.getenv("RERANKER_TOP_K_MULTIPLIER", "2"))
RERANKER_TIMEOUT = int(os.getenv("RERANKER_TIMEOUT", "60"))

# Persistent HTTP client (connection pooling)
_http_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """Get or create a persistent httpx client."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(RERANKER_TIMEOUT, connect=10.0)
        )
    return _http_client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_reranker_enabled() -> bool:
    """Check whether the reranker is enabled via env config."""
    return ENABLE_RERANKER


def get_top_k_multiplier() -> int:
    """Return the over-fetch multiplier for Milvus queries."""
    return RERANKER_TOP_K_MULTIPLIER


def rerank(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """
    Re-score *chunks* against *query* by calling the reranker microservice.

    Each chunk dict must have ``text`` and ``chunk_id`` (or ``node_id``) keys.
    The original COSINE score is preserved as ``original_score``; the
    ``score`` field is replaced with the cross-encoder relevance score.

    If the service is unavailable or the call fails, the original list is
    returned sorted by the existing ``score`` (graceful fallback).
    """
    if not chunks:
        return chunks

    try:
        t0 = time.time()

        # Build request payload
        payload = {
            "query": query,
            "top_k": top_k,
            "chunks": [
                {
                    "id": c.get("chunk_id") or c.get("node_id") or str(i),
                    "text": c.get("text", ""),
                    "score": c.get("score", 0.0),
                    "metadata": c.get("metadata"),
                }
                for i, c in enumerate(chunks)
            ],
        }

        client = _get_client()
        resp = client.post(f"{RERANKER_SERVICE_URL}/rerank", json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Build a lookup: id → reranked scores
        reranked_map: Dict[str, Dict] = {}
        for rc in data.get("reranked_chunks", []):
            reranked_map[rc["id"]] = rc

        # Update original chunk dicts with reranker scores
        for i, chunk in enumerate(chunks):
            cid = chunk.get("chunk_id") or chunk.get("node_id") or str(i)
            if cid in reranked_map:
                rc = reranked_map[cid]
                chunk["original_score"] = chunk.get("score", 0.0)
                chunk["score"] = rc["score"]
                chunk["reranker_raw_score"] = rc["reranker_raw_score"]

        # Sort descending by reranker score and take top_k
        reranked = sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)[:top_k]

        elapsed = time.time() - t0
        device = data.get("device", "unknown")
        svc_ms = data.get("elapsed_ms", 0)
        logger.info(
            f"✅ Reranked {len(chunks)} → {len(reranked)} chunks in {elapsed:.3f}s "
            f"(service: {svc_ms:.1f}ms, device: {device}, "
            f"top: {reranked[0]['score']:.4f}, bottom: {reranked[-1]['score']:.4f})"
        )
        return reranked

    except Exception as e:
        logger.error(f"❌ Reranker service call failed: {e} — falling back to COSINE score ordering")
        return sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)[:top_k]
