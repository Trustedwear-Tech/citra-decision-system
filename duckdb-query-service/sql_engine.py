# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
SQL Engine — LLM-powered SQL generation + DuckDB execution.

Migrated from Citra-Service services/sql_query_engine.py.
Key difference: uses the persistent TablePool instead of ephemeral connections.
The caller no longer opens/closes DuckDB — the pool owns the connection lifecycle.

LLM provider is configurable via environment variables — see llm_client.py.
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

import duckdb

from config import config
from llm_client import get_llm_client, get_default_model
from table_pool import get_table_pool

logger = logging.getLogger(__name__)

MAX_RESULT_ROWS = None  # No cap — consumers slice independently

CHART_ROW_LIMITS = config.CHART_ROW_LIMITS


# ── LLM helper (OpenAI-compatible via openai SDK) ────────────────────────

async def _llm_call(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    # Generous cap: reasoning models (GLM/DeepSeek) spend hidden reasoning
    # tokens against this budget, and max_tokens is a cost-neutral UPPER bound
    # (the model stops when done). 4096 truncated SQL gen on reasoning models.
    max_tokens: int = 16000,
    temperature: float = 0.1,
) -> str:
    """Call the configured LLM provider via OpenAI-compatible SDK."""
    effective_model = model or get_default_model()
    client = get_llm_client()

    response = await client.chat.completions.create(
        model=effective_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = response.choices[0].message.content
    logger.info("[LLM_CALL] model=%s content=%s", effective_model, repr((content or "")[:300]))
    return content or ""


# ── SQL generation ──

async def generate_sql_with_llm(
    query: str,
    tables_info: List[Dict[str, Any]],
    vector_samples: Optional[List[Dict[str, Any]]] = None,
    chart_type: Optional[str] = None,
    model: str = None,
    user_id: str = None,
    force_aggregate: bool = False,
    plan: Optional[Dict[str, Any]] = None,
) -> str:
    """Use the configured LLM to generate a DuckDB SQL query from natural language."""
    schema_parts = []
    for t in tables_info:
        col_types = t.get("column_types", {})
        if col_types:
            cols = ", ".join(f'"{c}" ({col_types.get(c, "VARCHAR")})' for c in t["columns"])
        else:
            cols = ", ".join(f'"{c}"' for c in t["columns"])
        schema_parts.append(
            f'Table: "{t["table_name"]}" ({t.get("filename", "unknown")}, {t["row_count"]} rows)\n'
            f"  Columns: {cols}"
        )
    schema_text = "\n".join(schema_parts)

    samples_text = ""
    if vector_samples:
        sample_lines = []
        for i, sample in enumerate(vector_samples[:5]):
            raw = sample.get("raw_data", sample.get("data", {}))
            if raw:
                pairs = list(raw.items())[:8]
                sample_str = ", ".join(f'{k}: "{v}"' for k, v in pairs)
                sample_lines.append(f"  Row {i+1}: {sample_str}")
        if sample_lines:
            samples_text = (
                "\n\nSample data from vector search (real values from the dataset):\n"
                + "\n".join(sample_lines)
            )

    row_limit = CHART_ROW_LIMITS.get(chart_type, MAX_RESULT_ROWS) if chart_type else MAX_RESULT_ROWS

    system_prompt = f"""You are a SQL expert. Generate a DuckDB SQL query for the user's question.

Available tables:
{schema_text}
{samples_text}

Rules:
- Return ONLY the SQL query, no explanation or markdown
- Use DuckDB SQL syntax
- ALL column names MUST be double-quoted (e.g., "Primary Type", "Community Area", "Case Number")
- Table names must be double-quoted
- SQL keywords must be spelled correctly: SELECT, WITH, FROM, WHERE, GROUP BY, ORDER BY, LIMIT, UNION ALL
- Column data types are shown in parentheses. Respect them:
  - TRIM(), LOWER(), UPPER() only work on VARCHAR columns
  - For BOOLEAN columns: compare directly (e.g., "Arrest" = true), do NOT use TRIM or LOWER
  - For DOUBLE/INTEGER columns: use numeric comparisons, do NOT use TRIM or string functions
  - If you must apply string functions to non-VARCHAR columns, CAST first: TRIM(CAST("Column" AS VARCHAR))
- For aggregate queries (GROUP BY / COUNT / SUM / AVG), do NOT add a LIMIT clause unless the user asks for a specific number — return all groups
- For non-aggregate queries (raw row listing), add LIMIT {row_limit}
- For trend queries, ORDER BY the time/date column
- For comparisons, ORDER BY the metric DESC to show highest first
- Handle NULLs and empty strings gracefully (NULLIF, COALESCE)
- If column values look numeric but are strings, CAST them: TRY_CAST(col AS DOUBLE)
- Never use SELECT * — always select specific columns relevant to the query
- When filtering by a specific entity (e.g. WHERE symbol = 'X'), ALWAYS include that entity column in the SELECT so the result shows what the aggregation belongs to (e.g. SELECT "symbol", SUM("quantity") ...)
- For pie/doughnut charts: limit to {CHART_ROW_LIMITS.get('pie', 10)} categories, aggregate small values into 'Other'

DuckDB-specific syntax (IMPORTANT):
- AVOID using UNION ALL inside CTEs. Instead, use a single SELECT with OR/IN conditions.
- If you must use UNION ALL, do NOT place it inside a CTE — use it at the top level or wrap each SELECT in parentheses: (SELECT ...) UNION ALL (SELECT ...)
- DuckDB does not support ORDER BY inside individual SELECT statements of a UNION ALL — put ORDER BY only on the final outer query
- Use ILIKE instead of LOWER() + LIKE for case-insensitive matching

Stateful / matching calculations (CRITICAL):
- If the user asks for a stateful per-group calculation — FIFO or LIFO matching, running balance, realized P&L, inventory cost, lot matching, reconciliation between paired rows — use DuckDB window functions (SUM() OVER, LAG, LEAD, ROW_NUMBER) and/or recursive CTEs. Do NOT approximate these with plain GROUP BY SUM aggregates — plain sums on buy/sell data give cash-flow, not realized P&L, and will be wrong.
- For FIFO P&L specifically: sort rows chronologically per symbol/group, build a running buy-lot queue, match each sell against the oldest unmatched buy lots, and sum (sell_price − buy_price) × matched_qty across all matches. Express this with window functions or a recursive CTE in DuckDB.
- If the user's question contains words like "FIFO", "LIFO", "realized", "matched", "running balance", "reconcile", "open position", treat it as stateful and use the above pattern — never reduce it to a single SUM/GROUP BY."""

    if force_aggregate:
        system_prompt += f"""

IMPORTANT: This dataset has a large number of rows. You MUST use GROUP BY to aggregate and summarize the data into meaningful groups. Do NOT return individual raw rows. Always aggregate (COUNT, SUM, AVG, MIN, MAX, etc.) and limit to at most 500 groups. If the user's question doesn't specify how to group, choose the most meaningful column(s) to group by based on the data schema."""

    # Prepend plan block (if a pre-execution planner produced one).
    if plan:
        try:
            from sql_planner import format_plan_for_prompt
            plan_block = format_plan_for_prompt(plan)
            if plan_block:
                system_prompt = plan_block + "\n\n" + system_prompt
        except Exception as e:
            logger.warning("Failed to inject SQL plan into prompt: %s", e)

    user_prompt = f"Question: {query}"
    if chart_type:
        user_prompt += f"\nThis will be displayed as a {chart_type} chart."

    t_llm = time.time()
    response = await _llm_call(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=16000,
        temperature=0.1,
    )

    sql = _extract_sql(response)
    logger.info("[SQL] LLM generation: %.3fs | SQL: %s", time.time() - t_llm, sql[:200])
    return sql


def _extract_sql(response: str) -> str:
    """Extract SQL from LLM response, stripping markdown and reasoning artifacts."""
    if not response:
        return ""
    text = response.strip()
    # Strip <think>...</think> blocks from reasoning models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Extract from markdown code blocks
    sql_match = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL)
    if sql_match:
        text = sql_match.group(1).strip()
    # Find SQL keyword at start of any line
    sql_start = re.search(r"(?i)^(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE)", text, re.MULTILINE)
    if sql_start:
        text = text[sql_start.start():]
    # Fallback: look for SELECT/WITH anywhere (not just line-start) with word boundary
    elif re.search(r"(?i)\bSELECT\b|\bWITH\b", text):
        match = re.search(r"(?i)(\bSELECT\b|\bWITH\b)", text)
        if match:
            text = text[match.start():]
    return text.rstrip("; \n\t")


