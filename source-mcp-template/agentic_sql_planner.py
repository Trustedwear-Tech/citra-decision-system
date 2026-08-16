"""Tool-using agentic planner for SQL-family backends (sql / duckdb).

The LLM agent loops over EXPLICIT tools to build the right data fetch:
  • probe_count(where)    — count rows matching a filter (the count probe)
  • answer_aggregate(sql) — return a COUNT/SUM/AVG/GROUP-BY result
  • answer_rows(sql)      — return ACTUAL rows; needs a probe_count <= CAP, OR a
                            deterministic top-N (ORDER BY … LIMIT n <= CAP)

The code ENFORCES the no-partial-result invariant: ``answer_rows`` is refused
unless a count probe confirmed <= CAP; above CAP the agent MUST aggregate. So a
truncated row sample can NEVER leave the MCP — every total/sum/breakdown is
computed at the source.

The ONE exemption is a deterministic top-N — a SELECT carrying both an explicit
ORDER BY and LIMIT n <= CAP. That is not a partial result: "the most recent
complaint" is completely answered by ``ORDER BY created_at DESC LIMIT 1``, and
that row is the whole answer, not 1 of 2001. Without the exemption such a
question was UNANSWERABLE on any table over CAP rows — the probe returns the
whole-table count, the cap refuses rows, and "narrow the filter" is circular
(narrowing needs a value; the value is what was asked for). The agent burned
every round rephrasing and returned nothing. An unordered LIMIT stays refused:
that really is an arbitrary slice.

JSON-mode (not native tool-calling) keeps it robust on GLM-class models. Returns
None on agent failure so the caller can fall back to the deterministic
count-first path.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import plan_cache
import query_engine
from planners._llm import call_json_llm
from planners.nl_to_sql import _dataset_table_name, _dialect_hint, _format_columns

logger = logging.getLogger("source-mcp.agentic")

CAP = 50
# M9: the agent answers in ~2 rounds (probe → answer); 3 leaves one retry. Was 6
# — too generous, and it chains in series before the deterministic fallback, so a
# degraded /query could burn ~6 LLM calls here before the fallback even starts.
MAX_ROUNDS = 3

_SYSTEM = """You are a data-query agent for the table(s) below. Answer the user's question by emitting EXACTLY ONE JSON action per turn (a single JSON object, nothing else). Your tools:

- {{"action": "probe_count", "where": "<SQL WHERE predicate, or empty string for all rows>", "table": "<table name — REQUIRED when more than one table is shown below>"}}
    Returns how many rows match that filter. Call this BEFORE returning rows.
- {{"action": "answer_aggregate", "sql": "<a full aggregate SELECT: COUNT/SUM/AVG/MIN/MAX, add GROUP BY for a breakdown>"}}
    Returns the computed aggregate. Use for totals, sums, averages, breakdowns, OR whenever the matching rows exceed {cap}.
- {{"action": "answer_rows", "sql": "<a full row SELECT>"}}
    Returns the actual rows. Allowed when EITHER:
      (a) a probe_count for the same filter returned <= {cap}; OR
      (b) the SELECT is a deterministic top-N — it carries BOTH an explicit ORDER BY
          AND LIMIT <= {cap}. A top-N needs NO probe_count and is allowed no matter how
          many rows match in total, because the ordering makes those rows the complete
          answer rather than an arbitrary slice.

Choosing the answer:
- "how many / count / total / sum / average / breakdown / by <category>" → answer_aggregate (COUNT/SUM/AVG, GROUP BY for a breakdown). No need to list rows.
- "latest / most recent / newest / oldest / top N / first / last / highest / lowest / any one" → answer_rows with an explicit ORDER BY on the ranking column (DESC for latest/highest, ASC for oldest/lowest) plus LIMIT <n> — n is how many were asked for, default 1. Skip probe_count. NEVER answer one of these with a count: "which is the most recent complaint" asks for a record, not a total.
- "show / list / which / see / details of" → probe_count first; if the count is <= {cap}, answer_rows (return the actual records). If it is greater than {cap}: use the ORDER BY + LIMIT top-N form when the user wants a ranked few, otherwise answer_aggregate and tell the user to narrow the filter.

