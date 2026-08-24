# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""NL→SQL plan cache + few-shot store (pluggable backend, fail-open).

Backend is Redis (platform / multi-replica) OR an in-process LRU (customer-side
single-instance MCP with no Redis) — selected in ``cache_backend.py``. Every
function here is backend-agnostic: it calls the same small Redis-subset API on
whatever ``_get_redis()`` returns. Customer-side the MCP ships no ``REDIS_HOST``,
so this transparently uses the in-process cache instead of failing open to *no
cache* (which would make every NL→SQL query pay full planner latency).

Two layers, both keyed per (source_id, schema fingerprint) so a schema change
auto-invalidates:

  Layer 1 — PLAN CACHE: a normalized NL question → the SQL that answered it.
            A hit lets the caller SKIP the planner LLM entirely and just execute
            the cached SQL — the big latency win for repeated/canonical queries.

  Layer 2 — FEW-SHOT STORE: a short ring of recent successful (question, SQL)
            pairs per source, injected into the planner prompt on a cache MISS
            so the model gets the schema's real patterns right first try (fewer
            self-correction retries).

Mirrors the Redis usage in Citra-Service/services/file_relevance_scorer.py:
sync ``redis`` client from REDIS_* env, ``decode_responses``, ``socket_timeout``,
and EVERY path wrapped so Redis being down/absent degrades to "no cache" — it
never breaks a query.
"""
import os
import json
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PLAN_TTL = int(os.getenv("PLAN_CACHE_TTL", "86400"))        # seconds (24h); SLIDING — refreshed on every hit
_EXAMPLES_N = int(os.getenv("PLAN_CACHE_EXAMPLES", "5"))       # few-shot K injected
_POOL = int(os.getenv("PLAN_CACHE_POOL", "100"))              # Redis ring reranked over
_EXAMPLES_TTL = int(os.getenv("PLAN_CACHE_EXAMPLES_TTL", "86400"))
_RERANK_TIMEOUT = float(os.getenv("PLAN_CACHE_RERANK_TIMEOUT", "5"))


def _reranker_url() -> str:
    return (os.getenv("RERANKER_URL", "") or "").rstrip("/")


def _enabled() -> bool:
    return os.getenv("PLAN_CACHE_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


# Module-level cache-client singleton (Redis OR in-process — see cache_backend).
# Building the client once and sharing it avoids per-call setup; the Redis client
# is connection-pooled + thread-safe and the in-process client is lock-guarded,
# so one shared instance serves the whole process. ``_redis_failed`` makes a
# transport failure visible exactly once (rate-limited).
_redis_client = None
_redis_init_done = False
_redis_failed = False
_cache_backend = "uninit"


def _get_redis():
    """Memoized cache client — Redis or the in-process LRU, per ``cache_backend``.
    Never raises; the cache is best-effort. (Name kept for back-compat: callers
    and tests treat the return value as a Redis-subset client.)"""
    global _redis_client, _redis_init_done, _cache_backend
    if _redis_init_done:
        return _redis_client
    try:
        from cache_backend import make_cache_client

        _redis_client, _cache_backend = make_cache_client(logger)
    except Exception as exc:  # noqa: BLE001 — cache is best-effort, never block a query
        _redis_client, _cache_backend = None, "none"
        _warn_redis_down("backend init", exc)
    _redis_init_done = True
    return _redis_client


def _warn_redis_down(op: str, exc: Exception) -> None:
    """Log the FIRST cache-backend failure at WARNING, then stay quiet — a
    misconfigured backend must not silently disable the cache forever (every
    query would pay full planner latency with no signal), but it also must not
    spam the log on every call. Fail-open: the caller still degrades to 'no
    cache'."""
    global _redis_failed
    if not _redis_failed:
        _redis_failed = True
        logger.warning(
            "plan_cache: cache %s failed (%s) — plan cache DISABLED, every query "
            "now pays full planner latency; this is logged once.", op, exc,
        )


def _norm(question: str) -> str:
    """Normalize an NL question for cache-keying: lowercase, collapse internal
    whitespace, strip surrounding punctuation/space. Keeps phrasing variants
    from missing an otherwise-identical query."""
    q = " ".join((question or "").lower().split())
    return q.strip(" ?.!;:")


