"""
Semantic RAG Engine
===================
Handles queries for unstructured data sources (files, API text).

Flow:
  1. Embed the query
  2. Search the source's Milvus semantic collection (limit=20)
  3. Filter chunks below the minimum score threshold
  4. Call the reranker service → return top 5 chunks
  5. If reranker is not configured, return top 5 by Milvus COSINE score

Text is stored INSIDE Milvus (not in MongoDB), so each hit contains the
full chunk text — no second round-trip required.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import get_settings
from models import CLASSIFICATION_LEVELS, ChunkResult
from rag.embed import embed_query
from planners import plan_retrieval, format_retrieval_plan_for_log
from router import get_source

logger = logging.getLogger(__name__)


def _build_doc_type_expr(
    doc_types: Optional[List[str]],
    classification_max: Optional[str],
) -> Optional[str]:
    """Compose a Milvus boolean expression for taxonomy/classification filtering.

    Returns ``None`` when no filter is requested. The caller must tolerate the
    expression failing at search time (older collections may not have these
    fields) and fall back to an unfiltered search.
    """
    parts: List[str] = []
    if doc_types:
        # Sanitize: keep alnum + underscore + hyphen, drop anything else.
        cleaned = [
            "".join(ch for ch in dt if ch.isalnum() or ch in "_-")
            for dt in doc_types
            if isinstance(dt, str) and dt.strip()
        ]
        cleaned = [c for c in cleaned if c]
        if cleaned:
            quoted = ", ".join(f'"{c}"' for c in cleaned)
            parts.append(f"doc_type in [{quoted}]")
    if classification_max and classification_max in CLASSIFICATION_LEVELS:
        idx = CLASSIFICATION_LEVELS.index(classification_max)
        allowed = CLASSIFICATION_LEVELS[: idx + 1]
        quoted = ", ".join(f'"{c}"' for c in allowed)
        parts.append(f"classification in [{quoted}]")
    if not parts:
        return None
    return " and ".join(parts)


# ---------------------------------------------------------------------------
# Reranker call
# ---------------------------------------------------------------------------

async def _rerank(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int,
) -> List[Dict[str, Any]]:
    """Call the reranker service. Falls back to score-ordered list on failure."""
    cfg = get_settings()
    if not cfg.reranker_url or not chunks:
        chunks.sort(key=lambda c: c.get("score", 0.0), reverse=True)
        return chunks[:top_k]

    payload = {
        "query": query,
        "chunks": [
            {
                "id": str(i),
                "text": c["text"],
                "score": float(c.get("score", 0.0)),
                "metadata": c.get("metadata", {}),
            }
            for i, c in enumerate(chunks)
        ],
        "top_k": top_k,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{cfg.reranker_url.rstrip('/')}/rerank",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        id_to_chunk = {str(i): c for i, c in enumerate(chunks)}
        reranked = []
        for rc in data.get("reranked_chunks", []):
            original = id_to_chunk.get(rc["id"])
            if original:
                original["score"] = rc["score"]
                reranked.append(original)
        return reranked

    except Exception as exc:
        logger.warning(f"⚠️ [SEMANTIC] Reranker failed — falling back to Milvus scores: {exc}")
        chunks.sort(key=lambda c: c.get("score", 0.0), reverse=True)
        return chunks[:top_k]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_semantic(
    query: str,
    source_id: str,
    max_results: int = 5,
    doc_types: Optional[List[str]] = None,
    classification_max: Optional[str] = None,
) -> List[ChunkResult]:
    """
    Query the semantic Milvus collection for a source and return reranked chunks.

    Args:
        query:              Natural-language query from Citra
        source_id:          Which source's collection to search
        max_results:        Final chunk count (after reranking)
        doc_types:          Optional pre-filter on chunk metadata.doc_type
                            (e.g. ["policy", "sop"]). Best-effort: if the
                            collection lacks this field, the filtered search
                            is retried without the expression.
        classification_max: Optional upper bound on metadata.classification
                            (public < internal < confidential < restricted).

    Returns:
        List of ChunkResult sorted by reranker score descending.
    """
    cfg = get_settings()
    fetch_limit = cfg.semantic_fetch_limit   # default 20
    min_score = cfg.semantic_min_score       # default 0.25

    # ── Plan (no-clarification) — optional; None falls back to default flow ─
    source_meta = get_source(source_id) or {}
    # Collection name is <prefix>_<dept>_<source>. The dept MUST come from the
    # source's own registry entry — NOT cfg.dept_ids[0], which is just the
    # first of the MCP's multi-dept list and would mis-name any source not in
    # that first dept (e.g. a 'central' policy library on a urban-dev MCP).
    collection_name = cfg.collection_name_semantic(source_id, source_meta.get("dept_id"))
    plan = None
    if cfg.planner_enabled:
        plan = await plan_retrieval(
            query,
            source_id=source_id,
            source_description=str(source_meta.get("description", "")),
            timeout_seconds=cfg.planner_timeout_seconds,
        )
    logger.info(f"📋 [SEMANTIC] plan: {format_retrieval_plan_for_log(plan)}")

    # Derive query list and top_k from plan
    if plan and plan.get("retrieval_strategy") == "multi_query" and plan.get("sub_questions"):
        query_texts = plan["sub_questions"]
    else:
        query_texts = [query]

    effective_top_k = int(plan["top_k"]) if plan else max_results
    # Final result cap stays bounded by caller's max_results (with headroom for planner).
    final_k = min(max(effective_top_k, max_results), 20)
    do_rerank = bool(plan["rerank"]) if plan else True

    # ── 1. Embed query (one per sub-question) ──────────────────────────────
    try:
        query_vecs: List[List[float]] = []
        for q in query_texts:
            query_vecs.append(await embed_query(q))
    except Exception as exc:
        logger.error(f"❌ [SEMANTIC] Embedding failed: {exc}")
        return []

    # ── 2. Milvus vector search (one per sub-question, union results) ──────
    try:
        from rag._milvus import get_client
        client = get_client()

        if not client.has_collection(collection_name):
            logger.warning(
                f"⚠️ [SEMANTIC] Collection {collection_name!r} does not exist — "
                f"run POST /ingest/{source_id} first"
            )
            return []

        merged_hits: Dict[str, Dict[str, Any]] = {}
        filter_expr = _build_doc_type_expr(doc_types, classification_max)
        if filter_expr:
            logger.info(f"🔎 [SEMANTIC] applying filter expr: {filter_expr}")
        # Track whether the schema lacks taxonomy fields so we don't retry
        # the expression for every sub-question.
        filter_disabled = False
        for qvec in query_vecs:
            search_kwargs: Dict[str, Any] = dict(
                collection_name=collection_name,
                data=[qvec],
                limit=fetch_limit,
                output_fields=[
                    # Required for the result envelope.
                    "text", "source", "source_id", "chunk_index", "ingested_at",
                    # Taxonomy / classification — used for filtering and UI chips.
                    "doc_type", "classification",
                    # Document identity — needed so the caller can group
                    # chunks back into "one row per document" AND so the
                    # runtime "Open" button can request a signed URL to
                    # the original artifact in object storage. doc_path
                    # is the bucket key suffix; page lets the URL deep-link.
                    "doc_path", "page", "id",
                ],
            )
            search_results = None
            if filter_expr and not filter_disabled:
                try:
                    search_results = client.search(filter=filter_expr, **search_kwargs)
                except Exception as exc:
                    logger.warning(
                        f"⚠️ [SEMANTIC] filter expr rejected by collection "
                        f"{collection_name!r} ({exc}); retrying without filter"
                    )
                    filter_disabled = True
            if search_results is None:
                # Fall back to plain search; also drop optional output fields
                # if the collection doesn't have them.
                try:
                    search_results = client.search(**search_kwargs)
                except Exception:
                    search_kwargs["output_fields"] = [
                        "text", "source", "source_id", "chunk_index", "ingested_at",
                    ]
                    search_results = client.search(**search_kwargs)
            for hit in search_results[0]:
                score = float(hit.get("distance", 0.0))
                if score < min_score:
                    continue
                entity = hit.get("entity", {})
                # Dedup by the unique chunk id (Milvus primary key). `source` and
                # `chunk_index` are None in some collections (e.g. the policy
                # library), so the old "<source>::<chunk_index>" key collapsed
                # EVERY chunk to "<source_id>::None" — only 1 chunk ever survived,
                # which forced the triage agent to re-query the RAG 6x. The PK is
                # always unique per chunk and is also the right key for deduping
                # the same chunk across multi_query unions.
                key = (
                    hit.get("id")
                    or entity.get("id")
                    or f"{entity.get('source', source_id)}::{entity.get('chunk_index', 0)}"
                )
                existing = merged_hits.get(key)
                if existing is None or score > existing["score"]:
                    merged_hits[key] = {
                        "text": entity.get("text", ""),
                        "score": score,
                        "source": entity.get("source", source_id),
                        "metadata": {
                            "source_id": entity.get("source_id", source_id),
                            "chunk_index": entity.get("chunk_index", 0),
                            "doc_type": entity.get("doc_type"),
                            "classification": entity.get("classification"),
                            # Forward doc identity. Without these the runtime
                            # cannot group chunks by document or sign a URL
                            # back to the source — both critical for the
                            # document_view panel UX.
                            "doc_path": entity.get("doc_path"),
                            "page": entity.get("page"),
                            "id": entity.get("id"),
                        },
                    }
        raw_chunks = list(merged_hits.values())
    except Exception as exc:
        logger.error(f"❌ [SEMANTIC] Milvus search failed on {collection_name!r}: {exc}")
        return []

    logger.info(
        f"🔍 [SEMANTIC] {source_id}: {len(query_vecs)} query(s), "
        f"{len(raw_chunks)} unique chunks above min_score={min_score}"
    )

    if not raw_chunks:
        return []

    # ── 3. Rerank (optional) → top final_k ─────────────────────────────────
    if do_rerank:
        reranked = await _rerank(query, raw_chunks, top_k=final_k)
    else:
        raw_chunks.sort(key=lambda c: c.get("score", 0.0), reverse=True)
        reranked = raw_chunks[:final_k]

    logger.info(f"✅ [SEMANTIC] {source_id}: returning {len(reranked)} chunk(s)")

    # Attach plan to each chunk's metadata so the caller can see interpretation.
    results: List[ChunkResult] = []
    for c in reranked:
        meta = dict(c.get("metadata", {}))
        if plan:
            meta["plan"] = plan
        results.append(ChunkResult(
            text=c["text"],
            score=c["score"],
            source=c["source"],
            metadata=meta,
        ))
    return results
