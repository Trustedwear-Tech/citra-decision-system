# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Consolidated platform semantic-search reader (RAG short-circuit #6).

RAG is common to many consumers (chat, smart-app runtime, builder). This module
is the ONE platform-side semantic reader — the MCP does NO RAG anymore (pure
disconnect); every consumer routes `kind == semantic` queries here.

It GENERALIZES Citra-Service's existing personal-data-store RAG (the `citra`
vault collection path) to a PARAMETERIZED per-source dept collection
(`<prefix>_<dept>_<source>`, e.g. `mcp_operations_sop_library_operations`) —
same embedding model, same reranker, same Milvus client, just collection +
filter as parameters. So SOP-Library / dept-RAG content ingested to those
collections is queried here without the MCP.

This file holds the PURE cores (collection-name resolution matching the MCP's
`_safe` convention; the Milvus boolean-filter builder). ``semantic_search`` (the
embed → Milvus → rerank call) reuses Citra-Service's existing RAG plumbing and
is the live-infra part.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semantic_reader")

# COSINE floor applied to Milvus hits BEFORE reranking (mirrors the personal
# path's MILVUS_ABSOLUTE_MIN_SCORE default). The reranker's own scores are a
# different, smaller band (~0.02–0.06 remote cross-encoder) — never apply this
# COSINE floor to reranked scores.
_DEFAULT_MIN_COSINE = 0.10

# The dept semantic collections (written by the platform's VectorSink) use a
# MINIMAL schema: a primary `id`, the dense vector field named `vector` (NOT the
# personal vault's `dense_vector`), and everything else — including `text` — as
# DYNAMIC fields. So we search `anns_field="vector"` and fetch `output_fields=["*"]`
# to get the text + all metadata. Verified against a live demo collection
# (demo_acme_power_central_pmu_acme_power_policy_library).
_VECTOR_FIELD = "vector"
_OUTPUT_FIELDS = ["*"]

# The vector-field name differs by ingestion path: platform VectorSink collections
# use `vector`; the SOP-Library ingestion (personal-vault schema) uses
# `dense_vector`. Detect it from the collection schema (cached per collection) so
# the reader is uniform across both.
_vector_field_cache: Dict[str, str] = {}


def _resolve_vector_field(client, collection: str) -> str:
    """Return the collection's dense-vector field name (cached). Prefers `vector`
    then `dense_vector`, else the first field carrying a `dim` param."""
    if collection in _vector_field_cache:
        return _vector_field_cache[collection]
    field = _VECTOR_FIELD
    try:
        fields = (client.describe_collection(collection) or {}).get("fields", [])
        names = {f.get("name") for f in fields}
        if "vector" in names:
            field = "vector"
        elif "dense_vector" in names:
            field = "dense_vector"
        else:
            for f in fields:
                if (f.get("params") or {}).get("dim"):
                    field = f.get("name") or field
                    break
    except Exception:  # noqa: BLE001 — describe failed; return the default but do NOT
        # cache a guess: a transient describe failure would otherwise pin the wrong
        # field for the process lifetime (a dense_vector collection would then
        # silently return no results forever). Re-resolve on the next query instead.
        logger.warning("[semantic] could not describe %s; assuming vector field %r "
                       "for THIS call only (not caching)", collection, field)
        return field
    _vector_field_cache[collection] = field  # only cache an AUTHORITATIVE resolution
    return field


def _default_prefix() -> str:
    """The deployment's semantic-collection prefix — must match the MCP's
    `milvus_collection_prefix` (e.g. `mcp` for enterprise, `demo_<org>` for demo
    deployments). Read from env so one deployment value drives all reads."""
    import os
    return os.getenv("SEMANTIC_COLLECTION_PREFIX", MCP_COLLECTION_PREFIX)


# Resolved (dept, source) → actual collection name, cached. The derived name is
# the fast path; when the prefix is misconfigured we fall back to matching on the
# unambiguous `_<dept>_<source>` SUFFIX (which the prefix can never change) so the
# reader is resilient to a wrong/absent SEMANTIC_COLLECTION_PREFIX.
_collection_cache: Dict[str, str] = {}