def schema_fingerprint(datasets: List[Dict[str, Any]]) -> str:
    """Short hash of the datasets' table+column shape — a SQL plan is only valid
    for the schema it was generated against, so this is part of every key. A
    column add/drop/rename/retype changes the fingerprint → old plans never
    resurface.

    Keyed on, per dataset: the dataset id PLUS each column's (name, type). Two
    things this guards against:
      • the same question text scoped to a DIFFERENT set of tables collides to
        the same plan key (wrong-table replay) — the dataset ids make the key
        table-set-specific; and
      • a column whose NAME is unchanged but whose TYPE changed (e.g. text→int)
        reusing a stale plan that no longer type-checks — the column types make
        the key schema-exact."""
    shape = []
    for ds in (datasets or []):
        cols = []
        for c in (ds.get("columns") or []):
            if isinstance(c, dict):
                name = c.get("name")
                ctype = str(c.get("type") or "")
            else:
                name = str(c)
                ctype = ""
            if name:
                cols.append([name, ctype])
        shape.append([ds.get("id"), sorted(cols)])
    raw = json.dumps(sorted(shape, key=lambda x: str(x[0])), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _plan_key(source_id: str, fp: str, question: str) -> str:
    h = hashlib.sha256(f"{source_id}|{fp}|{_norm(question)}".encode()).hexdigest()
    return f"mcp_plan:{h}"


def _examples_key(source_id: str, fp: str) -> str:
    return f"mcp_plan_ex:{source_id}:{fp}"


def get_plan(source_id: Optional[str], fp: str, question: str) -> Optional[str]:
    """Cached SQL for this exact (normalized) question against this schema, or None.

    SLIDING TTL: a hit refreshes the key's expiry, so a query that keeps being
    asked never expires and a genuinely hot plan never re-plans. Safe against
    schema drift — a schema change yields a new fingerprint → a new key, and the
    stale one simply ages out."""
    if not (_enabled() and source_id and question):
        return None
    try:
        r = _get_redis()
        if r is None:
            return None
        key = _plan_key(source_id, fp, question)
        sql = r.get(key)
        if sql:
            r.expire(key, _PLAN_TTL)   # slide the window on every hit
            return sql
        return None
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("get_plan", exc)
        return None


def set_plan(source_id: Optional[str], fp: str, question: str, sql: str) -> None:
    """Remember the SQL that successfully answered this question."""
    if not (_enabled() and source_id and question and sql):
        return
    try:
        r = _get_redis()
        if r is None:
            return
        r.setex(_plan_key(source_id, fp, question), _PLAN_TTL, sql)
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("set_plan", exc)


def push_example(source_id: Optional[str], fp: str, question: str, sql: str) -> None:
    """Add a successful (question, SQL) pair to the per-source few-shot ring
    (newest first, capped at PLAN_CACHE_EXAMPLES)."""
    if not (_enabled() and source_id and question and sql):
        return
    try:
        r = _get_redis()
        if r is None:
            return
        key = _examples_key(source_id, fp)
        r.lpush(key, json.dumps({"q": question.strip(), "sql": sql.strip()}))
        r.ltrim(key, 0, _POOL - 1)          # keep a POOL to rerank over, not just K
        r.expire(key, _EXAMPLES_TTL)
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("push_example", exc)


def _recent_pool(source_id: Optional[str], fp: str, limit: int) -> List[Tuple[str, str]]:
    """Raw recency pool (newest first) from the per-source Redis ring."""
    if not (_enabled() and source_id):
        return []
    try:
        r = _get_redis()
        if r is None:
            return []
        raw = r.lrange(_examples_key(source_id, fp), 0, max(0, limit - 1))
        out: List[Tuple[str, str]] = []
        for item in raw:
            try:
                d = json.loads(item)
                if d.get("q") and d.get("sql"):
                    out.append((d["q"], d["sql"]))
            except Exception:
                continue
        return out
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("recent_pool", exc)
        return []


async def _rerank(question: str, pool: List[Tuple[str, str]], k: int) -> List[Tuple[str, str]]:
    """Score each candidate QUESTION against the incoming question via the
    reranker service (cross-encoder) and return the top-k pairs. Mirrors the
    ``/rerank`` contract used in catalogue_index.py. Raises on transport error
    (caller falls back to recency)."""
    import httpx
    url = _reranker_url()
    chunks = [{"id": str(i), "text": q, "score": 0.0, "metadata": {}}
              for i, (q, _sql) in enumerate(pool)]
    async with httpx.AsyncClient(timeout=_RERANK_TIMEOUT) as client:
        resp = await client.post(
            f"{url}/rerank",
            json={"query": question, "chunks": chunks, "top_k": k},
        )
        resp.raise_for_status()
        payload = resp.json() or {}
    ordered: List[Tuple[str, str]] = []
    for rr in (payload.get("reranked_chunks") or []):
        try:
            idx = int(rr.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(pool):
            ordered.append(pool[idx])
    return ordered[:k] if ordered else pool[:k]


async def get_examples(
    source_id: Optional[str], fp: str, question: str, k: Optional[int] = None,
) -> List[Tuple[str, str]]:
    """Top-K few-shot (question, SQL) pairs MOST SIMILAR to ``question``, scored
    by the reranker over the per-source Redis pool. Fail-open: empty pool → [];
    reranker absent/down or a single-item pool → recency order."""
    k = k or _EXAMPLES_N
    pool = _recent_pool(source_id, fp, _POOL)
    if not pool:
        return []
    if len(pool) <= 1 or not _reranker_url():
        return pool[:k]
    try:
        return await _rerank(question, pool, k)
    except Exception as exc:  # noqa: BLE001 — reranker is best-effort
        logger.warning("plan_cache: reranker failed (%s) — recency order", exc)
        return pool[:k]


def render_examples_block(examples: List[Tuple[str, str]]) -> str:
    """Few-shot block for a planner prompt. Empty string when there are none."""
    if not examples:
        return ""
    lines = ["\nWorked examples for THIS schema (question → SQL that ran clean) — "
             "follow these patterns (casing, filters, joins):"]
    for q, sql in examples:
        lines.append(f"Q: {q}\nSQL: {sql}")
    return "\n".join(lines) + "\n"


# ── Count-probe cache (SIZE ESTIMATE ONLY — never row/aggregate DATA) ──────────
# The planner runs `SELECT count(*)` probes purely to ESTIMATE result size (the
# list-vs-aggregate routing decision). That estimate is reusable; the data the
# user actually sees is ALWAYS re-executed live — we never cache rows or a
# user-facing aggregate. Keyed per (source_id, data_version, normalized count
# SQL):
#   • ``data_version`` is a per-source counter INCR'd on every write
#     (execute_action) — so one write instantly invalidates EVERY stale count
#     for that source with a single op and no key scan.
#   • the short TTL is the safety net for writes that bypass the MCP (straight
#     to the DB), and bounds how stale a size estimate can ever be.
# A stale estimate can at worst pick the wrong route once; it can never return
# stale data — the row path re-fetches CAP+1 live and the over-cap path re-plans
# the aggregate and executes it live.
#
# TTL is 24h and FIXED (not sliding, unlike the plan cache): a write THROUGH the
# MCP busts it instantly via data_version, but a write that bypasses the MCP
# (direct DB / ingestion) is only caught by expiry — so a fixed daily refresh
# bounds how long a hot estimate can drift, even for a constantly-asked query.
_COUNT_TTL = int(os.getenv("COUNT_CACHE_TTL", "86400"))


def _count_enabled() -> bool:
    return os.getenv("COUNT_CACHE_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


def _norm_sql(sql: str) -> str:
    """Collapse whitespace so cosmetically-different but identical count SQL keys
    the same. Identifier case is preserved (quoted identifiers are case-sensitive)."""
    return " ".join((sql or "").split()).strip()


def _dv_key(source_id: str) -> str:
    return f"mcp_dv:{source_id}"


def _count_key(source_id: str, dv: int, sql: str) -> str:
    h = hashlib.sha256(f"{source_id}|{dv}|{_norm_sql(sql)}".encode()).hexdigest()
    return f"mcp_cnt:{h}"


def _inc_count_metric(result: str) -> None:
    """Best-effort Prometheus counter for count-cache outcomes; never raises."""
    try:
        import metrics  # type: ignore
        metrics.inc_count_cache(result)
    except Exception:  # noqa: BLE001
        pass


def get_data_version(source_id: Optional[str]) -> int:
    """Per-source data version (0 when unset / Redis down). Part of every count
    key, so a write — which bumps it — instantly invalidates stale counts."""
    if not source_id:
        return 0
    try:
        r = _get_redis()
        if r is None:
            return 0
        v = r.get(_dv_key(source_id))
        return int(v) if v is not None else 0
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("get_data_version", exc)
        return 0


def bump_data_version(source_id: Optional[str]) -> None:
    """INCR the source's data version — call after a successful WRITE so every
    cached count for the source is invalidated at once. Fail-open."""
    if not source_id:
        return
    try:
        r = _get_redis()
        if r is None:
            return
        r.incr(_dv_key(source_id))
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("bump_data_version", exc)


def get_count(source_id: Optional[str], count_sql: str) -> Optional[int]:
    """Cached integer for this exact count(*) probe, or None on miss.

    SIZE ESTIMATE ONLY — callers use this to decide list-vs-aggregate, never to
    answer a user-facing count (the planner re-executes any user-facing
    aggregate live)."""
    if not (_count_enabled() and source_id and count_sql):
        return None
    try:
        r = _get_redis()
        if r is None:
            return None
        dv = get_data_version(source_id)
        v = r.get(_count_key(source_id, dv, count_sql))
        if v is None:
            _inc_count_metric("miss")
            return None
        _inc_count_metric("hit")
        return int(v)
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("get_count", exc)
        return None


def set_count(source_id: Optional[str], count_sql: str, n: int) -> None:
    """Remember a count(*) probe result (size estimate) under the current data
    version, with a short TTL."""
    if not (_count_enabled() and source_id and count_sql) or not isinstance(n, int):
        return
    try:
        r = _get_redis()
        if r is None:
            return
        dv = get_data_version(source_id)
        r.setex(_count_key(source_id, dv, count_sql), _COUNT_TTL, str(n))
        _inc_count_metric("store")
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("set_count", exc)


# ── Query-embedding cache (§1 — deterministic per model, high reuse) ───────────
# An embedding is a pure function of (model, dimension, text), so the SAME query
# text always maps to the SAME vector — ideal to cache. The dataset-selection
# ranker (catalogue_index) embeds the user's question on every /query; a repeated
# or canonical question then skips the embedding-service round-trip entirely.
# Caches the VECTOR (a model output), never any source data. Fail-open.
_EMB_TTL = int(os.getenv("EMBED_CACHE_TTL", str(7 * 86400)))   # 7d — vectors are stable per model


def _emb_enabled() -> bool:
    return os.getenv("EMBED_CACHE_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


def _emb_key(model: str, dim: int, text: str) -> str:
    h = hashlib.sha256(f"{model}|{dim}|{_norm(text)}".encode()).hexdigest()
    return f"mcp_emb:{h}"


def get_query_embedding(model: str, dim: int, text: str) -> Optional[List[float]]:
    """Cached embedding vector for this (model, dim, normalized text), or None."""
    if not (_emb_enabled() and model and text):
        return None
    try:
        r = _get_redis()
        if r is None:
            return None
        raw = r.get(_emb_key(model, dim, text))
        if not raw:
            return None
        vec = json.loads(raw)
        return vec if isinstance(vec, list) else None
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("get_query_embedding", exc)
        return None


def set_query_embedding(model: str, dim: int, text: str, vec: List[float]) -> None:
    """Remember a query embedding vector."""
    if not (_emb_enabled() and model and text and isinstance(vec, list) and vec):
        return
    try:
        r = _get_redis()
        if r is None:
            return
        r.setex(_emb_key(model, dim, text), _EMB_TTL, json.dumps(vec))
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("set_query_embedding", exc)


# ── Dataset-selection cache (§1 — reuse the ROUTING decision, never data) ──────
# select_datasets ranks the question against every candidate dataset's signature
# (embed + cosine + optional reranker) on every no-hint /query. The OUTPUT is a
# routing decision — an ordered list of dataset IDs — not data; the dataset dicts
# (and the rows behind them) are always re-resolved live from the in-memory
# registry. Keyed per (scope, schema fingerprint of the candidate set, max,
# kinds, normalized question): the fingerprint makes a registry/schema change
# (table add/drop/retype) yield a new key, so a stale selection never resurfaces.
_DSEL_TTL = int(os.getenv("DATASET_SELECT_CACHE_TTL", "3600"))   # 1h


def _dsel_enabled() -> bool:
    return os.getenv("DATASET_SELECT_CACHE_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


def _dsel_key(scope: str, fp: str, question: str, max_results: int, kinds: Optional[List[str]]) -> str:
    ks = ",".join(sorted(kinds)) if kinds else ""
    h = hashlib.sha256(
        f"{scope}|{fp}|{max_results}|{ks}|{_norm(question)}".encode()
    ).hexdigest()
    return f"mcp_dsel:{h}"


def get_dataset_selection(
    scope: str, fp: str, question: str, max_results: int,
    kinds: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """Cached ordered dataset-id list for this question+scope+schema, or None."""
    if not (_dsel_enabled() and question):
        return None
    try:
        r = _get_redis()
        if r is None:
            return None
        raw = r.get(_dsel_key(scope, fp, question, max_results, kinds))
        if not raw:
            return None
        ids = json.loads(raw)
        return [str(i) for i in ids] if isinstance(ids, list) else None
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("get_dataset_selection", exc)
        return None


def set_dataset_selection(
    scope: str, fp: str, question: str, max_results: int,
    dataset_ids: List[str], kinds: Optional[List[str]] = None,
) -> None:
    """Remember the ordered dataset-id selection for this question+scope+schema."""
    if not (_dsel_enabled() and question) or not isinstance(dataset_ids, list):
        return
    try:
        r = _get_redis()
        if r is None:
            return
        r.setex(
            _dsel_key(scope, fp, question, max_results, kinds),
            _DSEL_TTL, json.dumps([str(i) for i in dataset_ids]),
        )
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("set_dataset_selection", exc)


# ── Introspection cache (§1/§2-P3 — SHARED across replicas, not per-process) ───
# describe_dataset live-introspects the source (schema + row-count estimate).
# A per-process TTL cache makes every replica re-introspect on its own schedule
# — a DB herd on mass expiry / scale-out. A Redis layer in front lets ALL
# replicas share one introspection per (source, dataset) within the TTL. Schema
# is caller-independent and stable, so this is safe; fail-open to the in-process
# cache the caller still keeps. Stores schema/metadata only, never row data.
_INTRO_TTL = int(os.getenv("CATALOGUE_INTROSPECT_TTL_S", "300"))


def _intro_key(source_id: str, dataset_id: str) -> str:
    return f"mcp_intro:{source_id}:{dataset_id}"


def get_introspect(source_id: str, dataset_id: str) -> Optional[Dict[str, Any]]:
    """Shared cached introspection dict for (source, dataset), or None."""
    if not (source_id and dataset_id):
        return None
    try:
        r = _get_redis()
        if r is None:
            return None
        raw = r.get(_intro_key(source_id, dataset_id))
        if not raw:
            return None
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("get_introspect", exc)
        return None


def set_introspect(source_id: str, dataset_id: str, intro: Dict[str, Any]) -> None:
    """Remember an introspection result, shared across replicas."""
    if not (source_id and dataset_id and isinstance(intro, dict)):
        return
    try:
        r = _get_redis()
        if r is None:
            return
        r.setex(_intro_key(source_id, dataset_id), _INTRO_TTL, json.dumps(intro, default=str))
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        _warn_redis_down("set_introspect", exc)


# ── Leader lock (§2/P3 — dedup the registration write across instances) ────────
# When N instances (or uvicorn workers) of the SAME MCP start together, every
# one POSTs the identical registration payload — redundant writes + races. A
# short Redis SETNX lock per tool_id lets exactly ONE instance write the
# registration; the rest skip the POST (but still heartbeat liveness). The lock
# carries a TTL and is never explicitly released — it simply expires, so a later
# re-registration (e.g. heartbeat saw a 404) re-contends cleanly.
#
# Fail-OPEN by design (RULE #1 nuance): a coordination lock that can't reach
# Redis must not BLOCK registration — it returns True so EVERY instance registers
# (the pre-existing, correct single-node behaviour). The failure is logged once.
def try_acquire_lock(name: str, ttl_seconds: int) -> bool:
    """Best-effort distributed lock. Returns True if THIS caller won the lock
    (or Redis is unavailable → fail-open, everyone proceeds). Never raises."""
    try:
        r = _get_redis()
        if r is None:
            return True  # fail-open: no Redis ⇒ no coordination ⇒ legacy behaviour
        # SET key NX EX ttl — atomic acquire-or-fail.
        got = r.set(f"mcp_lock:{name}", "1", nx=True, ex=max(1, int(ttl_seconds)))
        return bool(got)
    except Exception as exc:  # noqa: BLE001 — coordination is best-effort
        _warn_redis_down("try_acquire_lock", exc)
        return True  # fail-open