def execute_safe(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int = MAX_RESULT_ROWS,
) -> Dict[str, Any]:
    """Execute a SQL query safely — SELECT/WITH only."""
    normalized = sql.strip().upper()
    if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
        logger.error("[SQL] REJECTED (not SELECT/WITH): %s", repr(sql[:500]))
        return {
            "error": "Only SELECT queries are allowed",
            "columns": [], "rows": [], "row_count": 0, "truncated": False,
        }
    try:
        t_exec = time.time()
        result = conn.execute(sql)
        columns = [desc[0] for desc in result.description]

        if max_rows is not None:
            rows = result.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
        else:
            rows = result.fetchall()
            truncated = False

        row_dicts = []
        for row in rows:
            row_dict = {}
            for i, col in enumerate(columns):
                val = row[i]
                if val is None:
                    row_dict[col] = None
                elif isinstance(val, (int, float, str, bool)):
                    row_dict[col] = val
                else:
                    row_dict[col] = str(val)
            row_dicts.append(row_dict)

        logger.info("[SQL] Execution: %.3fs (%d rows)", time.time() - t_exec, len(row_dicts))
        return {
            "columns": columns,
            "rows": row_dicts,
            "row_count": len(row_dicts),
            "truncated": truncated,
            "error": None,
        }
    except Exception as e:
        logger.error("SQL execution failed: %s", e)
        return {"error": str(e), "columns": [], "rows": [], "row_count": 0, "truncated": False}


