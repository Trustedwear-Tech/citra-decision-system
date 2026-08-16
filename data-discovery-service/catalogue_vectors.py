# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Catalogue vector index — semantic dataset search for the SmartApp builder.

The builder must find the *relevant* datasets for a BA's goal across a
potentially huge enterprise catalogue, without fetching/ranking the whole
thing. This module is the **recall** stage: at crawl time we embed each
dataset's signature (name + description + columns) and upsert it into a
**dedicated Milvus collection** (separate from the RAG document collections);
at query time we ANN-search it, scoped to the caller's tenant (and source).
The precision stage (cross-encoder rerank) stays in smart-app-service.

Design notes:
  * Separate Milvus collection (``catalogue_milvus_collection``) — the
    catalogue is dataset *metadata*, not document chunks; keeping it apart
    from ``mcp_<dept>_<source>`` RAG collections avoids schema collisions.
  * Keyed/filtered by ``tenant_id`` (+ ``source_id``; ``dept_id`` reserved for
    a later RBAC pass). One ANN query returns only the relevant top-K.
  * FAIL-OPEN everywhere: if vectoring is disabled, Milvus/embeddings are
    unreachable, or pymilvus isn't installed, indexing is a no-op and search
    returns ``None`` so the caller falls back to the plain Mongo list.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import Settings

logger = logging.getLogger(__name__)

_client: Any = None  # pymilvus MilvusClient singleton
_collection_ready: bool = False


def _enabled(settings: Settings) -> bool:
    return bool(settings.catalogue_vector_enabled and settings.milvus_uri)


def _signature(entry: Dict[str, Any]) -> str:
    """The text we embed for a dataset — name + description + column names."""
    cols = entry.get("columns") or []
    col_names = ", ".join(
        str(c.get("name")) for c in cols if isinstance(c, dict) and c.get("name")
    )
    desc = (entry.get("description") or "").strip()
    name = entry.get("name") or entry.get("dataset_id") or ""
    return f"{name}\n{desc}\nColumns: {col_names}".strip()


def _pk(tenant_id: str, source_id: str, dataset_id: str) -> str:
    return f"{tenant_id}::{source_id}::{dataset_id}"


async def _embed(settings: Settings, texts: List[str]) -> List[List[float]]:
    """OpenAI-compatible embeddings call (mirrors the dept-MCP embed helper)."""
    url = f"{settings.embedding_base_url.rstrip('/')}/embeddings"
    payload: Dict[str, Any] = {"model": settings.embedding_model, "input": texts}
    if settings.embedding_dimension:
        payload["dimensions"] = settings.embedding_dimension
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=settings.embedding_timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("data", [])
    rows.sort(key=lambda x: x.get("index", 0))
    return [r["embedding"] for r in rows]


def _get_client(settings: Settings):
    """Lazy pymilvus client + idempotent collection creation. None on failure."""
    global _client, _collection_ready
    if _client is not None and _collection_ready:
        return _client
    try:
        from pymilvus import DataType, MilvusClient  # lazy: optional dep
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CATALOGUE_VEC] pymilvus unavailable: %s", exc)
        return None
    try:
        if _client is None:
            _client = MilvusClient(
                uri=settings.milvus_uri, token=settings.milvus_token or None
            )
        name = settings.catalogue_milvus_collection
        if not _client.has_collection(name):
            schema = _client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=600)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=settings.embedding_dimension)
            schema.add_field("tenant_id", DataType.VARCHAR, max_length=128)
            schema.add_field("dept_id", DataType.VARCHAR, max_length=128)
            schema.add_field("source_id", DataType.VARCHAR, max_length=256)
            schema.add_field("dataset_id", DataType.VARCHAR, max_length=512)
            schema.add_field("public", DataType.VARCHAR, max_length=2)  # "1"|"0"
            idx = _client.prepare_index_params()
            idx.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
            _client.create_collection(name, schema=schema, index_params=idx)
            logger.info("[CATALOGUE_VEC] created Milvus collection %s", name)
        _client.load_collection(name)
        _collection_ready = True
        return _client
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CATALOGUE_VEC] Milvus init failed: %s", exc)
        return None


async def index_entries(settings: Settings, entries: List[Dict[str, Any]]) -> int:
    """Embed + upsert dataset signatures into the catalogue Milvus collection.

    Called from the crawl after the Mongo upsert. Returns count indexed (0 on
    disabled / failure — fail-open, never raises into the crawl).
    """
    if not _enabled(settings) or not entries:
        return 0
    client = _get_client(settings)
    if client is None:
        logger.error(
            "[CATALOGUE_VEC] indexing ENABLED but Milvus unavailable — %d "
            "datasets written to Mongo but NOT indexed; /catalogue/search will "
            "503 until reindexed.", len(entries),
        )
        return 0
    try:
        texts = [_signature(e) for e in entries]
        vecs = await _embed(settings, texts)
        rows = []
        for e, v in zip(entries, vecs):
            tid = str(e.get("tenant_id") or "")
            sid = str(e.get("source_id") or "")
            did = str(e.get("dataset_id") or "")
            rows.append(
                {
                    "pk": _pk(tid, sid, did),
                    "embedding": v,
                    "tenant_id": tid,
                    "dept_id": str(e.get("dept_id") or ""),
                    "source_id": sid,
                    "dataset_id": did,
                    "public": "1" if e.get("public_within_org") else "0",
                }
            )
        client.upsert(collection_name=settings.catalogue_milvus_collection, data=rows)
        logger.info("[CATALOGUE_VEC] indexed %d datasets", len(rows))
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        # Don't abort the crawl (Mongo catalogue is already written), but log
        # LOUD — an unindexed catalogue makes /catalogue/search 503 until the
        # next successful crawl.
        logger.error("[CATALOGUE_VEC] index_entries failed (datasets NOT indexed): %s", exc)
        return 0