def _resolve_live_collection(client, dept_id: Optional[str], source_id: str,
                             prefix: str) -> Optional[str]:
    """The collection that actually holds this source's chunks: the derived
    ``<prefix>_<dept>_<source>`` if present, else a suffix match (prefix-agnostic),
    else None. Cached per (dept, source)."""
    key = f"{dept_id or ''}/{source_id}"
    if key in _collection_cache:
        return _collection_cache[key]
    derived = resolve_semantic_collection(dept_id, source_id, prefix=prefix)
    try:
        if client.has_collection(derived):
            _collection_cache[key] = derived
            return derived
        # Fallback: the prefix is wrong/missing. Match on the immutable suffix
        # `_<safe(dept)>_<safe(source)>` — unique per (dept, source).
        dept_seg = _safe_segment(dept_id) if dept_id else "default"
        suffix = f"_{dept_seg}_{_safe_segment(source_id)}"
        candidates = [c for c in (client.list_collections() or []) if c.endswith(suffix)]
        if len(candidates) == 1:
            logger.warning("[semantic] prefix miss for %s — resolved by suffix to %s "
                           "(set SEMANTIC_COLLECTION_PREFIX to silence)", derived, candidates[0])
            _collection_cache[key] = candidates[0]
            return candidates[0]
        if len(candidates) > 1:
            logger.error("[semantic] ambiguous suffix %r matches %s — cannot resolve",
                         suffix, candidates)
        return None
    except Exception:  # noqa: BLE001 — resolution failure ⇒ no context, logged
        logger.exception("[semantic] collection resolution failed for %s", derived)
        return None

# Must match source-mcp-template/config.py `collection_name_semantic`:
#   <prefix>_<safe(dept)>_<safe(source_id)>
# and Citra-Service/dept_library.py `dept_library_collection_name`. The
# ingestion side and this read side MUST derive the SAME name or dept RAG
# content lands in a collection the reader never queries (the acme-power hyphen
# gotcha lives here). Pinned by test_semantic_reader.
MCP_COLLECTION_PREFIX = "mcp"


def _safe_segment(name: str) -> str:
    """Byte-identical to source-mcp-template/config.py `_safe` and
    dept_library._safe_segment: non-alphanumerics → '_', truncate 40, lower."""
    return re.sub(r"[^a-zA-Z0-9]", "_", name or "")[:40].lower()


def resolve_semantic_collection(
    dept_id: Optional[str], source_id: str, *, prefix: str = MCP_COLLECTION_PREFIX,
) -> str:
    """The Milvus collection a semantic source's chunks live in:
    ``<prefix>_<dept>_<source_id>`` — derived EXACTLY as the MCP + VectorSink +
    SOP-Library ingestion derive it, so the reader queries where the content was
    written. ``source_id`` is the catalogued semantic source's id (already
    dept-unique for SOP libraries, e.g. ``sop_library_operations``)."""
    if not source_id:
        raise ValueError("source_id is required to resolve a semantic collection")
    dept_seg = _safe_segment(dept_id) if dept_id else "default"
    return f"{_safe_segment(prefix) or 'mcp'}_{dept_seg}_{_safe_segment(source_id)}"


# ---------------------------------------------------------------------------
# Milvus boolean-filter builder (pure)
# ---------------------------------------------------------------------------

# Fields a caller may filter a dept collection by (metadata stored on chunks).
# Restricted to a known allowlist so a caller can never inject arbitrary Milvus
# expression syntax through a filter key.
_ALLOWED_FILTER_FIELDS = frozenset({
    # VectorSink dept-collection metadata (verified live) …
    "dept", "source_id", "doc_type", "industry", "page", "org_id", "tag",
    # doc_path scopes to ONE document — the key the document-fetch path filters on.
    "doc_path",
    # … plus the SOP-Library ingestion metadata (folder-scoped uploads).
    "folder_id", "department", "document_id", "classification",
})


def _quote(value: Any) -> str:
    """Safely quote a scalar for a Milvus string-equality expression: escape
    embedded quotes/backslashes so a value can't break out of the literal."""
    s = str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def build_filter_expr(filters: Optional[Dict[str, Any]]) -> str:
    """Compose a Milvus boolean expression from a filters dict — only allowlisted
    fields, values safely quoted. A list value becomes an ``in [...]`` clause; a
    scalar becomes ``field == "v"``. Unknown fields are dropped (they cannot be
    smuggled into the expression). Returns '' when nothing to filter."""
    if not filters:
        return ""
    parts: List[str] = []
    for field, value in filters.items():
        if field not in _ALLOWED_FILTER_FIELDS or value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            vals = [v for v in value if v is not None]
            if not vals:
                continue
            parts.append(f"{field} in [{', '.join(_quote(v) for v in vals)}]")
        else:
            parts.append(f"{field} == {_quote(value)}")
    return " and ".join(parts)