def format_for_chart(
    sql_result: Dict[str, Any],
    chart_type: Optional[str] = None,
) -> Dict[str, Any]:
    if sql_result.get("error"):
        return {
            "has_structured_data": False,
            "error": sql_result["error"],
            "records": [], "record_count": 0, "schema_fields": [],
        }
    return {
        "has_structured_data": True,
        "data_source": "sql_analytics",
        "records": sql_result["rows"],
        "record_count": sql_result["row_count"],
        "schema_fields": sql_result["columns"],
        "truncated": sql_result.get("truncated", False),
        "provider": "duckdb",
    }


def format_for_llm(sql_result: Dict[str, Any], query: str, tables_info: Optional[List[Dict[str, Any]]] = None) -> str:
    if sql_result.get("error"):
        return f"SQL query failed: {sql_result['error']}"
    rows = sql_result.get("rows", [])
    columns = sql_result.get("columns", [])
    if not rows:
        return f"SQL query for '{query}' returned no results."
    lines = [f"SQL Analytics Result for query: '{query}' ({len(rows)} rows):"]
    # Include full file schema so LLM knows what file/structure the result belongs to
    if tables_info:
        for t in tables_info:
            col_types = t.get("column_types", {})
            if col_types:
                all_cols = ", ".join(f'{c} ({col_types.get(c, "VARCHAR")})' for c in t["columns"])
            else:
                all_cols = ", ".join(t["columns"])
            lines.append(f"Source file: {t.get('filename', t.get('table_name', 'unknown'))} ({t['row_count']} total rows)")
            lines.append(f"All columns: {all_cols}")
    lines.append("Result columns: " + ", ".join(columns))
    lines.append("")
    for i, row in enumerate(rows[:500]):
        pairs = [f"{col}: {row.get(col, 'N/A')}" for col in columns]
        lines.append(f"  Row {i+1}: {', '.join(pairs)}")
    if len(rows) > 500:
        lines.append(f"  ... and {len(rows) - 500} more rows")
    if sql_result.get("truncated"):
        lines.append("  (Results were truncated)")
    return "\n".join(lines)


# ── Retry + aggregation helpers ──

