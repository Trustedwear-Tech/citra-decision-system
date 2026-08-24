# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Fraud image index — Phase P2b of docs/fraud-detection-primitives-plan.md.

Near-duplicate / similar-image detection across cases via multimodal image
embeddings (jina-clip-v2 class, OPEN-WEIGHTS by design — an air-gapped
deployment self-hosts the same model → identical vectors, no re-embed).

Design decisions (2026-07-02):
  * Request the model's FULL native dim (1024) and persist it on the artifact
    fingerprint doc in Mongo (~4 KB/unique image). Milvus indexes a
    Matryoshka-truncated slice (512, renormalized) — a future dim change is a
    LOCAL re-index with zero new API calls.
  * The API is the THIRD line of defense: SHA-256 (identical) → dHash
    (recompression) → embedding (crops / screenshots / re-shoots). Each unique
    image is embedded EXACTLY ONCE, keyed by content hash.
  * Empty ``IMAGE_EMBED_API_KEY`` disables this layer gracefully; failures are
    RECORDED in artifact_flags (never silent), and never block the analysis.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

_milvus_client = None
_collection_ready: set = set()


def _index_collection_name(settings) -> str:
    """Env-routed Milvus collection name (test_ prefix in the test env)."""
    name = settings.fraud_image_collection
    try:
        import main

        if main.current_env() == "test":
            name = f"test_{name}"
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod collection
        pass
    return name


def _get_milvus(settings):
    """Cached MilvusClient — construction delegated to grounding_refresh's
    factory (single source of truth for URI/token/timeout hardening)."""
    global _milvus_client
    if _milvus_client is None:
        from grounding_refresh import _milvus_client as _make_client

        _milvus_client = _make_client(settings)
    return _milvus_client


def _ensure_collection(settings) -> str:
    name = _index_collection_name(settings)
    if name in _collection_ready:
        return name
    client = _get_milvus(settings)
    if not client.has_collection(name):
        # Quick-create: string PK = sha256 (upsert dedups naturally), cosine
        # metric, dynamic fields carry tenant/app/case refs for filtering.
        client.create_collection(
            collection_name=name,
            dimension=settings.image_embed_index_dim,
            primary_field_name="id",
            id_type="string",
            max_length=80,
            vector_field_name="vector",
            metric_type="COSINE",
            auto_id=False,
        )
        log.info("[FRAUD-IMG] created Milvus collection %s (dim=%d)",
                 name, settings.image_embed_index_dim)
    _collection_ready.add(name)
    return name


def _flt_str(v: str) -> str:
    """Escape a value for interpolation into a Milvus filter expression —
    record ids are agent-supplied, so quotes/backslashes must not be able to
    break (or widen) the filter."""
    return (v or "").replace("\\", "\\\\").replace('"', '\\"')


def truncate_renorm(vec: List[float], dim: int) -> List[float]:
    """Matryoshka truncation: first ``dim`` components, re-normalized to unit
    length (required for cosine to stay meaningful after truncation)."""
    v = vec[:dim]
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


async def embed_image(settings, raw: bytes, mime: str = "image/jpeg") -> List[float]:
    """One embedding call — full requested dim. Raises on failure (caller
    records the error visibly in artifact_flags; never silent).

    Payload shape is provider-adaptive: Jina's native API takes
    ``{"image": <b64>}`` items in an input array; OpenRouter (default —
    google/gemini-embedding-2 on the already-paid account) takes the image as
    a DATA-URI STRING in ``input`` (live-verified 2026-07-02: object/array
    shapes are rejected with invalid_union; the plain data-URI string returns
    200 with the requested ``dimensions``). Both return the same OpenAI
    response shape."""
    b64 = base64.b64encode(raw).decode("ascii")
    if "jina" in (settings.image_embed_model or "").lower() \
            or "jina.ai" in (settings.image_embed_url or "").lower():
        image_input: Any = [{"image": b64}]
    else:
        image_input = f"data:{mime or 'image/jpeg'};base64,{b64}"
    payload = {
        "model": settings.image_embed_model,
        "dimensions": settings.image_embed_store_dim,
        "input": image_input,
    }
    headers = {"Authorization": f"Bearer {settings.image_embed_api_key}",
               "Content-Type": "application/json"}
    timeout_s = float(getattr(settings, "image_embed_timeout_s", 45.0) or 45.0)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                settings.image_embed_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as exc:
        # httpx timeout exceptions stringify to "" — a bare
        # f"{type}: {exc}" in artifact_flags reads as 'ReadTimeout: ' and
        # tells the operator nothing. Name the model, the budget, and the
        # consequence, at ERROR level: the default model is a FREE tier that
        # throttles under load, and a timeout means the similar-image fraud
        # tier is OFF for this artifact.
        msg = (
            f"image-embedding call TIMED OUT after {timeout_s:.0f}s "
            f"(model {settings.image_embed_model!r}) — the similar-image "
            f"fraud tier is OFF for this artifact. Free-tier endpoints "
            f"throttle under load: raise IMAGE_EMBED_TIMEOUT_S, or move to a "
            f"paid/self-hosted embedding model."
        )
        log.error("[FRAUD] %s", msg)
        raise RuntimeError(msg) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = (exc.response.text or "")[:200]
        msg = (
            f"image-embedding call FAILED with HTTP {status} "
            f"(model {settings.image_embed_model!r}): {detail} — the "
            f"similar-image fraud tier is OFF for this artifact."
            + (" HTTP 429 = free-tier rate limit; move to a paid/self-hosted "
               "embedding model." if status == 429 else "")
        )
        log.error("[FRAUD] %s", msg)
        raise RuntimeError(msg) from exc
    vec = ((data.get("data") or [{}])[0]).get("embedding")
    if not isinstance(vec, list) or not vec:
        raise RuntimeError(f"embedding response malformed: keys={list(data)[:6]}")
    return [float(x) for x in vec]


