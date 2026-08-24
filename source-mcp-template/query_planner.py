# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Query planner — catalogue-keyed orchestrator
=============================================
Single entry point for ``POST /query``. Replaces the old
``router.dispatch`` + ``rag.structured_engine.search_structured`` chain
with a catalogue-driven flow that handles every structured backend
(SQL, DuckDB-over-files, OData/SAP, SOQL/Salesforce, REST) plus the
existing semantic vector path through one orchestrator.

Flow
----

  1. ``catalogue_index.select_datasets`` resolves the request into a
     list of catalogue dataset documents using, in priority order,
     ``dataset_ids`` → ``source_id`` → vector search.

  2. Datasets are grouped by ``kind``. Each kind is planned by its own
     NL-to-query module:

         sql       → planners.nl_to_sql.plan          (SELECT string)
         duckdb    → planners.nl_to_duckdb.plan       (SELECT string)
         odata     → planners.nl_to_odata.plan        (dict, stub)
         soql      → planners.nl_to_soql.plan         (string, stub)
         rest      → planners.nl_to_rest.plan         (dict, stub)
         semantic  → no planner — handled inline by rag.semantic_engine

  3. The generated query is handed to ``query_engine.execute(...)`` —
     the same engine ``/run_query`` uses, so prose-fusion answers and
     raw rows come from one execution path.

  4. Rows are formatted into ``ChunkResult`` text (table-style for
     structured kinds; chunk passthrough for semantic) so legacy
     callers — chat, dashboard, presentation — keep their existing
     response shape while moving onto the new architecture.

Backwards compatibility
-----------------------
``QueryRequest`` already requires ``source_id``. The new
``dataset_ids`` field is optional; when omitted the planner enumerates
every catalogued dataset under ``source_id`` (or vector-searches if
the source has no catalogue block). Existing chat / dashboard callers
keep working unchanged while smart-app runtime and the new dashboard
pin specific dataset_ids.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from models import ChunkResult, QueryRequest, QueryResponse, SourceType

logger = logging.getLogger(__name__)


# ── Single-flight (§1 — cache-stampede guard) ──────────────────────────────────
# On a cold key (cold start / TTL expiry) many identical questions arrive at once
# and EACH would hit the planner LLM + DB. Collapse concurrent identical cold
# misses onto ONE in-flight computation per process; followers await the leader's
# result instead of duplicating the expensive planning. Keyed per
# (source, schema fingerprint, kind, row cap, normalized question) so only truly
# identical requests share a result. Cross-process stampede is bounded separately
# by the Redis plan cache (the leader's result is written there for the next wave).
_inflight: Dict[str, "asyncio.Future"] = {}