async def _retry_sql_with_error(
    query: str,
    tables_info: List[Dict[str, Any]],
    failed_sql: str,
    error_msg: str,
    vector_samples: Optional[List[Dict[str, Any]]] = None,
    chart_type: Optional[str] = None,
    user_id: str = None,
) -> Optional[str]:
    schema_parts = []
    for t in tables_info:
        col_types = t.get("column_types", {})
        if col_types:
            cols = ", ".join(f'"{c}" ({col_types.get(c, "VARCHAR")})' for c in t["columns"])
        else:
            cols = ", ".join(f'"{c}"' for c in t["columns"])
        schema_parts.append(f'Table: "{t["table_name"]}" — Columns: {cols}')

    system_prompt = f"""You are a SQL expert. The previous SQL query failed. Fix it.

Available tables:
{chr(10).join(schema_parts)}

Failed SQL:
{failed_sql}

Error:
{error_msg}

Rules for the fix:
- ALL column names MUST be double-quoted (e.g., "Primary Type", "Community Area")
- SQL keywords must be spelled correctly: SELECT, WITH, FROM, WHERE, GROUP BY, ORDER BY, LIMIT, UNION ALL
- TRIM(), LOWER(), UPPER() only work on VARCHAR columns — CAST non-VARCHAR first
- For BOOLEAN columns: compare directly ("Arrest" = true), do NOT use TRIM or LOWER
- For DOUBLE/INTEGER columns: use numeric comparisons, do NOT use string functions

DuckDB-specific syntax (IMPORTANT):
- AVOID using UNION ALL inside CTEs. Instead, use a single SELECT with OR/IN conditions.
- If you must use UNION ALL, do NOT place it inside a CTE — use it at the top level or wrap each SELECT in parentheses: (SELECT ...) UNION ALL (SELECT ...)
- DuckDB does not support ORDER BY inside individual SELECT statements of a UNION ALL — put ORDER BY only on the final outer query
- Use ILIKE instead of LOWER() + LIKE for case-insensitive matching

Return ONLY the corrected SQL query. No explanation."""

    try:
        response = await _llm_call(
            system_prompt=system_prompt,
            user_prompt=f"Original question: {query}",
            max_tokens=16000,
            temperature=0.1,
        )
        sql = _extract_sql(response)
        logger.info("[SQL] Retry SQL extracted: %s", sql[:200] if sql else "EMPTY")
        return sql if sql else None
    except Exception as e:
        logger.error("SQL retry failed: %s", e)
        return None


async def _generate_aggregation_sql(
    query: str,
    tables_info: List[Dict[str, Any]],
    first_sql: str,
    first_result_columns: List[str],
    first_result_row_count: int,
    chart_type: Optional[str] = None,
    user_id: str = None,
    model: str = None,
) -> Optional[str]:
    schema_parts = []
    for t in tables_info:
        col_types = t.get("column_types", {})
        if col_types:
            cols = ", ".join(f'"{c}" ({col_types.get(c, "VARCHAR")})' for c in t["columns"])
        else:
            cols = ", ".join(f'"{c}"' for c in t["columns"])
        schema_parts.append(f'Table: "{t["table_name"]}" — Columns: {cols}')

    system_prompt = f"""You are a SQL expert. The previous SQL query returned {first_result_row_count} rows, which is too many.
Rewrite the query to aggregate and summarize the data into at most 500 rows.

Available tables:
{chr(10).join(schema_parts)}

Previous SQL (returned {first_result_row_count} rows with columns: {', '.join(first_result_columns)}):
{first_sql}

Rules:
- You MUST use GROUP BY with aggregate functions (COUNT, SUM, AVG, MIN, MAX) to reduce the row count
- You MUST add LIMIT 500 to the final query
- Choose the most meaningful column(s) to group by based on the user's question
- Keep the result relevant to the user's original question
- ALL column names MUST be double-quoted
- Return ONLY the SQL query, no explanation or markdown

DuckDB-specific syntax:
- AVOID using UNION ALL inside CTEs
- Use ILIKE instead of LOWER() + LIKE for case-insensitive matching"""

    if chart_type:
        system_prompt += f"\nThe result will be displayed as a {chart_type} chart — optimize grouping for that visualization."

    try:
        response = await _llm_call(
            system_prompt=system_prompt,
            user_prompt=f"Original question: {query}\nRewrite with aggregation (max 500 rows).",
            model=model,
            max_tokens=16000,
            temperature=0.1,
        )
        sql = _extract_sql(response)
        logger.info("[SQL] Aggregation SQL generated: %s", sql[:200])
        return sql
    except Exception as e:
        logger.error("Aggregation SQL generation failed: %s", e)
        return None


# ── Main entry points ──

