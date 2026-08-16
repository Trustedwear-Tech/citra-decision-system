# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Reranker-service proxy — re-scores a list of candidates against the
query using the cross-encoder hosted by reranker-service.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ._proxy import proxy_url
from .client import http_client


@dataclass
class Candidate:
    id: str
    text: str
    score: float
    original_score: float
    metadata: dict[str, Any]


def rerank(query: str, candidates: Sequence[dict[str, Any]], *,
           top_k: int = 10, timeout: float = 30.0) -> list[Candidate]:
    """Rerank a list of candidate dicts.

    Each candidate must have ``text``; ``id``, ``score``, ``metadata`` are
    optional. Returns the top-K reranked, sorted by reranker score desc.
    """
    payload = {"query": query, "candidates": list(candidates), "top_k": int(top_k)}
    with http_client(timeout=timeout) as c:
        r = c.post(proxy_url("rerank"), json=payload)
        r.raise_for_status()
        data = r.json() or {}
    out: list[Candidate] = []
    for c_ in data.get("reranked_chunks") or []:
        out.append(Candidate(
            id=str(c_.get("id") or ""),
            text=str(c_.get("text") or ""),
            score=float(c_.get("score") or 0.0),
            original_score=float(c_.get("original_score") or 0.0),
            metadata=c_.get("metadata") or {},
        ))
    return out