def scope_expr(*, source_id: str, dept_id: Optional[str] = None,
               org_id: Optional[str] = None) -> str:
    """Server-authoritative data-layer isolation, ANDed into EVERY semantic Milvus
    query. A read is bounded to the source's own ``source_id`` + ``dept`` + ``org_id``,
    so — even if authz is ever loose or collection resolution lands on the wrong
    collection — a caller can NEVER receive another dept's / org's chunks. `dept`
    covers the VectorSink schema, `department` the SOP-Library schema (OR, so one
    expr fits both). Values are quote-escaped (injection-safe). Only clauses with a
    value are emitted, so a caller that can't supply org/dept still gets source_id
    isolation."""
    parts: List[str] = []
    if source_id:
        parts.append(f"source_id == {_quote(source_id)}")
    if dept_id:
        # Every ingestion path (VectorSink + the shared dept-library store) writes
        # `dept`. Do NOT reference `department`: a strict (non-dynamic) collection
        # — like the purpose-built shared dept-library collection — rejects the
        # whole query with "field department not exist", returning zero hits.
        parts.append(f"dept == {_quote(dept_id)}")
    if org_id:
        parts.append(f"org_id == {_quote(org_id)}")
    return " and ".join(parts)


def _and_exprs(*exprs: str) -> str:
    """AND together the non-empty Milvus expressions."""
    return " and ".join(e for e in exprs if e)


# ---------------------------------------------------------------------------
# Authorization (pure)
# ---------------------------------------------------------------------------


def can_query_semantic(
    *, roles: List[str], user_dept_ids: List[str], dept_id: Optional[str],
    public_within_org: bool = False, roles_allowed: Optional[List[str]] = None,
) -> bool:
    """May this caller query a semantic source scoped to ``dept_id``?
    org_admin / super_admin always; otherwise the source's ``roles_allowed``
    must admit the caller AND either the corpus is ``public_within_org`` (or has
    no dept) or the caller belongs to the dept (reads are dept‑public, per the
    SOP Library model).

    ``public_within_org`` and ``roles_allowed`` are the source's AUTHORITATIVE
    visibility — resolved by the endpoint from discovery's /tools/source-scope,
    never taken from the caller.

    ``roles_allowed`` was previously ignored here: the dept-MCP hard-gates on it
    for STRUCTURED reads (auth.py:155), and sources.json documents it as "roles
    that can query/read this source", but the semantic path checked only dept
    membership. A plain ``user`` in the owning dept could therefore POST
    /semantic/search with any source_id and read a corpus authored
    roles_allowed: ["dept_admin","org_admin"] — /tools/available hid it from
    their routing, but the endpoint takes source_id from the request body.
    Empty/None ⇒ no role restriction (the sources.json default is ["user"]).
    """
    rset = {str(r).lower() for r in (roles or [])}
    if rset & {"org_admin", "super_admin"}:
        return True
    allowed = {str(r).lower() for r in (roles_allowed or [])}
    if allowed and not (rset & allowed):
        return False
    if public_within_org or not dept_id:
        return True
    return dept_id in set(user_dept_ids or [])


# ---------------------------------------------------------------------------
# Result shaping (pure)
# ---------------------------------------------------------------------------