async def run_analytical_query(
    query: str,
    user_id: str,
    document_ids: List[str],
    vector_samples: Optional[List[Dict[str, Any]]] = None,
    chart_type: Optional[str] = None,
    schema_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
    force_aggregate: bool = False,
) -> Dict[str, Any]:
    """
    End-to-end analytical query using the persistent table pool.
    load data (if not cached) → generate SQL → execute → format.
    """
    pool = get_table_pool()
    table_data = await pool.ensure_tables_loaded(user_id, document_ids, schema_metadata_map)

    if not table_data:
        return {
            "success": False,
            "error": "Could not load data for SQL analysis",
            "sql_result": None,
            "formatted_text": "No structured data available for analysis.",
            "chart_data": {"has_structured_data": False, "records": [], "record_count": 0, "schema_fields": []},
        }

    conn = table_data["conn"]

    # Pre-execution plan (may return None; callers treat None as no-plan).
    from sql_planner import plan_sql_approach
    plan = await plan_sql_approach(query, table_data["tables"], vector_samples)

    # If the planner flagged ambiguity, short-circuit with a clarification
    # response instead of fabricating an answer.
    if plan and plan.get("needs_clarification") and plan.get("clarification_question"):
        q = plan["clarification_question"]
        return {
            "success": False,
            "needs_clarification": True,
            "clarification_question": q,
            "sql": None,
            "sql_result": None,
            "formatted_text": (
                f"⚠️ Clarification needed before running SQL analytics:\n\n{q}\n\n"
                f"Assumptions the planner considered:\n"
                + "\n".join(f"  - {a}" for a in (plan.get("assumptions") or []))
            ),
            "chart_data": {"has_structured_data": False, "records": [], "record_count": 0, "schema_fields": []},
            "plan": plan,
        }

    # Generate SQL
    sql = await generate_sql_with_llm(
        query=query,
        tables_info=table_data["tables"],
        vector_samples=vector_samples,
        chart_type=chart_type,
        user_id=user_id,
        force_aggregate=force_aggregate,
        plan=plan,
    )

    # Execute
    row_limit = CHART_ROW_LIMITS.get(chart_type, MAX_RESULT_ROWS) if chart_type else MAX_RESULT_ROWS
    sql_result = execute_safe(conn, sql, max_rows=row_limit)

    # Retry on failure
    first_error = sql_result.get("error")
    if first_error:
        logger.warning("First SQL attempt failed: %s — retrying", first_error)
        retry_sql = await _retry_sql_with_error(
            query, table_data["tables"], sql, first_error, vector_samples, chart_type, user_id,
        )
        if retry_sql:
            retry_result = execute_safe(conn, retry_sql, max_rows=row_limit)
            if not retry_result.get("error"):
                sql = retry_sql
                sql_result = retry_result
            else:
                # Retry also failed — keep the more informative first error
                # unless the first was a SELECT-validation error
                logger.warning("Retry also failed: %s", retry_result["error"])
                if first_error != "Only SELECT queries are allowed":
                    sql_result["error"] = first_error

    # Two-pass aggregation
    if not sql_result.get("error") and sql_result.get("row_count", 0) > 500:
        logger.info("First pass returned %d rows (>500), running aggregation pass", sql_result["row_count"])
        agg_sql = await _generate_aggregation_sql(
            query=query, tables_info=table_data["tables"], first_sql=sql,
            first_result_columns=sql_result["columns"],
            first_result_row_count=sql_result["row_count"],
            chart_type=chart_type, user_id=user_id,
        )
        if agg_sql:
            agg_result = execute_safe(conn, agg_sql, max_rows=500)
            if not agg_result.get("error") and agg_result.get("row_count", 0) > 0:
                logger.info("Aggregation pass: %d rows (was %d)", agg_result["row_count"], sql_result["row_count"])
                sql_result = agg_result
                sql = agg_sql

    return {
        "success": not sql_result.get("error"),
        "error": sql_result.get("error"),
        "sql": sql,
        "sql_result": sql_result,
        "formatted_text": format_for_llm(sql_result, query, table_data["tables"]),
        "chart_data": format_for_chart(sql_result, chart_type),
        "plan": plan,
    }


