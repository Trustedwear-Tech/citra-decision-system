# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tool-description embedder.

Used at registration time to compute a single vector per tool that
captures its name, description, tags, and taxonomy summary. The
``/tools/search`` endpoint cosine-ranks query embeddings against
these stored vectors.

The embedder talks to any OpenAI-compatible ``/v1/embeddings`` endpoint
(OpenAI for the cloud deployment; the self-hosted inference-service
on-prem). It is
intentionally synchronous-shaped at the API layer (one call per tool /
query) — discovery-service registrations are infrequent (on startup +
heartbeat doesn't re-embed) and ``/tools/search`` is called once per
agent turn, so we don't need batching.

Failures are non-fatal: if the embedder is unreachable at register time
we store ``embedding: None`` and the tool falls to the bottom of the
ranked search results (substring fallback). A subsequent admin re-embed
sweep can backfill them later.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

import httpx

from config import get_settings

logger = logging.getLogger(__name__)


def _build_searchable_text(
    *,
    name: str,
    description: str,
    tags: Iterable[str] = (),
    data_types: Iterable[str] = (),
    taxonomy: Optional[dict] = None,
) -> str:
    """Compose the corpus we embed for a tool.

    Includes everything an LLM would use to decide "is this tool
    relevant to my query?": name, description, tags, data-type labels,
    and any taxonomy doc-type labels / synonyms.
    """
    parts: list[str] = [name.strip(), description.strip()]
    tag_list = [t for t in (tags or []) if t]
    if tag_list:
        parts.append("tags: " + ", ".join(tag_list))
    dtype_list = [t for t in (data_types or []) if t]
    if dtype_list:
        parts.append("data types: " + ", ".join(dtype_list))
    if taxonomy and isinstance(taxonomy, dict):
        doc_types = taxonomy.get("doc_types") or []
        labels: list[str] = []
        for dt in doc_types:
            if not isinstance(dt, dict):
                continue
            label = (dt.get("label") or dt.get("id") or "").strip()
            if label:
                labels.append(label)
            synonyms = dt.get("synonyms") or []
            if isinstance(synonyms, list):
                labels.extend(str(s).strip() for s in synonyms if s)
        if labels:
            parts.append("doc types: " + ", ".join(labels))
    return " | ".join(p for p in parts if p)


async def embed(text: str) -> Optional[list[float]]:
    """Return an embedding vector for ``text`` or ``None`` if the
    embedder is not configured / unreachable.

    Caller treats ``None`` as "tool searchable in degraded (substring)
    mode" — never raises."""
    cfg = get_settings()
    text = (text or "").strip()
    if not text:
        return None
    if not cfg.embed_base_url or not cfg.embed_model:
        logger.warning(
            "discovery embedder not configured (EMBED_BASE_URL / EMBED_MODEL); "
            "tool will be stored without embedding"
        )
        return None

    headers = {"Content-Type": "application/json"}
    if cfg.embed_api_key:
        headers["Authorization"] = f"Bearer {cfg.embed_api_key}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{cfg.embed_base_url.rstrip('/')}/v1/embeddings",
                headers=headers,
                json={"model": cfg.embed_model, "input": text},
            )
            r.raise_for_status()
            data = r.json() or {}
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("discovery embedder call failed: %s", e)
        return None

    items = data.get("data") or []
    if not items:
        return None
    vec = items[0].get("embedding")
    if not isinstance(vec, list):
        return None
    return [float(x) for x in vec]


async def embed_tool(
    *,
    name: str,
    description: str,
    tags: Iterable[str] = (),
    data_types: Iterable[str] = (),
    taxonomy: Optional[dict] = None,
) -> Optional[list[float]]:
    """Embed a tool's catalogue text — convenience wrapper used by
    ``/tools/register``."""
    text = _build_searchable_text(
        name=name,
        description=description,
        tags=tags,
        data_types=data_types,
        taxonomy=taxonomy,
    )
    return await embed(text)