async def _single_flight(key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``factory()`` once per ``key`` while a call is in flight; concurrent
    callers with the same key await that single result. Never leaks an entry."""
    existing = _inflight.get(key)
    if existing is not None:
        # shield so a follower's cancellation can't cancel the shared leader.
        return await asyncio.shield(existing)
    loop = asyncio.get_running_loop()
    fut: "asyncio.Future" = loop.create_future()
    _inflight[key] = fut
    try:
        result = await factory()
    except BaseException as exc:  # propagate to followers, then re-raise
        if not fut.done():
            fut.set_exception(exc)
        raise
    else:
        if not fut.done():
            fut.set_result(result)
        return result
    finally:
        _inflight.pop(key, None)
        if not fut.done():
            # Leader was cancelled before resolving — unblock followers.
            fut.cancel()
        elif not fut.cancelled():
            # Consume any stored exception so the event loop doesn't log
            # "Future exception was never retrieved" when there were no
            # followers to await it (the leader re-raised separately).
            fut.exception()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def plan_and_execute(req: QueryRequest) -> QueryResponse:
    """Resolve catalogue datasets, plan per-kind, execute, format results."""
    import catalogue_index
    from config import get_settings

    # Dataset-selection cap (how many tables reach the planner) is distinct
    # from req.max_results (the ROW limit). Without this, a request for 50 rows
    # would pull up to 50 tables into the prompt.
    datasets = await catalogue_index.select_datasets(
        question=req.query,
        dataset_ids=req.dataset_ids,
        source_id=req.source_id,
        max_results=get_settings().nl_query_max_datasets,
    )

    if not datasets:
        # Nothing matched — fall back to legacy semantic search on the source
        # so existing dashboards that point at a pure-document source keep
        # behaving as they did before this refactor.
        return await _legacy_semantic(req)

    # Group by kind so each backend is planned + executed once.
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for ds in datasets:
        kind = (ds.get("kind") or "semantic").lower()
        by_kind.setdefault(kind, []).append(ds)

    results: List[ChunkResult] = []
    primary_kind: Optional[str] = None

    for kind, group in by_kind.items():
        try:
            chunks = await _plan_and_execute_kind(req, kind, group)
        except Exception as exc:  # noqa: BLE001 — never crash /query
            logger.error(f"❌ [PLANNER] {kind}: {exc}")
            chunks = [
                ChunkResult(
                    text=f"Backend {kind!r} failed: {exc}",
                    score=0.0,
                    source=group[0].get("id", "unknown"),
                    metadata={"kind": kind, "error": str(exc)},
                )
            ]
        results.extend(chunks)
        if primary_kind is None and chunks:
            primary_kind = kind

    return QueryResponse(
        results=results,
        source_id=req.source_id,
        source_type=_to_source_type(primary_kind),
        total=len(results),
    )


# ---------------------------------------------------------------------------
# Per-kind dispatch
# ---------------------------------------------------------------------------


async def _plan_and_execute_kind(
    req: QueryRequest,
    kind: str,
    datasets: List[Dict[str, Any]],
) -> List[ChunkResult]:
    if kind == "sql":
        return await _run_structured(req, kind, datasets)
    if kind == "duckdb":
        return await _run_structured(req, kind, datasets)
    if kind == "semantic":
        # RAG short-circuit (pure disconnect): the MCP serves NO semantic — it is
        # answered by the Citra-Service platform reader. Reaching here means a
        # stale binding or catalogue drift (the loader already drops semantic
        # sources). Fail loud; NEVER run RAG in the MCP.
        sid = datasets[0].get("source_id") if datasets else req.source_id
        logger.error(
            "[PLANNER] semantic dataset reached the MCP (source=%s) — semantic is "
            "served platform-side (Citra-Service /semantic/search), not here", sid)
        return [ChunkResult(
            text="This is a semantic (RAG) source. The dept-MCP no longer serves "
                 "RAG — route kind=semantic to the Citra-Service platform reader "
                 "(POST /semantic/search).",
            score=0.0, source=sid,
            metadata={"kind": "semantic", "error": "semantic_not_served_by_mcp"})]
    if kind in ("odata", "soql", "rest"):
        return await _run_extension(req, kind, datasets)
    logger.warning(f"[PLANNER] kind {kind!r} not supported")
    return []


# ---------------------------------------------------------------------------
# Structured (sql / duckdb)
# ---------------------------------------------------------------------------

# Count-first inline-row cap. A plain row-SELECT is NEVER returned as a
# truncated sample a consumer (copilot / brief) might mistake for the whole
# set: we COUNT first and only return rows when the full result is small.
# Above the cap we return the COUNT (the deterministic answer) + a nudge to ask
# for an aggregate. Aggregate / GROUP BY queries ARE the answer and pass through.
_COUNT_FIRST_CAP = 50


def _is_aggregate_sql(sql: str, kind: str) -> bool:
    """True if the SELECT is an aggregate (has GROUP BY, or every output column
    is an aggregate function) — its rows are the answer, don't collapse them.
    False (→ count-first applies) for a plain row-SELECT, and on any parse
    failure (fail safe: never hand back a sample)."""
    try:
        import sqlglot  # type: ignore
        from sqlglot import expressions as sg_exp  # type: ignore
        tree = sqlglot.parse_one(sql, read=("duckdb" if kind == "duckdb" else None))
        if tree is None:
            return False
        if tree.find(sg_exp.Group) is not None:
            return True
        sel = tree.find(sg_exp.Select)
        exprs = (sel.expressions if sel else None) or []
        if not exprs:
            return False
        _AGG = (sg_exp.Count, sg_exp.Sum, sg_exp.Avg, sg_exp.Min, sg_exp.Max)
        def _agg(e):
            inner = e.this if isinstance(e, sg_exp.Alias) else e
            return inner.find(*_AGG) is not None and inner.find(sg_exp.Column) is None \
                if not isinstance(inner, _AGG) else True
        return all(_agg(e) for e in exprs)
    except Exception:
        return False


def _count_wrapper_sql(sql: str, kind: str) -> Optional[str]:
    """Wrap a SELECT as ``SELECT count(*) FROM (<select w/o ORDER BY/LIMIT>)``.
    Returns None when it can't be built (→ skip count-first, fall through)."""
    try:
        import sqlglot  # type: ignore
        from sqlglot import expressions as sg_exp  # type: ignore
        dialect = "duckdb" if kind == "duckdb" else None
        tree = sqlglot.parse_one(sql, read=dialect)
        if tree is None:
            return None
        for node in list(tree.find_all(sg_exp.Order)):
            node.pop()
        for node in list(tree.find_all(sg_exp.Limit)):
            node.pop()
        return f"SELECT count(*) AS _cf_n FROM ({tree.sql(dialect=dialect)}) _cf"
    except Exception:
        return None


def _truncated_to_count_chunk(
    rows: Any, kind: str, source_id: Optional[str], primary_dataset_id: str,
    sql: Optional[str] = None,
) -> Optional["ChunkResult"]:
    """Kind-AGNOSTIC count-first fallback (applies to every backend, not just
    SQL). If a row result hit the inline cap it's a SAMPLE, not the full set —
    return a count marker instead of the truncated rows so NO source type ever
    hands back a sample a consumer might total. Returns None when complete."""
    if not isinstance(rows, list) or len(rows) < _COUNT_FIRST_CAP:
        return None
    n = len(rows)
    return ChunkResult(
        text=(
            f"At least {n} rows match this query (hit the {_COUNT_FIRST_CAP}-row "
            f"inline cap) — returning a count marker, not a sample. For a total / "
            f"sum / breakdown, ask for an aggregate (COUNT/SUM/AVG, optionally "
            f"GROUP BY); to see rows, narrow the filter."
        ),
        score=1.0,
        source=primary_dataset_id,
        metadata={
            "kind": kind, "row_count_at_least": n, "rows_omitted": True,
            "aggregate": True, "sql": sql, "source_id": source_id,
        },
    )


async def _exec_cached_plan(
    req: QueryRequest, kind: str, datasets: List[Dict[str, Any]],
    sql: str, source_id: Optional[str],
) -> Optional[List[ChunkResult]]:
    """Execute a cached SQL plan directly (NO planner LLM). Returns chunks on a
    clean, still-valid result; None to fall through to live planning when the
    cached plan errors (schema drift) or a row-select now overflows the cap
    (so we never serve a stale truncated sample)."""
    import query_engine
    from router import _sources
    source = _sources.get(source_id) or {}
    read_via = None
    if kind == "duckdb":
        rv = datasets[0].get("read_via") or {}
        files = rv.get("files") or (rv.get("extra") or {}).get("files") or []
        read_via = {"kind": "duckdb", "table": rv.get("target") or rv.get("table") or "data",
                    "files": files, "extra": rv.get("extra") or {}}
    row_limit = max(req.max_results * 10, 50)
    primary_dataset_id = datasets[0].get("id", "unknown")
    is_agg = _is_aggregate_sql(sql, kind)
    rl = row_limit if is_agg else (_COUNT_FIRST_CAP + 1)
    res = await query_engine.execute(
        kind=kind, source=source, query=sql, row_limit=rl,
        read_via=read_via if kind == "duckdb" else None,
    )
    if res.error:
        logger.info("[PLANNER] plan-cache stale (exec error) — re-planning: %s", res.error)
        return None
    if not is_agg and len(res.rows) > _COUNT_FIRST_CAP:
        logger.info("[PLANNER] plan-cache row-select now > cap — re-planning")
        return None
    logger.info("[PLANNER] plan-cache HIT — skipped planner LLM (source=%s)", source_id)
    return _rows_to_chunks(
        rows=res.rows, error=None, sql=res.sql_used or sql, kind=kind,
        primary_dataset_id=primary_dataset_id, source_id=source_id or req.source_id,
    )


async def _run_structured(
    req: QueryRequest,
    kind: str,
    datasets: List[Dict[str, Any]],
) -> List[ChunkResult]:
    """Plan-cache wrapper around the live planner (``_run_structured_impl``).

    Layer 1 — on a repeat question for the same schema, execute the cached SQL
    and SKIP the planner LLM entirely. On a clean live answer, store the plan +
    push it to the few-shot ring. Fail-open: any cache error → live planning.
    """
    import plan_cache
    source_id = datasets[0].get("source_id") if datasets else None
    cache_sid = source_id or req.source_id
    fp = plan_cache.schema_fingerprint(datasets)

    cached_sql = plan_cache.get_plan(cache_sid, fp, req.query)
    if cached_sql:
        hit = await _exec_cached_plan(req, kind, datasets, cached_sql, source_id)
        if hit is not None:
            return hit

    # Cold miss → single-flight the live planning so a burst of identical cold
    # requests doesn't fan out N planner-LLM + DB chains. Followers await the
    # leader; the leader also writes the plan cache for the next wave.
    sf_key = f"{cache_sid}|{fp}|{kind}|{req.max_results}|{plan_cache._norm(req.query)}"

    async def _compute() -> List[ChunkResult]:
        out = await _run_structured_impl(req, kind, datasets, fp=fp)
        # Store only a CLEAN answer: has SQL, no error chunk, not the degenerate
        # "too many rows" count-marker. Covers agentic answers, aggregate
        # re-plans, and clean row returns.
        try:
            md = (getattr(out[0], "metadata", None) or {}) if out else {}
            sql = md.get("sql")
            if sql and not md.get("error") and not md.get("rows_omitted"):
                plan_cache.set_plan(cache_sid, fp, req.query, sql)
                plan_cache.push_example(cache_sid, fp, req.query, sql)
        except Exception:
            pass
        return out

    return await _single_flight(sf_key, _compute)


async def _run_structured_impl(
    req: QueryRequest,
    kind: str,
    datasets: List[Dict[str, Any]],
    fp: Optional[str] = None,
) -> List[ChunkResult]:
    """NL→SQL via catalogue, then execute — with self-correction retries.

    Each attempt: generate SQL → execute. On an execution error the failing
    SQL + DB error are fed back into the next planner call so the model fixes
    it (up to ``llm_planner_max_retries``). A planner LLM error (e.g. null
    content from a reasoning model) is retried too. After all attempts the
    final chunk carries the *real* reason — never a bare ``planner_failed``.
    """
    from config import get_settings
    from planners.nl_to_sql import PlannerError
    from planners._llm import PlannerLLMError
    import query_engine

    if kind == "sql":
        from planners import nl_to_sql as planner_mod
    else:
        from planners import nl_to_duckdb as planner_mod

    cfg = get_settings()
    row_limit = max(req.max_results * 10, 50)
    primary_dataset_id = datasets[0].get("id", "unknown")
    # Execute via the shared engine. We need a source dict — pull it from
    # the first dataset (multi-source SQL across MCP boundaries is not yet
    # supported; the planner already grouped by kind, not by source).
    source_id = datasets[0].get("source_id")
    from router import _sources
    source = _sources.get(source_id) or {}

    # Dialect for the planner prompt — derive from the source's connection so the
    # model emits valid SQL for THIS engine. Postgres ≠ ANSI: `CURRENT_TIMESTAMP`
    # has no parens, and it's `INTERVAL '90 days'` not `INTERVAL '90' DAYS`. The
    # planner was defaulting to "ansi" (dialect never passed) → invalid Postgres,
    # which is exactly the broken SQL seen on the BSPHCL outage queries. duckdb
    # pins its own dialect inside nl_to_duckdb, so only pass it for the sql kind.
    _db_type = str((source.get("connection") or {}).get("type") or "").lower()
    _dialect = {
        "postgres": "postgres", "postgresql": "postgres",
        "mysql": "mysql", "mariadb": "mysql",
        "sqlserver": "sqlserver", "mssql": "sqlserver", "tsql": "sqlserver",
    }.get(_db_type, "ansi")
    _plan_dialect_kw: Dict[str, Any] = {} if kind == "duckdb" else {"dialect": _dialect}

    # Layer 2 — few-shot: recent successful (question, SQL) pairs for THIS
    # schema, injected into the planner prompt so it gets the patterns right
    # first try (fewer self-correction retries). Empty when none / cache off.
    import plan_cache
    _fp = fp or plan_cache.schema_fingerprint(datasets)
    _examples = await plan_cache.get_examples(source_id or req.source_id, _fp, req.query)
    _ex_block = plan_cache.render_examples_block(_examples)

    # The engine needs read_via to know which files to mount for duckdb;
    # it's independent of the generated SQL, so build it once.
    read_via = None
    if kind == "duckdb":
        rv = datasets[0].get("read_via") or {}
        files = rv.get("files") or (rv.get("extra") or {}).get("files") or []
        read_via = {
            "kind": "duckdb",
            "table": rv.get("target") or rv.get("table") or "data",
            "files": files,
            "extra": rv.get("extra") or {},
        }

    # DETERMINISTIC-FIRST: the single-shot planner (one LLM call to generate the
    # SQL) + the count-first guard (a plain COUNT query — NO LLM) handles the
    # common case in ~1 round and gives the same no-partial-sample guarantee as
    # the agentic planner. The tool-using agentic planner (2-3 LLM rounds) is the
    # FALLBACK below — it runs only when this single-shot path can't answer, so a
    # normal query no longer pays the extra agentic rounds. (Was agentic-first;
    # flipped to cut MCP planner LLM round-trips per query.)
    max_attempts = max(1, cfg.llm_planner_max_retries)
    prev_sql: Optional[str] = None
    last_error: Optional[str] = None  # DB execution error OR planner reason

    for attempt in range(max_attempts):
        previous_attempt = (
            {"sql": prev_sql, "error": last_error}
            if (prev_sql and last_error)
            else None
        )
        try:
            sql = await planner_mod.plan(
                question=req.query,
                datasets=datasets,
                row_limit=row_limit,
                previous_attempt=previous_attempt,
                examples_block=_ex_block,
                **_plan_dialect_kw,
            )
        except PlannerError as exc:
            # The LLM call itself failed (null content / network / shape).
            # Could be transient — retry without SQL feedback.
            last_error = f"planner LLM error: {exc}"
            prev_sql = None
            logger.warning(
                f"[PLANNER] attempt {attempt + 1}/{max_attempts} planner failed: {exc}"
            )
            continue
        if not sql:
            last_error = "planner returned empty SQL"
            prev_sql = None
            continue

        # Count-first guard: for a plain row-SELECT, COUNT before fetching so we
        # never return a truncated sample. Over the cap → return the COUNT only
        # (the agentic answer) and tell the caller to request an aggregate.
        #
        # A deterministic top-N (ORDER BY … LIMIT n <= cap) is EXEMPT: it is not a
        # sample. "The most recent complaint" is completely answered by ORDER BY
        # created_at DESC LIMIT 1 — that row is the answer, not 1 of 1300. Without
        # this, count-first saw the 1300-row base set, re-planned as an aggregate
        # (the directive literally says "if the question only asks to list/see
        # records, return SELECT count(*)"), and handed back a count. The caller
        # asked again, got the count again, and burned its whole loop before
        # giving up with no answer. An unordered LIMIT stays a sample and is still
        # collapsed; a parse failure returns None here and keeps the old path.
        from agentic_sql_planner import _deterministic_top_n
        _top_n = _deterministic_top_n(sql, kind)
        if _top_n is not None:
            logger.info(
                "[PLANNER] deterministic top-N (LIMIT %d + ORDER BY) — exempt from "
                "count-first; returning the ordered rows", _top_n,
            )
        if _top_n is None and not _is_aggregate_sql(sql, kind):
            _count_sql = _count_wrapper_sql(sql, kind)
            if _count_sql:
                # Count-probe cache: a reusable SIZE ESTIMATE (list-vs-aggregate),
                # not data. On a hit we skip the (often full-scan) COUNT; the
                # rows/aggregate below are still executed live. Busted on writes.
                _cnt_sid = source_id or req.source_id
                _n = plan_cache.get_count(_cnt_sid, _count_sql)
                if _n is None:
                    _cres = await query_engine.execute(
                        kind=kind, source=source, query=_count_sql, row_limit=1,
                        read_via=read_via if kind == "duckdb" else None,
                    )
                    if not _cres.error and _cres.rows:
                        try:
                            _n = int(list(_cres.rows[0].values())[0])
                        except Exception:
                            _n = None
                        if isinstance(_n, int):
                            plan_cache.set_count(_cnt_sid, _count_sql, _n)
                if isinstance(_n, int) and _n > _COUNT_FIRST_CAP:
                        # AGENTIC re-plan: too many rows to list — ask the MCP's
                        # planner LLM to produce the AGGREGATE that answers the
                        # question (COUNT/SUM/AVG, GROUP BY as fits), run it, and
                        # return THAT. So the MCP itself decides the next query
                        # after seeing the count — no consumer round-trip.
                        logger.info(
                            "[PLANNER] count-first: %d rows > cap %d — re-planning "
                            "as an aggregate", _n, _COUNT_FIRST_CAP,
                        )
                        _agg_directive = (
                            f"{req.query}\n\n[The full result is {_n} rows — too many "
                            f"to return as a list. Generate ONE aggregate SQL query "
                            f"that answers this over the SAME filter: COUNT/SUM/AVG, "
                            f"with GROUP BY if a breakdown is implied. If the question "
                            f"only asks to list/see records, return SELECT count(*) "
                            f"for that filter.]"
                        )
                        try:
                            _agg_sql = await planner_mod.plan(
                                question=_agg_directive, datasets=datasets,
                                row_limit=row_limit, previous_attempt=None,
                                examples_block=_ex_block, **_plan_dialect_kw,
                            )
                        except PlannerError:
                            _agg_sql = None
                        if _agg_sql and _is_aggregate_sql(_agg_sql, kind):
                            _ares = await query_engine.execute(
                                # L1: cap the aggregate re-plan at the count-first
                                # cap (was row_limit, up to 500) — a GROUP BY
                                # breakdown shouldn't bloat the chat with 500
                                # buckets; stay consistent with the other caps.
                                kind=kind, source=source, query=_agg_sql,
                                row_limit=min(row_limit, _COUNT_FIRST_CAP),
                                read_via=read_via if kind == "duckdb" else None,
                            )
                            if not _ares.error:
                                return _rows_to_chunks(
                                    rows=_ares.rows, error=None,
                                    sql=_ares.sql_used or _agg_sql, kind=kind,
                                    primary_dataset_id=primary_dataset_id,
                                    source_id=source_id or req.source_id,
                                )
                        # Re-plan didn't yield a usable aggregate → COUNT marker
                        # (the deterministic floor — still never a sample).
                        return [ChunkResult(
                            text=(
                                f"{_n} rows match this query — returning the COUNT, "
                                f"not the rows. This number IS the total. For a sum / "
                                f"average / breakdown, ask for an aggregate."
                            ),
                            score=1.0,
                            source=primary_dataset_id,
                            metadata={
                                "kind": kind, "row_count": _n, "aggregate": True,
                                "rows_omitted": True, "sql": _count_sql,
                                "source_id": source_id or req.source_id,
                            },
                        )]
                    # _n <= cap (or unknown) → fall through and fetch the rows.

        if kind == "duckdb":
            result = await query_engine.execute(
                kind="duckdb", source=source, query=sql,
                row_limit=row_limit, read_via=read_via,
            )
        else:
            result = await query_engine.execute(
                kind="sql", source=source, query=sql, row_limit=row_limit,
            )

        if not result.error:
            # Success (empty rows is a valid answer — do NOT retry).
            if attempt > 0:
                logger.info(
                    f"[PLANNER] SQL succeeded on attempt {attempt + 1} "
                    f"after self-correction"
                )
            # Belt-and-suspenders: if the precise pre-fetch count-first was
            # skipped (e.g. sqlglot unavailable) and the fetch came back capped,
            # collapse to a count marker — never return a truncated sample.
            _g = _truncated_to_count_chunk(
                result.rows, kind, source_id or req.source_id,
                primary_dataset_id, result.sql_used or sql,
            )
            if _g:
                return [_g]
            return _rows_to_chunks(
                rows=result.rows,
                error=None,
                sql=result.sql_used or sql,
                kind=kind,
                primary_dataset_id=primary_dataset_id,
                source_id=source_id or req.source_id,
            )

        # Execution error → feed the failing SQL + DB error back and retry.
        last_error = result.error
        prev_sql = result.sql_used or sql
        logger.warning(
            f"[PLANNER] attempt {attempt + 1}/{max_attempts} SQL failed: "
            f"{result.error} — re-planning with feedback"
        )

    # Deterministic single-shot couldn't answer → fall back to the tool-using
    # agentic planner (probe→decide, 2-3 LLM rounds) for genuinely exploratory
    # queries. It runs ONLY here, after the cheap path failed, so the common case
    # never pays its extra rounds.
    try:
        from agentic_sql_planner import agentic_answer
        _ag = await agentic_answer(
            question=req.query, datasets=datasets, kind=kind, source=source,
            read_via=read_via, examples_block=_ex_block,
            dialect=("duckdb" if kind == "duckdb" else _dialect),
            make_chunks=lambda rows, sql: _rows_to_chunks(
                rows=rows, error=None, sql=sql, kind=kind,
                primary_dataset_id=primary_dataset_id,
                source_id=source_id or req.source_id,
            ),
            is_aggregate=lambda sql: _is_aggregate_sql(sql, kind),
            count_wrapper=lambda sql: _count_wrapper_sql(sql, kind),
        )
        if _ag is not None:
            logger.info("[PLANNER] agentic fallback answered after deterministic exhaustion")
            return _ag
    except Exception as _ae:  # noqa: BLE001
        logger.warning("[PLANNER] agentic fallback also errored (%r)", _ae)

    # Both planners failed — surface the real reason (never a detail-free planner_failed).
    detail = last_error or "planner could not generate a query"
    text = (
        f"NL→{kind.upper()} planning failed after {max_attempts} attempt(s). "
        f"{detail}"
    )
    if prev_sql:
        text += f"\nLast SQL: {prev_sql}"
    logger.error(f"[PLANNER] {text}")
    return [
        ChunkResult(
            text=text,
            score=0.0,
            source=primary_dataset_id,
            metadata={
                "kind": kind,
                "error": "planner_failed",
                "detail": detail,
                "sql": prev_sql,
            },
        )
    ]


def _rows_to_chunks(
    *,
    rows: List[Dict[str, Any]],
    error: Optional[str],
    sql: str,
    kind: str,
    primary_dataset_id: str,
    source_id: str,
) -> List[ChunkResult]:
    if error:
        return [
            ChunkResult(
                text=f"Query failed.\nGenerated query: {sql}\nError: {error}",
                score=0.0,
                source=primary_dataset_id,
                metadata={"kind": kind, "sql": sql, "error": error, "source_id": source_id},
            )
        ]
    if not rows:
        return [
            ChunkResult(
                text=f"Query returned no rows.\nQuery: {sql}",
                score=0.5,
                source=primary_dataset_id,
                metadata={"kind": kind, "sql": sql, "row_count": 0, "source_id": source_id},
            )
        ]

    # Render rows as a pipe-delimited table — matches the legacy
    # structured_engine output so chat/dashboard prose-fusion keeps
    # parsing it.
    header = " | ".join(str(k) for k in rows[0].keys())
    lines = [f"Table: {primary_dataset_id}", header, "-" * min(len(header), 120)]
    for row in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in row.values()))
    text = "\n".join(lines) + f"\n\nQuery used:\n{sql}"

    return [
        ChunkResult(
            text=text,
            score=1.0,
            source=primary_dataset_id,
            metadata={
                "kind": kind,
                "sql": sql,
                "row_count": len(rows),
                "dataset_id": primary_dataset_id,
                "source_id": source_id,
                # Raw rows are passed through so structured tool callers
                # (smart-app `query_dataset`, dashboard tile bindings) can
                # render their own table without parsing the prose form.
                "rows": rows,
            },
        )
    ]