async def run_per_table_fetch(
    query: str,
    user_id: str,
    document_ids: List[str],
    vector_samples: Optional[List[Dict[str, Any]]] = None,
    chart_type: Optional[str] = None,
    schema_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Per-table comparison (COMPARE queries)."""
    pool = get_table_pool()
    table_data = await pool.ensure_tables_loaded(user_id, document_ids, schema_metadata_map)

    if not table_data:
        return {
            "success": False,
            "error": "Could not load data for comparison",
            "per_table_results": [],
            "formatted_text": "No structured data available for comparison.",
            "chart_data": {"has_structured_data": False, "records": [], "record_count": 0, "schema_fields": []},
        }

    conn = table_data["conn"]
    per_table_results = []
    all_rows = []
    all_columns: set = set()
    formatted_parts = []
    row_limit = CHART_ROW_LIMITS.get(chart_type, MAX_RESULT_ROWS) if chart_type else MAX_RESULT_ROWS

    # Plan once for the whole multi-table question; reuse for each per-table SQL.
    from sql_planner import plan_sql_approach
    plan = await plan_sql_approach(query, table_data["tables"], vector_samples)

    if plan and plan.get("needs_clarification") and plan.get("clarification_question"):
        q = plan["clarification_question"]
        return {
            "success": False,
            "needs_clarification": True,
            "clarification_question": q,
            "per_table_results": [],
            "formatted_text": (
                f"⚠️ Clarification needed before running SQL analytics:\n\n{q}\n\n"
                f"Assumptions the planner considered:\n"
                + "\n".join(f"  - {a}" for a in (plan.get("assumptions") or []))
            ),
            "chart_data": {"has_structured_data": False, "records": [], "record_count": 0, "schema_fields": []},
            "plan": plan,
        }

    for tbl in table_data["tables"]:
        table_name = tbl["table_name"]
        filename = tbl.get("filename", table_name)
        doc_id = tbl.get("document_id", "")

        tbl_samples = [
            s for s in (vector_samples or [])
            if s.get("raw_data", {}).get("document_id") == doc_id
        ] or vector_samples

        single_table_info = [tbl]
        try:
            sql = await generate_sql_with_llm(
                query=query, tables_info=single_table_info, vector_samples=tbl_samples,
                chart_type=chart_type, user_id=user_id,
                force_aggregate=tbl["row_count"] > (row_limit or 500),
                plan=plan,
            )
            sql_result = execute_safe(conn, sql, max_rows=row_limit)

            if sql_result.get("error"):
                retry_sql = await _retry_sql_with_error(
                    query, single_table_info, sql, sql_result["error"], tbl_samples, chart_type, user_id,
                )
                if retry_sql:
                    sql_result = execute_safe(conn, retry_sql, max_rows=row_limit)

            if not sql_result.get("error") and sql_result.get("row_count", 0) > 500:
                agg_sql = await _generate_aggregation_sql(
                    query=query, tables_info=single_table_info, first_sql=sql,
                    first_result_columns=sql_result["columns"],
                    first_result_row_count=sql_result["row_count"],
                    chart_type=chart_type, user_id=user_id,
                )
                if agg_sql:
                    agg_result = execute_safe(conn, agg_sql, max_rows=500)
                    if not agg_result.get("error") and agg_result.get("row_count", 0) > 0:
                        sql_result = agg_result

        except Exception as e:
            logger.error("Per-table SQL generation failed for %s: %s", filename, e)
            sql_result = {"error": str(e), "columns": [], "rows": [], "row_count": 0, "truncated": False}

        per_table_results.append({
            "table_name": table_name, "filename": filename,
            "document_id": doc_id, "sql_result": sql_result,
        })

        rows = sql_result.get("rows", [])
        columns = sql_result.get("columns", [])
        if rows:
            all_rows.extend(rows)
            all_columns.update(columns)
            lines = [f"\n--- Data from: {filename} ({tbl['row_count']} total rows) ---"]
            # Include full schema so LLM knows all available columns in this file
            col_types = tbl.get("column_types", {})
            if col_types:
                all_cols_str = ", ".join(f'{c} ({col_types.get(c, "VARCHAR")})' for c in tbl["columns"])
            else:
                all_cols_str = ", ".join(tbl["columns"])
            lines.append(f"All columns: {all_cols_str}")
            lines.append("Result columns: " + ", ".join(columns))
            display_limit = row_limit or 500
            for i, row in enumerate(rows[:display_limit]):
                pairs = [f"{col}: {row.get(col, 'N/A')}" for col in columns]
                lines.append(f"  Row {i+1}: {', '.join(pairs)}")
            if sql_result.get("truncated"):
                lines.append(f"  (Truncated to {display_limit} rows)")
            formatted_parts.append("\n".join(lines))
        else:
            formatted_parts.append(f"\n--- Data from: {filename} --- No results")

    combined_text = (
        f"Comparison Data ({len(per_table_results)} files):\n"
        f"The user wants to compare data across these files. "
        f"Each file's data is listed separately below — analyse and compare them.\n"
        + "\n".join(formatted_parts)
    )

    merged_records = []
    for ptr in per_table_results:
        for row in ptr["sql_result"].get("rows", []):
            tagged = dict(row)
            tagged["_source_file"] = ptr["filename"]
            merged_records.append(tagged)

    chart_data = {
        "has_structured_data": bool(merged_records),
        "data_source": "per_table_comparison",
        "records": merged_records,
        "record_count": len(merged_records),
        "schema_fields": list(all_columns),
        "truncated": False,
        "provider": "duckdb",
    }

    return {
        "success": bool(all_rows),
        "error": None if all_rows else "All per-table queries returned empty results",
        "per_table_results": per_table_results,
        "formatted_text": combined_text,
        "chart_data": chart_data,
        "plan": plan,
    }