Rules:
- ALWAYS probe_count before answer_rows, UNLESS the SELECT is a deterministic top-N (explicit ORDER BY + LIMIT <= {cap}), which needs no probe.
- If the count is greater than {cap}, you may NOT return an unordered row sample: use answer_aggregate, narrow the filter, or answer a top/latest-N question with ORDER BY + LIMIT.
- A LIMIT without an ORDER BY is an arbitrary sample and WILL be refused — always pair them.
- For TEXT / status / category equality filters, match case-insensitively: LOWER("col") = LOWER('value') — unless the schema's distinct values show the exact casing.
- Use ONLY the columns listed UNDER each table. A column listed under one table does NOT exist on another — never put table A's column in a query against table B. To pull data from another table, JOIN via the relationships below; do NOT invent a foreign-key column (e.g. do not assume `consumer_id` / `complaint_id` exists on a table where it is not listed). SELECT-only, read-only. Quote identifiers with double quotes.
- When MORE THAN ONE table is shown, you MUST set "table" in probe_count to the exact table your WHERE/columns reference, and answer_rows/answer_aggregate SQL MUST use the fully-qualified, correct table name(s).
- Emit ONE JSON object per turn and nothing else.
{dialect_hint}{rels}
Schema:
{schema}
"""


def _deterministic_top_n(sql: str, kind: str) -> Optional[int]:
    """The N of a deterministic top-N row SELECT (``ORDER BY … LIMIT n``, n <= CAP),
    else None.

    A top-N is NOT a sample: "the most recent complaint" is completely answered by
    ``ORDER BY created_at DESC LIMIT 1``, and that one row is the whole answer — not
    1 of 2001. The cap exists so nobody totals an arbitrary subset; an ordered,
    bounded slice can't be mistaken for a total, so it is safe to return however
    large the underlying match set is.

    Both clauses are required. ORDER BY without LIMIT is unbounded; LIMIT without
    ORDER BY is an ARBITRARY subset (the engine may return any n rows) — that IS a
    sample and stays refused, which is what the LIMIT-stripping guard was built to
    catch. Fail-safe: None on any parse failure, so an unparseable query keeps the
    old no-partial-result behaviour.
    """
    try:
        import sqlglot  # type: ignore
        from sqlglot import expressions as sg_exp  # type: ignore
        tree = sqlglot.parse_one(sql, read=("duckdb" if kind == "duckdb" else None))
        if tree is None:
            return None
        if tree.find(sg_exp.Order) is None:
            return None
        lim = tree.find(sg_exp.Limit)
        if lim is None:
            return None
        n = int((lim.expression or lim.this).this)
        return n if 1 <= n <= CAP else None
    except Exception:
        return None


def _schema_block(datasets: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for ds in datasets:
        t = _dataset_table_name(ds)
        cols = _format_columns(ds.get("columns") or [])
        rc = ds.get("row_count_approx", -1)
        rc_str = f" (~{rc:,} rows)" if isinstance(rc, int) and rc > 0 else ""
        blocks.append(f'Table: "{t}"{rc_str}\n  Columns:\n{cols}')
    return "\n\n".join(blocks)


def _rel_block(datasets: List[Dict[str, Any]]) -> str:
    """Render the catalogue FK relationships as explicit JOIN hints so the agent
    relates tables via real keys instead of inventing a foreign-key column on the
    wrong table."""
    lines: List[str] = []
    seen: set = set()
    for ds in datasets:
        ft = _dataset_table_name(ds)
        for rel in ds.get("relationships") or []:
            key = (ft, rel.get("from_column"), rel.get("to_dataset"), rel.get("to_column"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f'  "{ft}"."{rel.get("from_column")}" = '
                f'"{rel.get("to_dataset")}"."{rel.get("to_column")}"'
            )
    if not lines:
        return ""
    return "\nKnown JOIN relationships — use these to relate tables (do not guess keys):\n" + "\n".join(lines) + "\n"


async def agentic_answer(
    *,
    question: str,
    datasets: List[Dict[str, Any]],
    kind: str,
    source: Dict[str, Any],
    read_via: Optional[Dict[str, Any]],
    make_chunks: Callable[[List[Dict[str, Any]], str], List[Any]],
    is_aggregate: Callable[[str], bool],
    count_wrapper: Optional[Callable[[str], Optional[str]]] = None,
    examples_block: str = "",
    dialect: str = "ansi",
) -> Optional[List[Any]]:
    """Run the agent loop. Returns the final ChunkResult list (rows when small,
    aggregate when large), or None if the agent could not produce a terminal
    answer (the caller falls back to the deterministic count-first path)."""
    if not datasets:
        return None
    system = _SYSTEM.format(
        cap=CAP,
        schema=_schema_block(datasets),
        dialect_hint=_dialect_hint(dialect),
        rels=_rel_block(datasets),
    ) + (examples_block or "")
    transcript: List[str] = [f"USER QUESTION: {question}"]
    table = _dataset_table_name(datasets[0])
    # Allowed physical table names — lets the LLM pin probe_count to the table its
    # WHERE references on multi-table sources (was hardcoded to datasets[0]).
    allowed_tables = {_dataset_table_name(ds) for ds in datasets}
    last_count: Optional[int] = None
    _rv = read_via if kind == "duckdb" else None

    async def _run(sql: str, row_limit: int):
        return await query_engine.execute(
            kind=kind, source=source, query=sql, row_limit=row_limit, read_via=_rv,
        )

    for _round in range(MAX_ROUNDS):
        user = "\n".join(transcript) + "\n\nEmit your next JSON action."
        # 4000, not 700: a reasoning-class model spends hidden tokens before the
        # JSON action — too low a cap truncates content to empty (finish=length),
        # which now fails loud (PlannerLLMError) rather than silently bailing.
        resp = await call_json_llm(system=system, user=user, max_tokens=4000)
        if not isinstance(resp, dict):
            logger.info("[AGENTIC] round %d: no/invalid JSON from LLM — bailing", _round + 1)
            return None
        action = (resp.get("action") or "").strip()

        if action == "probe_count":
            where = (resp.get("where") or "").strip()
            # Let the LLM pick the count table (multi-table sources); validate it
            # against the advertised tables, else default to datasets[0]'s table.
            _req_table = (resp.get("table") or "").strip()
            if _req_table:
                if _req_table not in allowed_tables:
                    transcript.append(
                        f'probe_count(table="{_req_table}") ERROR: unknown table. '
                        f"Use one of: {', '.join(sorted(allowed_tables))}."
                    )
                    continue
                _count_table = _req_table
            else:
                _count_table = table
            sql = f'SELECT count(*) AS n FROM "{_count_table}"' + (f" WHERE {where}" if where else "")
            # Count-probe cache: this count is a SIZE ESTIMATE for the
            # list-vs-aggregate decision, not data — reuse it across apps to skip
            # a repeated (often full-scan) COUNT. Busted on any write to the source.
            _sid = (source or {}).get("source_id")
            last_count = plan_cache.get_count(_sid, sql)
            if last_count is not None:
                transcript.append(f'probe_count(where="{where}") -> {last_count} rows')
                continue
            r = await _run(sql, 1)
            if r.error:
                logger.warning("[AGENTIC] probe_count SQL failed (round %d) q=%r where=%r: %s | sql=%s",
                               _round + 1, question, where, r.error, sql)
                transcript.append(f'probe_count(where="{where}") ERROR: {r.error} — fix the predicate.')
                continue
            try:
                last_count = int(list(r.rows[0].values())[0]) if r.rows else 0
            except Exception:
                last_count = None
            if isinstance(last_count, int):
                plan_cache.set_count(_sid, sql, last_count)
            transcript.append(f'probe_count(where="{where}") -> {last_count} rows')
            continue

        if action == "answer_aggregate":
            sql = (resp.get("sql") or "").strip()
            if not sql or not is_aggregate(sql):
                transcript.append("answer_aggregate REFUSED: sql must be an aggregate (COUNT/SUM/AVG/MIN/MAX or GROUP BY). Provide one.")
                continue
            r = await _run(sql, CAP)
            if r.error:
                logger.warning("[AGENTIC] answer_aggregate SQL failed (round %d) q=%r: %s | sql=%s",
                               _round + 1, question, r.error, sql)
                transcript.append(f"answer_aggregate ERROR: {r.error} — fix and retry.")
                continue
            logger.info("[AGENTIC] answered via aggregate in round %d", _round + 1)
            return make_chunks(r.rows, r.sql_used or sql)

        if action == "answer_rows":
            sql = (resp.get("sql") or "").strip()
            # A deterministic top-N (ORDER BY … LIMIT n) is a COMPLETE answer, not a
            # sample, so it needs no count probe and is exempt from the cap below.
            # Without this the agent could never answer "show me the most recent X"
            # on a table over CAP rows: the probe returns the whole-table count, the
            # cap refuses rows, and the "narrow the filter" advice is circular —
            # narrowing needs a value, and the value is what it is asking for. It
            # burned every round rephrasing and returned nothing.
            top_n = _deterministic_top_n(sql, kind) if sql else None
            if top_n is None and (last_count is None or last_count > CAP):
                transcript.append(
                    f"answer_rows REFUSED: rows are only allowed after a probe_count <= {CAP}; "
                    f"current count = {last_count}. If over {CAP}, use answer_aggregate — or, "
                    f"for a 'top / latest / first N' question, re-issue this SELECT with an "
                    f"explicit ORDER BY plus LIMIT <= {CAP} (a deterministic top-N is a "
                    f"complete answer and needs no probe)."
                )
                continue
            if not sql:
                transcript.append("answer_rows ERROR: empty sql.")
                continue
            # AIRTIGHT guard, bound to THIS query: COUNT its true matches with the
            # query's OWN LIMIT stripped, so the agent can't slip a sample past the
            # cap by self-limiting (e.g. `LIMIT 5` on a 227-row table). If the true
            # match count > CAP, refuse and force an aggregate.
            #
            # Exempt a deterministic top-N: stripping ORDER BY … LIMIT n and counting
            # the base set is exactly the wrong question for it. "Latest complaint"
            # matching 2001 rows does not make the newest one a sample — the ordering
            # is what makes those n rows THE answer. Unordered self-limits are still
            # counted and refused here, which is the abuse this guard was written for.
            _cw = count_wrapper(sql) if (count_wrapper and top_n is None) else None
            if _cw:
                _sid = (source or {}).get("source_id")
                _true = plan_cache.get_count(_sid, _cw)
                if _true is None:
                    _cr = await _run(_cw, 1)
                    if not _cr.error and _cr.rows:
                        try:
                            _true = int(list(_cr.rows[0].values())[0])
                        except Exception:
                            _true = None
                        if isinstance(_true, int):
                            plan_cache.set_count(_sid, _cw, _true)
                if isinstance(_true, int) and _true > CAP:
                    transcript.append(
                        f"answer_rows REFUSED: this query matches {_true} rows "
                        f"(> {CAP}) — returning any of them is a sample. Use "
                        f"answer_aggregate (or narrow the filter to <= {CAP})."
                    )
                    continue
            # Belt (count unavailable / unparseable): fetch CAP+1 and refuse on overflow.
            r = await _run(sql, CAP + 1)
            if r.error:
                logger.warning("[AGENTIC] answer_rows SQL failed (round %d) q=%r: %s | sql=%s",
                               _round + 1, question, r.error, sql)
                transcript.append(f"answer_rows ERROR: {r.error} — fix and retry.")
                continue
            if len(r.rows) > CAP:
                transcript.append(
                    f"answer_rows REFUSED: this query matches more than {CAP} rows — "
                    f"returning them would be a sample. Use answer_aggregate instead."
                )
                continue
            logger.info("[AGENTIC] answered via %d rows in round %d", len(r.rows), _round + 1)
            return make_chunks(r.rows, r.sql_used or sql)

        transcript.append(f"Unknown action {action!r}. Use probe_count / answer_aggregate / answer_rows.")

    logger.info("[AGENTIC] exhausted %d rounds without a terminal answer — falling back", MAX_ROUNDS)
    return None