# ---------------------------------------------------------------------------
# Semantic — RETIRED from the MCP (pure disconnect). Semantic (RAG) queries are
# answered by the Citra-Service platform reader (POST /semantic/search), so the
# MCP query path no longer imports rag.semantic_engine. Semantic sources are
# dropped at load (router._swap_registry) and fail loud in _plan_and_execute_kind
# if a stale binding reaches here.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Extension kinds (odata / soql / rest)
# ---------------------------------------------------------------------------


async def _run_extension(
    req: QueryRequest,
    kind: str,
    datasets: List[Dict[str, Any]],
) -> List[ChunkResult]:
    """Stubs return a clear "not implemented" chunk until a dept subclass overrides.

    The plan + execute *contract* is in place: when a dept ships a real
    ``planners.nl_to_<kind>`` module and a matching ``query_engine``
    branch, this orchestrator routes traffic without further changes.
    """
    if kind == "odata":
        from planners import nl_to_odata as planner_mod
    elif kind == "soql":
        from planners import nl_to_soql as planner_mod
    else:
        from planners import nl_to_rest as planner_mod

    plan = await planner_mod.plan(question=req.query, datasets=datasets)
    if plan is None:
        return [
            ChunkResult(
                text=(
                    f"Backend {kind!r} is an extension point; this MCP "
                    "deployment has no NL→query handler installed."
                ),
                score=0.0,
                source=datasets[0].get("id", "unknown"),
                metadata={"kind": kind, "error": "not_implemented"},
            )
        ]

    # If a subclass DID return a plan, route through query_engine. The
    # engine raises ValueError for kinds it cannot handle, which we
    # surface as a clean failure chunk.
    source_id = datasets[0].get("source_id")
    from router import _sources
    source = _sources.get(source_id) or {}

    import query_engine

    try:
        result = await query_engine.execute(
            kind=kind,
            source=source,
            # The plan IS the query (dict for odata/rest, str for soql); the
            # connector reads it from `query`. `read_via` carries the dataset's
            # entity/sObject/path target as the fallback — same contract as the
            # /run_query path in catalogue.py. (Previously the dict plan was
            # mis-routed into read_via with query="", so OData never executed.)
            query=plan,
            row_limit=max(req.max_results * 10, 50),
            read_via=datasets[0].get("read_via") or None,
        )
    except ValueError as exc:
        return [
            ChunkResult(
                text=str(exc),
                score=0.0,
                source=datasets[0].get("id", "unknown"),
                metadata={"kind": kind, "error": "engine_unsupported"},
            )
        ]

    # Count-first guard for the extension backends too (odata/soql/rest) — a
    # capped row result becomes a count marker, never a sample.
    if not result.error:
        _g = _truncated_to_count_chunk(
            result.rows, kind, source_id or req.source_id,
            datasets[0].get("id", "unknown"), str(plan),
        )
        if _g:
            return [_g]
    return _rows_to_chunks(
        rows=result.rows,
        error=result.error,
        sql=str(plan),
        kind=kind,
        primary_dataset_id=datasets[0].get("id", "unknown"),
        source_id=source_id or req.source_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_source_type(kind: Optional[str]) -> SourceType:
    """Map the dominant resolved kind back onto the legacy SourceType enum
    so existing chat / dashboard callers see the response shape they expect."""
    if kind in ("sql", "duckdb", "odata", "soql", "rest"):
        return SourceType.structured
    if kind == "mongodb":
        return SourceType.mongodb
    return SourceType.semantic


async def _legacy_semantic(req: QueryRequest) -> QueryResponse:
    """No dataset matched. Historically this fell back to a per-source semantic
    (RAG) search, but the MCP no longer serves RAG (pure disconnect) — semantic
    is answered by the Citra-Service platform reader. So a no-match here means
    there is nothing structured to plan; return empty rather than run RAG.

    (Semantic sources are dropped at load, so they never reach the planner; this
    is the belt-and-braces path for an uncatalogued/unknown structured source.)
    """
    logger.info(
        "[PLANNER] no dataset matched for source=%s — MCP serves no RAG; returning "
        "empty (semantic is answered platform-side)", req.source_id)
    return QueryResponse(
        results=[], source_id=req.source_id,
        source_type=SourceType.structured, total=0,
    )