async def delete_entries(settings: Settings, keys: List[Tuple[str, str, str]]) -> int:
    """Drop pruned datasets from the recall index. ``keys`` = (tenant, source, dataset).

    Called from the crawl's prune, right after the Mongo delete — the mirror of
    ``index_entries``. Returns the count removed.

    Fail-open (like indexing) but for a narrower reason: ``search`` hydrates its
    hits from Mongo and drops the ones it can't find, so a left-behind vector
    cannot serve a deleted dataset. It just silently consumes a ``top_k`` slot,
    costing recall — a quality bug, not a correctness one. So log LOUD and let
    the crawl finish rather than raise.
    """
    if not _enabled(settings) or not keys:
        return 0
    client = _get_client(settings)
    if client is None:
        logger.error(
            "[CATALOGUE_VEC] %d dataset(s) pruned from Mongo but NOT from the index "
            "(Milvus unavailable) — they will eat recall slots until reindexed.",
            len(keys),
        )
        return 0
    try:
        pks = [_pk(t, s, d) for (t, s, d) in keys]
        client.delete(collection_name=settings.catalogue_milvus_collection, ids=pks)
        logger.info("[CATALOGUE_VEC] removed %d pruned dataset(s) from the index", len(pks))
        return len(pks)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[CATALOGUE_VEC] delete_entries failed — %d stale vector(s) remain: %s",
            len(keys), exc,
        )
        return 0


async def search(
    settings: Settings,
    *,
    query: str,
    tenant_id: str,
    dept_ids: Optional[List[str]] = None,
    source_id: Optional[str] = None,
    top_k: int = 50,
    all_depts: bool = False,
) -> Optional[List[Tuple[str, str]]]:
    """ANN-search the catalogue, scoped to tenant (+ source + dept RBAC).

    Returns ``[(source_id, dataset_id), ...]`` ranked by relevance — possibly
    **empty** if nothing matched (the caller decides what an empty result
    means). Returns ``None`` ONLY when vectoring is *disabled* — a configured
    mode, not a failure. When vectoring is ENABLED, any genuine failure
    (Milvus/pymilvus/embedding/search error) **RAISES** — we fail loud rather
    than masking a broken index behind a silent fallback.

    RBAC: ``all_depts=True`` (org_admin / super_admin) → no dept filter. Else a
    dataset is visible when its ``dept_id`` is in ``dept_ids`` OR it's
    ``public`` OR it's unstamped (``dept_id == ""`` — we can't enforce a dept we
    don't know).
    """
    if not _enabled(settings) or not (query or "").strip():
        return None  # disabled / empty query — configured no-op, not a failure
    client = _get_client(settings)
    if client is None:
        # Enabled but the client couldn't initialise (pymilvus missing / bad
        # uri) — that's a real misconfiguration, not a "disabled" no-op.
        raise RuntimeError(
            "catalogue vector search is enabled but Milvus is unavailable "
            "(pymilvus missing or MILVUS_URI unreachable)"
        )
    qvec = (await _embed(settings, [query]))[0]  # embedding failure propagates

    filt = f'tenant_id == "{tenant_id}"'
    if source_id:
        filt += f' and source_id == "{source_id}"'
    if not all_depts:
        # Trusted JWT claims, but skip any with a quote to keep the expr safe.
        depts = [d for d in (dept_ids or []) if d and '"' not in d]
        clauses = ['public == "1"', 'dept_id == ""']
        if depts:
            clauses.append("dept_id in [" + ", ".join(f'"{d}"' for d in depts) + "]")
        filt += " and (" + " or ".join(clauses) + ")"
    # Search failure propagates (fail loud) — a broken index must not be
    # masked as "no results".
    res = client.search(
        collection_name=settings.catalogue_milvus_collection,
        data=[qvec],
        limit=top_k,
        filter=filt,
        output_fields=["source_id", "dataset_id"],
    )

    out: List[Tuple[str, str]] = []
    for hit in (res[0] if res else []):
        ent = hit.get("entity", {}) if isinstance(hit, dict) else {}
        sid = ent.get("source_id")
        did = ent.get("dataset_id")
        if sid and did:
            out.append((str(sid), str(did)))
    return out
