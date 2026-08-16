# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
SQL Planner (duckdb-query-service)
==================================
Mirror of Citra-Service/services/sql_planner.py. Runs BEFORE SQL generation
to produce a structured plan that steers generate_sql_with_llm toward the
correct pattern (window functions / recursive CTE / simple aggregate / …)
and surfaces definitional assumptions that would otherwise produce wrong
answers silently (e.g. FIFO vs cash-flow P&L).

Fallback: any failure returns None. Callers MUST treat None as "no plan —
proceed with the old behaviour". The planner never blocks the SQL pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from llm_client import get_llm_client, get_default_model

logger = logging.getLogger(__name__)

_DOMAIN_KEYWORDS = re.compile(
    r"\b("
    r"fifo|lifo|realized|unrealized|matched|reconcile|running\s+balance|open\s+position|"
    r"p&l|pnl|p\.l|profit\s*(?:and|&)?\s*loss|net\s+profit|gross\s+profit|"
    r"margin|gross\s+margin|net\s+margin|ebitda|ebit|yield|return|roi|irr|npv|"
    r"tax|vat|gst|sales\s+tax|withholding|"
    r"conversion|churn|retention|ltv|cac|arpu|mrr|arr|"
    r"weighted\s+average|cumulative|cohort|funnel"
    r")\b",
    re.IGNORECASE,
)

APPROACH_SIMPLE_AGGREGATE = "simple_aggregate"
APPROACH_WINDOW_FUNCTIONS = "window_functions"
APPROACH_RECURSIVE_CTE = "recursive_cte"
APPROACH_PIVOT = "pivot"
APPROACH_TOP_N = "top_n"
APPROACH_PER_TABLE = "per_table"

_VALID_APPROACHES = {
    APPROACH_SIMPLE_AGGREGATE,
    APPROACH_WINDOW_FUNCTIONS,
    APPROACH_RECURSIVE_CTE,
    APPROACH_PIVOT,
    APPROACH_TOP_N,
    APPROACH_PER_TABLE,
}


def _should_plan(query: str) -> bool:
    if not query or len(query.strip()) < 20:
        return False
    if _DOMAIN_KEYWORDS.search(query):
        return True
    return len(query.strip()) >= 40


def _compact_schema(tables_info: List[Dict[str, Any]]) -> str:
    lines = []
    for t in tables_info or []:
        cols = ", ".join(t.get("columns") or [])
        lines.append(f'"{t.get("table_name", "?")}" ({t.get("row_count", 0)} rows): {cols}')
    return "\n".join(lines)


_PLANNER_PROMPT = """You are a SQL planner. Given a user question and the available table schemas, produce a short JSON plan that steers the SQL generator toward the correct approach.

User question:
{query}

Available tables:
{schema}

Respond with a JSON object of this exact shape (no prose, JSON only):

{{
  "goal": "<one sentence restating what the user wants>",
  "method": "<the specific method if a choice exists, e.g. 'FIFO lot matching', 'gross margin = (revenue - cogs) / revenue', or 'straightforward aggregation'>",
  "assumptions": ["<definitional or data assumption>", ...],
  "approach": "<one of: simple_aggregate | window_functions | recursive_cte | pivot | top_n | per_table>",
  "required_columns": ["<column name from the schemas above>", ...],
  "needs_clarification": <true|false>,
  "clarification_question": "<null OR a single question to ask the user>"
}}

Rules:
- Pick `approach` carefully:
  * simple_aggregate — plain GROUP BY / SUM / COUNT / AVG
  * window_functions — running totals, lot matching (FIFO/LIFO), per-group cumulative sums, rank-based logic
  * recursive_cte — complex iterative matching (multi-leg FIFO reconciliation, inventory roll-forward)
  * pivot — long-to-wide reshaping
  * top_n — rank + LIMIT per group
  * per_table — the question compares independent tables; answer each separately
- If the user's metric has multiple reasonable interpretations (e.g. "P&L" could be cash-flow, realized FIFO, realized LIFO, or mark-to-market) AND the question does NOT pin down which one, set `needs_clarification=true` and put a single specific question in `clarification_question`. Otherwise set it to false and null.
- `required_columns` must be a subset of actual column names from the schemas above. Do not invent columns.
- `assumptions` should call out definitional choices and data-shape assumptions (e.g. "rows are already in chronological order by Date", "Qty is positive for buys and negative for sells").
- Keep every field short. No prose outside the JSON."""


