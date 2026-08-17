# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""NL relevance filter for builder phase — reranks RBAC-filtered lists.

The builder pod loads the BA's RBAC-visible MCP tool list and dataset
catalogue at the start of the interview. For a single-dept BA those
lists are small (tens of items) and the LLM can ground on them
directly. For an ``org_admin`` or ``super_admin`` the lists can balloon
to hundreds of items, blowing the prompt budget and diluting the LLM's
attention.

The discovery skill always passes the BA's goal as ``question``. We
reorder the candidates by relevance to that question using the shared
``reranker-service`` and return the top ``top_k`` (10 for MCP tools,
50 for datasets).

We rerank the RBAC-filtered list directly (no cosine pre-filter): item
counts at the org-admin tier are still well within the cross-encoder's
batch reach, and the alternative (embed every item every interview)
would need a second cache layer for negligible latency win.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

import httpx

from config import Settings

logger = logging.getLogger(__name__)


# Thresholds.
_NEEDS_SCOPE_ABOVE = 100     # > 100 RBAC-visible items: even reranked top-K
                             # may miss the right item; ask BA for a target
                             # area first.

# Minimum useful question length. "hi", "hello", "test" etc. yield
# garbage cross-encoder scores, so we skip the rerank and return the
# RBAC list unchanged — the LLM can ask the BA a clarifying question.
_MIN_QUESTION_CHARS = 8


def needs_scope_narrowing(candidates: List[Any]) -> bool:
    """True when the RBAC-visible list is too big to rerank usefully.

    The discovery skill should detect this and ask the BA for a target
    area (e.g. "claims", "procurement") to filter on before continuing.
    """
    return len(candidates) > _NEEDS_SCOPE_ABOVE


async def rerank_candidates(
    *,
    settings: Settings,
    question: Optional[str],
    candidates: List[Any],
    text_fn: Callable[[Any], str],
    top_k: int,
) -> List[Any]:
    """Return up to ``top_k`` ``candidates`` reranked by ``question``.

    Args:
        settings:    smart-app-service Settings (reranker_service_url).
        question:    free-text description from the builder UI.
        candidates:  arbitrary objects (dicts, models, strings).
        text_fn:     extracts the searchable text for one candidate.
        top_k:       max items to return after rerank (10 for MCP tools,
                     50 for datasets).

    Skip rules (return ``candidates[:top_k]`` unchanged):
      * empty list
      * ``len(candidates) <= top_k`` (nothing to rank away)
      * blank or trivially short ``question`` (e.g. "hi")
      * reranker URL not configured

    Fail-open: any reranker failure logs a warning and returns
    ``candidates[:top_k]`` in the original RBAC order so the builder
    never blocks on a reranker outage.
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        return candidates[:top_k]

    q = (question or "").strip()
    if len(q) < _MIN_QUESTION_CHARS:
        return candidates[:top_k]

    base = (settings.reranker_service_url or "").rstrip("/")
    if not base:
        return candidates[:top_k]

    chunks: List[dict] = []
    for i, c in enumerate(candidates):
        try:
            text = text_fn(c) or ""
        except Exception:  # noqa: BLE001
            text = ""
        if not text.strip():
            continue
        chunks.append({"id": str(i), "text": text, "score": 0.0, "metadata": {}})

    if not chunks:
        return candidates[:top_k]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base}/rerank",
                json={"query": q, "chunks": chunks, "top_k": top_k},
            )
            resp.raise_for_status()
            payload = resp.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUILDER] reranker unreachable (%s); falling back", exc)
        return candidates[:top_k]

    ordered: List[Any] = []
    for rr in payload.get("reranked_chunks") or []:
        try:
            idx = int(rr.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates):
            ordered.append(candidates[idx])
        if len(ordered) >= top_k:
            break

    if not ordered:
        return candidates[:top_k]
    return ordered
