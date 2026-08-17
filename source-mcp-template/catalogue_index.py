# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Catalogue index — dataset selection
====================================
Resolves a free-text NL question (and optional hints) to one or more
catalogue dataset documents. This is the *replacement* for the old
"caller picks a source_id; we route by source.type" flow.

Three resolution paths in priority order:

  1. Caller passes ``dataset_ids[]`` — load each from the in-memory
     catalogue map. Strict — unknown ids raise.
  2. Caller passes ``source_id`` — enumerate every dataset advertised
     under that source's ``catalogue.datasets[]`` block.
  3. Neither hint given — cosine-rank every dataset's signature
     (description + dataset_id + column names) against the question
     embedding, optionally rerank via the configured reranker service,
     and return the top ``max_results``. Mirrors the way action-chat
     ranks dept-MCPs (``discovery.search``). Fails open: returns
     enumeration order if embeddings or reranker are unavailable.

Schema and sample rows live on the catalogue document itself (workflow
builder + data-discovery-service crawl populate them). This module never
fetches schema — it only *selects datasets*. The downstream planners
(``planners.nl_to_sql`` etc.) read columns / sample_rows / relationships
straight off the dataset dict returned here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — derive datasets from the in-memory source map
# ---------------------------------------------------------------------------


def _datasets_from_source(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return catalogue datasets for one source, with ``source_id`` injected.

    Mirrors ``catalogue._datasets_for``: the top-level ``datasets[]`` on the
    dept_source doc wins; the nested ``catalogue.datasets[]`` block is the
    legacy fallback. Reading only the nested form (the old behaviour here)
    meant ``/query`` saw zero datasets for any source that declares the
    top-level form — so it fell through to semantic search and a structured
    SQL source silently returned no rows.
    """
    datasets = (
        list(source.get("datasets") or [])
        or list((source.get("catalogue") or {}).get("datasets") or [])
    )
    out: List[Dict[str, Any]] = []
    for ds in datasets:
        ds_copy = dict(ds)
        ds_copy.setdefault("source_id", source.get("source_id"))
        out.append(ds_copy)
    return out


def _all_datasets() -> List[Dict[str, Any]]:
    """Flatten every dataset across every loaded source."""
    from router import _sources

    out: List[Dict[str, Any]] = []
    for src in _sources.values():
        out.extend(_datasets_from_source(src))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def select_datasets(
    question: str,
    *,
    dataset_ids: Optional[List[str]] = None,
    source_id: Optional[str] = None,
    kinds: Optional[List[str]] = None,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """Resolve a question + hints to a list of catalogue dataset documents.

    Returns datasets ordered by relevance (vector path) or by catalogue
    declaration order (hint paths). The returned dicts always carry
    ``source_id`` and ``id`` so callers can pass them straight to
    ``query_engine.execute``.
    """
    if dataset_ids:
        resolved = _resolve_explicit(dataset_ids)
    elif source_id:
        from router import _sources
        src = _sources.get(source_id)
        if not src:
            logger.warning(f"[CATALOGUE] Unknown source_id {source_id!r}")
            return []
        source_datasets = _datasets_from_source(src)
        # Rank WITHIN the source when it has more tables than the cap, so a
        # large source (e.g. 100 tables) doesn't flood the NL→query prompt.
        # Small sources skip ranking (cheap, deterministic). Fails open to the
        # full list if embeddings/reranker are unavailable.
        if len(source_datasets) > max_results:
            logger.info(
                "[CATALOGUE] source %s has %d datasets > cap %d — ranking",
                source_id, len(source_datasets), max_results,
            )
            resolved = await _vector_search(
                question, max_results=max_results, candidates=source_datasets,
                scope=source_id,
            )
        else:
            resolved = source_datasets
    else:
        resolved = await _vector_search(question, max_results=max_results, scope="ALL")

    if kinds:
        kind_set = set(kinds)
        resolved = [d for d in resolved if d.get("kind") in kind_set]

    return resolved


def _resolve_explicit(dataset_ids: List[str]) -> List[Dict[str, Any]]:
    """Look each id up in the in-memory catalogue; an unknown id is skipped
    with a warning.

    Note: this is intentionally NOT fail-loud. An id may be momentarily absent
    during a registry refresh, and callers (e.g. the smart-app runtime) pin ids
    they expect to exist; raising here would turn a transient gap into a hard
    query failure. The warning surfaces a real typo without breaking the query."""
    everything = _all_datasets()
    by_id = {d.get("id"): d for d in everything}
    out: List[Dict[str, Any]] = []
    for did in dataset_ids:
        ds = by_id.get(did)
        if ds is None:
            logger.warning("[CATALOGUE] dataset_id %r not in any loaded source — skipping", did)
            continue
        out.append(ds)
    return out


# ---------------------------------------------------------------------------
# Vector search — cosine + reranker over the in-memory catalogue
# ---------------------------------------------------------------------------
#
# At expected scale (10²–10³ datasets per org), running cosine in-process
# over the catalogue is faster, simpler, and has no second source of truth
# than maintaining a separate Milvus collection. This mirrors the pattern
# action-chat uses to rank dept-MCPs (``discovery.search``).


def _signature_text(ds: Dict[str, Any]) -> str:
    """NL signature for a dataset — what we embed and what the reranker sees."""
    cols = ds.get("columns") or []
    col_names = ", ".join(
        str(c.get("name", "?")) for c in cols if isinstance(c, dict)
    )
    desc = (ds.get("description") or "").strip()
    ds_id = str(ds.get("id") or ds.get("dataset_id") or ds.get("name") or "")
    return f"{desc}\n{ds_id}\nColumns: {col_names}"


def _keyword_forced(question: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Datasets the question names by an EXACT source/table keyword.

    A pin-then-fill safeguard for ``_vector_search``: when the question
    mentions a dataset's id, name, physical_name, or source_id as a whole
    word, that dataset is relevant by construction and must NEVER be dropped
    by the ``max_results`` cap — even if the (possibly degraded) ranker
    scores it low. Whole-word match only, so a substring collision (``order``
    inside ``reorder_log``) doesn't force an unrelated table."""
    import re

    forced: List[Dict[str, Any]] = []
    q = (question or "").lower()
    if not q:
        return forced
    for ds in candidates:
        toks = {
            str(ds.get("id") or "").lower(),
            str(ds.get("name") or "").lower(),
            str(ds.get("physical_name") or "").lower(),
            str(ds.get("source_id") or "").lower(),
        }
        toks.discard("")
        for tok in toks:
            if re.search(r"\b" + re.escape(tok) + r"\b", q):
                forced.append(ds)
                break
    return forced


def _pin_then_fill(
    forced: List[Dict[str, Any]],
    ranked: List[Dict[str, Any]],
    max_results: int,
) -> List[Dict[str, Any]]:
    """Keep every forced match, then fill remaining slots by rank order.

    Forced matches (pinned ``dataset_ids`` / exact keyword hits) are returned
    first and are never dropped by the cap; the rest of ``max_results`` is
    filled from ``ranked`` (best-first), skipping anything already pinned.
    Identity is by dataset id (falling back to object identity)."""
    def _key(d: Dict[str, Any]):
        return d.get("id") or id(d)

    out: List[Dict[str, Any]] = []
    seen = set()
    for d in forced:
        k = _key(d)
        if k not in seen:
            seen.add(k)
            out.append(d)
    for d in ranked:
        if len(out) >= max_results:
            break
        k = _key(d)
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    import math
    return float(dot / (math.sqrt(na) * math.sqrt(nb)))


def _selection_cache_lookup(
    scope: str, candidates: List[Dict[str, Any]], question: str, max_results: int,
) -> Optional[List[Dict[str, Any]]]:
    """Return cached selection resolved against the CURRENT candidates, or None.

    The cache stores an ordered dataset-id list (a routing decision, not data);
    we re-resolve those ids to the live dataset dicts so rows are always fetched
    fresh. Keyed by a schema fingerprint of the candidate set, so any schema /
    registry change yields a different key and a stale selection can't resurface.
    A hit also shields a transient embedding-service outage (we serve the last
    healthy ranking instead of degrading to enumeration order)."""
    try:
        import plan_cache
        fp = plan_cache.schema_fingerprint(candidates)
        ids = plan_cache.get_dataset_selection(scope, fp, question, max_results)
        if not ids:
            return None
        by_id = {str(d.get("id")): d for d in candidates}
        out = [by_id[i] for i in ids if i in by_id]
        return out or None
    except Exception:  # noqa: BLE001 — cache is best-effort
        return None


def _selection_cache_store(
    scope: str, candidates: List[Dict[str, Any]], question: str,
    max_results: int, result: List[Dict[str, Any]],
) -> None:
    """Persist a HEALTHY ranked selection (ordered ids). Never called from a
    degraded fallback path — we must not cache enumeration order as if it were a
    real ranking."""
    try:
        import plan_cache
        fp = plan_cache.schema_fingerprint(candidates)
        plan_cache.set_dataset_selection(
            scope, fp, question, max_results, [str(d.get("id")) for d in result],
        )
    except Exception:  # noqa: BLE001 — cache is best-effort
        pass


async def _vector_search(
    question: str,
    *,
    max_results: int,
    candidates: Optional[List[Dict[str, Any]]] = None,
    scope: str = "ALL",
) -> List[Dict[str, Any]]:
    """Rank catalogue datasets by relevance to ``question``.

    ``candidates`` defaults to every dataset across every loaded source (the
    no-hint path). Pass a subset — e.g. one source's datasets — to rank
    WITHIN that source (so a source with many tables doesn't dump them all
    into the planner prompt). ``scope`` keys the selection cache ("ALL" or a
    source_id) so a within-source ranking never collides with the global one.

    Pipeline (mirrors action-chat's ``discovery.search`` for dept-MCPs):

      1. Embed the query + each candidate's signature text (description +
         dataset_id + column names) using ``rag.embed``.
      2. Cosine-rank, take top ``max_results * 3``.
      3. If a reranker service is configured, rerank by signature text.
      4. Return the top ``max_results`` full catalogue dicts.

    Fails open: if embeddings or the reranker are unavailable we fall back
    to enumeration order so ``/query`` stays usable in dev / CI / partial
    outage.
    """
    if candidates is None:
        candidates = _all_datasets()
    if not candidates:
        return []

    # Dataset-selection cache: reuse a prior HEALTHY ranking for this
    # (scope, schema, question, cap) — skips the embed+cosine(+rerank) entirely.
    cached = _selection_cache_lookup(scope, candidates, question, max_results)
    if cached is not None:
        return cached

    # Datasets the question names by an exact source/table keyword are pinned:
    # they survive the cap even when ranking degrades (see _pin_then_fill).
    forced = _keyword_forced(question, candidates)

    try:
        from rag.embed import embed_query, embed_texts_direct
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[CATALOGUE] RANKING DEGRADED — embed module unavailable (%s); "
            "returning enumeration order capped at %d (relevant table may be "
            "dropped). Forced/keyword matches are pinned.", exc, max_results,
        )
        return _pin_then_fill(forced, candidates, max_results)

    try:
        query_vec = await embed_query(question)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[CATALOGUE] RANKING DEGRADED — query embedding failed (%s); "
            "returning enumeration order capped at %d (relevant table may be "
            "dropped). Forced/keyword matches are pinned.", exc, max_results,
        )
        return _pin_then_fill(forced, candidates, max_results)

    # Use precomputed dataset embeddings (written at INGESTION onto the
    # dept_sources dataset block) when present, and only embed the datasets
    # that lack one. This lets the MCP rank even a large source without
    # re-embedding every signature on every query. A precomputed vector is
    # only trusted when its dim matches the query embedding (guards against an
    # ingestion/query embedding-model mismatch — falls back to on-the-fly).
    sig_vecs: List[Optional[List[float]]] = []
    missing_idx: List[int] = []
    missing_texts: List[str] = []
    for i, d in enumerate(candidates):
        emb = d.get("embedding")
        if isinstance(emb, list) and emb and len(emb) == len(query_vec):
            sig_vecs.append(emb)
        else:
            sig_vecs.append(None)
            missing_idx.append(i)
            missing_texts.append(_signature_text(d))
    if missing_texts:
        try:
            fresh = await embed_texts_direct(missing_texts)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[CATALOGUE] RANKING DEGRADED — catalogue embedding failed (%s); "
                "returning enumeration order capped at %d (relevant table may be "
                "dropped). Forced/keyword matches are pinned.", exc, max_results,
            )
            return _pin_then_fill(forced, candidates, max_results)
        for idx, vec in zip(missing_idx, fresh):
            sig_vecs[idx] = vec

    ranked: List[tuple] = []
    for ds, vec in zip(candidates, sig_vecs):
        ranked.append((_cosine(query_vec, vec), ds))
    ranked.sort(key=lambda r: r[0], reverse=True)

    short = ranked[: max(max_results * 3, max_results)]
    if not short:
        return []

    # Optional reranker pass over the signature texts.
    try:
        cfg_mod = __import__("config")
        cfg = cfg_mod.get_settings()
        reranker_url = (getattr(cfg, "reranker_url", "") or "").rstrip("/")
    except Exception:
        reranker_url = ""

    if reranker_url and len(short) > 1:
        try:
            import httpx

            chunks = []
            for i, (score, ds) in enumerate(short):
                chunks.append({
                    "id": str(i),
                    "text": _signature_text(ds),
                    "score": float(score),
                    "metadata": {
                        "dataset_id": ds.get("id"),
                        "source_id": ds.get("source_id"),
                        "kind": ds.get("kind"),
                    },
                })
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{reranker_url}/rerank",
                    json={"query": question, "chunks": chunks, "top_k": max_results},
                )
                resp.raise_for_status()
                payload = resp.json() or {}
            reranked = payload.get("reranked_chunks") or []
            ordered: List[Dict[str, Any]] = []
            for rr in reranked:
                try:
                    idx = int(rr.get("id"))
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(short):
                    ordered.append(short[idx][1])
            if ordered:
                # Pin forced/keyword matches so the cap can't drop a table the
                # question explicitly named, then fill by rerank order.
                result = _pin_then_fill(forced, ordered, max_results)
                _selection_cache_store(scope, candidates, question, max_results, result)
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CATALOGUE] reranker failed: {exc}; using cosine order")

    # Pin forced/keyword matches, then fill remaining slots by cosine rank.
    result = _pin_then_fill(forced, [ds for _, ds in short], max_results)
    _selection_cache_store(scope, candidates, question, max_results, result)
    return result