async def plan_sql_approach(
    query: str,
    tables_info: List[Dict[str, Any]],
    vector_samples: Optional[List[Dict[str, Any]]] = None,
    *,
    timeout_seconds: float = 6.0,
) -> Optional[Dict[str, Any]]:
    if not _should_plan(query):
        return None
    schema = _compact_schema(tables_info)
    if not schema:
        return None

    try:
        client = get_llm_client()
        model = get_default_model()
        prompt = _PLANNER_PROMPT.format(query=query.strip(), schema=schema)

        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a strict JSON-only SQL planner."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                # Generous, cost-neutral cap — reasoning tokens must not starve
                # the plan JSON (2048 truncated GLM under load).
                max_tokens=16000,
                response_format={"type": "json_object"},
            ),
            timeout=timeout_seconds,
        )

        content = (resp.choices[0].message.content or "").strip()
        plan = json.loads(content)

        approach = str(plan.get("approach", "")).strip().lower()
        if approach not in _VALID_APPROACHES:
            approach = APPROACH_SIMPLE_AGGREGATE
        plan["approach"] = approach
        plan["goal"] = str(plan.get("goal", "")).strip()
        plan["method"] = str(plan.get("method", "")).strip()

        assumptions = plan.get("assumptions") or []
        if not isinstance(assumptions, list):
            assumptions = [str(assumptions)]
        plan["assumptions"] = [str(a).strip() for a in assumptions if str(a).strip()]

        required_cols = plan.get("required_columns") or []
        if not isinstance(required_cols, list):
            required_cols = [str(required_cols)]
        plan["required_columns"] = [str(c).strip() for c in required_cols if str(c).strip()]

        plan["needs_clarification"] = bool(plan.get("needs_clarification", False))
        clar = plan.get("clarification_question")
        plan["clarification_question"] = str(clar).strip() if clar and str(clar).strip().lower() != "null" else None

        logger.info(
            "[SQL_PLAN] approach=%s method=%r needs_clarification=%s assumptions=%s",
            plan["approach"], plan["method"], plan["needs_clarification"], plan["assumptions"],
        )
        return plan

    except Exception as e:  # noqa: BLE001
        logger.warning("[SQL_PLAN] planner unavailable (%r); proceeding without plan", e)
        return None


_APPROACH_GUIDANCE = {
    APPROACH_SIMPLE_AGGREGATE: (
        "Use plain GROUP BY with SUM / COUNT / AVG / MIN / MAX. "
        "Do not use window functions unless strictly needed."
    ),
    APPROACH_WINDOW_FUNCTIONS: (
        "Use DuckDB window functions (SUM() OVER, LAG, LEAD, ROW_NUMBER, etc.) "
        "with appropriate PARTITION BY / ORDER BY. Do NOT approximate stateful "
        "calculations with a plain GROUP BY SUM — that will give wrong results."
    ),
    APPROACH_RECURSIVE_CTE: (
        "Use a WITH RECURSIVE CTE to iterate through rows chronologically and "
        "maintain state across iterations (e.g. FIFO lot queue, running inventory). "
        "Do NOT collapse this into a single GROUP BY."
    ),
    APPROACH_PIVOT: (
        "Use DuckDB's PIVOT / UNPIVOT syntax or conditional aggregation "
        "(SUM(CASE WHEN … END)) to reshape the data."
    ),
    APPROACH_TOP_N: (
        "Use ROW_NUMBER() OVER (PARTITION BY … ORDER BY … DESC) filtered to a rank threshold, "
        "OR use ORDER BY … DESC LIMIT N for a single group."
    ),
    APPROACH_PER_TABLE: (
        "This query compares independent tables. Generate SQL for only the single table "
        "you are called for; the caller will aggregate the per-table results."
    ),
}


def format_plan_for_prompt(plan: Optional[Dict[str, Any]]) -> str:
    if not plan:
        return ""
    assumptions = plan.get("assumptions") or []
    assumptions_str = "\n    - ".join(assumptions) if assumptions else "(none stated)"
    required_cols = plan.get("required_columns") or []
    cols_str = ", ".join(f'"{c}"' for c in required_cols) if required_cols else "(infer from schema)"
    guidance = _APPROACH_GUIDANCE.get(plan.get("approach", ""), "")

    return f"""APPROACH PLAN (follow strictly — this plan was produced by an upstream planner):
  Goal: {plan.get("goal", "(unstated)")}
  Method: {plan.get("method", "(unstated)")}
  SQL pattern: {plan.get("approach", "simple_aggregate")} — {guidance}
  Assumptions:
    - {assumptions_str}
  Required columns: {cols_str}

Generate SQL that implements EXACTLY this plan. If the plan calls for window functions or a recursive CTE, do not fall back to a plain GROUP BY.
"""