async def index_and_search(
    *,
    settings,
    raw: bytes,
    mime: str = "image/jpeg",
    sha256: str,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    record_ref: Optional[str],
    item_id: str,
    task_type: str,
    stored_vector: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Embed (or reuse cached full vector) → upsert into the index → search for
    near-duplicates / similar images from OTHER cases.

    Returns {vector?: full-dim (for the caller to persist when freshly
    embedded), near_duplicates: [...], similar: [...]} — hits carry score +
    case pointers, ready to cite as officer evidence.
    """
    if not settings.image_embed_api_key:
        return {"disabled": "IMAGE_EMBED_API_KEY not configured"}

    out: Dict[str, Any] = {}
    full = stored_vector
    if not full:
        full = await embed_image(settings, raw, mime)
        out["vector"] = full  # caller persists on the fingerprint doc
    idx_vec = truncate_renorm(full, settings.image_embed_index_dim)

    def _milvus_ops() -> Dict[str, Any]:
        name = _ensure_collection(settings)
        client = _get_milvus(settings)
        client.upsert(collection_name=name, data=[{
            "id": sha256,
            "vector": idx_vec,
            "tenant_id": tenant_id or "",
            "app_slug": app_slug or "",
            "record_ref": record_ref or "",
            "item_id": item_id,
            "task_type": task_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }])
        # tenant_id ALWAYS filters the search — single-tenant deploys make it a
        # no-op, but on a shared/demo-hub Milvus it is the isolation boundary
        # (multi-tenant code = correctness): never return another org's case
        # refs as officer evidence.
        flt = f'tenant_id == "{_flt_str(tenant_id or "")}" and id != "{_flt_str(sha256)}"'
        if record_ref:
            flt += f' and record_ref != "{_flt_str(record_ref)}"'
            # LEGACY: vectors indexed before record_refs were dataset-qualified carry
            # the BARE key. Exclude that form too, else a re-screened record's own
            # prior vector comes back as a near-duplicate OF ITSELF (+3 bogus points).
            # Milvus takes an expression, not a Python compare, so both forms are
            # excluded explicitly (cf. fraud_checks.same_record_ref).
            _bare = record_ref.rsplit(":", 1)[-1]
            if _bare != record_ref:
                flt += f' and record_ref != "{_flt_str(_bare)}"'
        hits = client.search(
            collection_name=name, data=[idx_vec], limit=6, filter=flt,
            output_fields=["record_ref", "app_slug", "item_id", "task_type", "created_at"],
        )
        return {"hits": hits[0] if hits else []}

    res = await asyncio.to_thread(_milvus_ops)
    near, similar = [], []
    for h in res["hits"]:
        score = float(h.get("distance", 0.0))  # COSINE similarity via MilvusClient
        entity = h.get("entity") or {}
        row = {
            "score": round(score, 4),
            "record_ref": entity.get("record_ref"),
            "app_slug": entity.get("app_slug"),
            "item_id": entity.get("item_id"),
            "task_type": entity.get("task_type"),
            "first_indexed": entity.get("created_at"),
        }
        if score >= settings.fraud_image_dup_threshold:
            near.append(row)
        elif score >= settings.fraud_image_similar_threshold:
            similar.append(row)
    if near:
        out["near_duplicates"] = near
    if similar:
        out["similar"] = similar
    return out