def shape_chunk(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise one Milvus search hit into the standard chunk shape the
    consumers expect: ``{text, score, metadata}``. Tolerates both the nested
    ``{"entity": {...}}`` form and a flat hit; pulls ``text`` out of metadata."""
    entity = hit.get("entity") if isinstance(hit.get("entity"), dict) else hit
    entity = entity or {}
    text = str(entity.get("text") or "")
    score = hit.get("distance", hit.get("score"))
    # Drop the text (lifted out) and the raw vector (`output_fields=["*"]` returns
    # the 768-float embedding, which must never ship to a consumer).
    meta = {k: v for k, v in entity.items() if k not in ("text", "vector", "dense_vector")}
    if "chunk_id" not in meta:
        cid = hit.get("id") or hit.get("primary_key")
        if cid is not None:
            meta["chunk_id"] = cid
    return {"text": text,
            "score": float(score) if isinstance(score, (int, float)) else None,
            "metadata": meta}


# ---------------------------------------------------------------------------
# The reader (live-infra — reuses Citra-Service's embed / Milvus / reranker)
# ---------------------------------------------------------------------------


def _pick_collection(client, *, dept_id, source_id, collection, prefix):
    """Resolve the collection to read. An explicit name (the MCP's published
    ``rag_collection``) is authoritative — used directly when it exists. Falls
    back to deriving ``<prefix>_<dept>_<source>`` (with suffix match) only if the
    explicit name is absent, so a stale/wrong published name degrades to a warning
    + correct result instead of zero chunks."""
    if collection and not client.has_collection(collection):
        logger.warning("[semantic] published collection %r absent for source=%s "
                       "dept=%s — deriving from prefix instead", collection, source_id, dept_id)
        collection = None
    if not collection:
        collection = _resolve_live_collection(
            client, dept_id, source_id, prefix or _default_prefix())
    return collection


def _doc_order_key(chunk: Dict[str, Any]):
    """Best-effort section order for a document's chunks. VectorSink primary ids
    are document-ordered (``<slug>_p<page>_c<idx>``) — parse page+idx (so ``_c10``
    sorts after ``_c2``); fall back to (page, id-string).

    The key is ALWAYS ``(int, str)`` so a mixed batch (some ids match, some don't)
    never compares ``int`` vs ``str`` on the tiebreaker → no ``TypeError`` during
    sort (which runs outside the caller's try/except and would 500 the endpoint).
    Matched ids zero-pad the chunk index so the string tiebreaker still orders
    numerically."""
    meta = chunk.get("metadata") or {}
    cid = str(meta.get("chunk_id") or "")
    m = re.search(r"_p(\d+)_c(\d+)$", cid)
    if m:
        return (int(m.group(1)), f"{int(m.group(2)):09d}")
    try:
        page = int(meta.get("page") or 0)
    except (TypeError, ValueError):
        page = 0
    return (page, cid)


async def fetch_document(
    *,
    source_id: str,
    dept_id: Optional[str],
    doc_path: str,
    org_id: Optional[str] = None,
    collection: Optional[str] = None,
    prefix: Optional[str] = None,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """Return EVERY chunk of ONE document (by ``doc_path``), ordered — a metadata
    fetch, NOT a vector search.

    For 'read / summarize the WHOLE document' intents that per-chunk top-k
    retrieval can't satisfy: neighbouring documents' chunks out-rank the target
    document's own later sections, so a single semantic query never reassembles
    the full doc (the DT-Failure-SOP case — intro chunk returned, sections 3-8
    crowded out by the theft / disconnection SOPs). Scoping to one ``doc_path``
    returns its complete, ordered content instead.

    Loud-but-empty on infra failure (a fetch miss must never 500 the consumer)."""
    if not (doc_path or "").strip() or not source_id:
        return []
    dp = doc_path.strip()
    try:
        from config.milvus_config import get_milvus_client
        client = get_milvus_client()
        collection = _pick_collection(
            client, dept_id=dept_id, source_id=source_id,
            collection=collection, prefix=prefix)
        if not collection:
            logger.warning("[semantic] no collection for source=%s dept=%s (doc fetch)",
                           source_id, dept_id)
            return []
        # Page through ALL of the document's chunks — a whole-document read must
        # not be clipped to a small display top_k (that would silently drop later
        # sections, the exact bug this feature fixes). Milvus query() returns an
        # unordered window; we sort afterwards. The scope filter (source/dept/org)
        # bounds the read to the caller's own data — a data-layer isolation guard.
        expr = _and_exprs(
            f"doc_path == {_quote(dp)}",       # _quote escapes → injection-safe
            scope_expr(source_id=source_id, dept_id=dept_id, org_id=org_id),
        )
        cap = max(1, int(limit))
        _PAGE = 500
        rows: List[Dict[str, Any]] = []
        offset = 0
        while len(rows) < cap:
            batch = client.query(
                collection_name=collection,
                filter=expr,
                output_fields=_OUTPUT_FIELDS,
                limit=min(_PAGE, cap - len(rows)),
                offset=offset,
            )
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += len(batch)
        if len(rows) >= cap:
            logger.warning("[semantic] doc fetch hit cap %d for source=%s doc_path=%s — "
                           "document may be truncated", cap, source_id, dp)
    except Exception:  # noqa: BLE001 — vector store down ⇒ no context, never 500
        logger.exception("[semantic] doc fetch failed for source=%s doc_path=%s", source_id, dp)
        return []
    chunks = [shape_chunk(r) for r in rows if isinstance(r, dict)]
    chunks = [c for c in chunks if c["text"]]
    chunks.sort(key=_doc_order_key)
    logger.info("[semantic] doc fetch source=%s doc_path=%s → %d chunk(s)",
                source_id, dp, len(chunks))
    return chunks


async def semantic_search(
    *,
    source_id: str,
    dept_id: Optional[str],
    query: str,
    top_k: int = 10,
    filters: Optional[Dict[str, Any]] = None,
    min_cosine: float = _DEFAULT_MIN_COSINE,
    prefix: Optional[str] = None,
    collection: Optional[str] = None,
    org_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search a semantic source's dept collection and return ranked chunks.

    Generalizes the personal-vault RAG: embed the query (``utils.embed_text``),
    dense-search the PARAMETERIZED collection via the shared MilvusClient
    (`<prefix>_<dept>_<source>`), apply the COSINE floor, then rerank
    (`reranker.rerank`) when enabled. This is the one platform-side RAG path —
    the MCP does no RAG.

    Live-infra: the embed/Milvus/rerank calls need a running stack, so this is
    covered by a smoke test, not unit tests. Loud-but-empty on infra failure —
    a RAG miss must never 500 the consumer; it degrades to no context, logged."""
    if not (query or "").strip() or not source_id:
        return []
    # Scope filter (source/dept/org) ANDed in for data-layer isolation — see scope_expr.
    expr = _and_exprs(
        scope_expr(source_id=source_id, dept_id=dept_id, org_id=org_id),
        build_filter_expr(filters),
    )

    try:
        from utils import embed_text
        vector = await embed_text(query, task_type="RETRIEVAL_QUERY")
    except Exception:  # noqa: BLE001 — embedding down ⇒ no context, never 500
        logger.exception("[semantic] embed failed for source=%s dept=%s", source_id, dept_id)
        return []

    # Over-fetch when reranking so the cross-encoder has candidates to reorder.
    try:
        from reranker import get_top_k_multiplier, is_reranker_enabled, rerank
        rerank_on = is_reranker_enabled()
        fetch_k = top_k * (get_top_k_multiplier() if rerank_on else 1)
    except Exception:  # noqa: BLE001 — reranker optional; fall back to dense only
        rerank_on, fetch_k, rerank = False, top_k, None

    # NB: do NOT rebind `collection` to None here — that would discard the caller's
    # explicit rag_collection (e.g. the shared `mcp_dept_libraries`) and force a
    # per-dept derivation that doesn't exist ⇒ "no collection" / zero hits. The
    # parameter is already bound, so the except's log ref below is safe regardless.
    try:
        from config.milvus_config import get_milvus_client
        client = get_milvus_client()
        collection = _pick_collection(
            client, dept_id=dept_id, source_id=source_id,
            collection=collection, prefix=prefix)
        if not collection:
            logger.warning("[semantic] no collection for source=%s dept=%s "
                           "(no ingested content?)", source_id, dept_id)
            return []
        raw = client.search(
            collection_name=collection,
            data=[vector],
            anns_field=_resolve_vector_field(client, collection),
            search_params={"metric_type": "COSINE"},
            limit=max(1, int(fetch_k)),
            output_fields=_OUTPUT_FIELDS,
            filter=expr or None,
        )
    except Exception:  # noqa: BLE001 — vector store down ⇒ no context, never 500
        logger.exception("[semantic] Milvus search failed on %s", collection)
        return []

    hits = raw[0] if raw else []
    chunks = [shape_chunk(h) for h in hits]
    # COSINE floor BEFORE rerank (the reranker's score band is different + smaller)
    chunks = [c for c in chunks if c["text"] and (c["score"] is None or c["score"] >= min_cosine)]

    if rerank_on and rerank is not None and chunks:
        try:
            return list(rerank(query, chunks, top_k))
        except Exception:  # noqa: BLE001 — rerank failure ⇒ dense order, logged
            logger.exception("[semantic] rerank failed on %s; using dense order", collection)

    chunks.sort(key=lambda c: (c["score"] if c["score"] is not None else -1.0), reverse=True)
    return chunks[:top_k]
