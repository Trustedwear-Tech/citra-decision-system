# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Structured Planner (no-clarification mode)
==========================================
Port of Citra-Service's sql_planner.py tailored for dept-mcp.

Differences from the Citra-Service version:
  • NO `needs_clarification` branch. Dept MCP serves data and cannot block on
    user input, so when a metric is ambiguous the planner MUST pick one of
    the documented defaults from `planners.defaults.DEFAULTS` and record
    what it did not pick.
  • Adds `chosen_interpretation`, `alternative_interpretations`, `confidence`.
  • `approach` enum identical to Citra-Service so downstream SQL guidance
    stays in lock-step.

Fallback contract: any failure returns None. Callers MUST treat None as
"no plan available — proceed with default behaviour". The planner never
blocks the engine.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .defaults import render_defaults_for_prompt
from ._llm import call_json_llm

logger = logging.getLogger(__name__)

_DOMAIN_KEYWORDS = re.compile(
    r"\b("
    r"fifo|lifo|realized|unrealized|matched|reconcile|running\s+balance|open\s+position|"
    r"p&l|pnl|p\.l|profit\s*(?:and|&)?\s*loss|net\s+profit|gross\s+profit|"
    r"margin|gross\s+margin|net\s+margin|ebitda|ebit|yield|return|roi|irr|npv|"
    r"tax|vat|gst|sales\s+tax|withholding|"
    r"conversion|churn|retention|ltv|cac|arpu|mrr|arr|"
    r"weighted\s+average|cumulative|cohort|funnel|"
    r"top\s+\d+|growth|year[- ]?over[- ]?year|yoy|qoq|mom|cagr"
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

_VALID_CONFIDENCE = {"high", "medium", "low"}


def _should_plan(query: str) -> bool:
    if not query or len(query.strip()) < 20:
        return False
    if _DOMAIN_KEYWORDS.search(query):
        return True
    return len(query.strip()) >= 40


def _compact_schema(tables_info: List[Dict[str, Any]]) -> str:
    lines = []
    for t in tables_info or []:
        cols_raw = t.get("columns") or []
        # columns may be [{name, type}, ...] (structured_engine) or [str, ...]
        col_names = []
        for c in cols_raw:
            if isinstance(c, dict):
                col_names.append(str(c.get("name", "")))
            else:
                col_names.append(str(c))
        col_names = [c for c in col_names if c]
        lines.append(
            f'"{t.get("table_name", "?")}" ({t.get("row_count", 0)} rows): {", ".join(col_names)}'
        )
    return "\n".join(lines)


_PLANNER_SYSTEM = "You are a strict JSON-only SQL planner. Dept MCP cannot ask users for clarification — you MUST pick a default when ambiguous."

_PLANNER_PROMPT = """You are a SQL planner for a departmental data service that CANNOT ask the user for clarification. For every ambiguous metric you MUST pick a default interpretation from the table below and record the alternatives you rejected.

User question:
{query}

Available tables:
{schema}

{defaults_block}

Respond with a JSON object of this exact shape (no prose, JSON only):

{{
  "goal": "<one sentence restating what the user wants>",
  "chosen_interpretation": "<the specific interpretation you will compute — e.g. 'realized FIFO P&L excluding open positions'>",
  "alternative_interpretations": ["<interpretation you rejected>", ...],
  "method": "<the concrete method — e.g. 'FIFO lot matching with WITH RECURSIVE', 'gross margin = (revenue - cogs) / revenue'>",
  "assumptions": ["<definitional or data assumption>", ...],
  "approach": "<one of: simple_aggregate | window_functions | recursive_cte | pivot | top_n | per_table>",
  "required_columns": ["<column name from the schemas above>", ...],
  "confidence": "<high | medium | low>"
}}

Rules:
- Pick `approach` carefully:
  * simple_aggregate — plain GROUP BY / SUM / COUNT / AVG
  * window_functions — running totals, lot matching (FIFO/LIFO), per-group cumulative sums, rank-based logic
  * recursive_cte — complex iterative matching (multi-leg FIFO reconciliation, inventory roll-forward)
  * pivot — long-to-wide reshaping
  * top_n — rank + LIMIT per group
  * per_table — the question compares independent tables; answer each separately
- If the question is ambiguous, pick the DEFAULT from the defaults table (never leave the answer unspecified).
- `alternative_interpretations` lists interpretations you rejected; empty list if the question was unambiguous.
- `confidence`:
  * high — the question is explicit, schema is clear
  * medium — you used a default, or schema requires a small inferential leap
  * low — you used a default AND schema coverage is partial
- `required_columns` must be a subset of actual column names from the schemas above. Do not invent columns.
- Keep every field short. No prose outside the JSON."""


async def plan_sql_approach(
    query: str,
    tables_info: List[Dict[str, Any]],
    *,
    timeout_seconds: float = 45.0,
) -> Optional[Dict[str, Any]]:
    """
    Produce a no-clarification pre-execution plan for a SQL query.

    Returns a normalized dict or None on any failure.
    """
    if not _should_plan(query):
        return None

    schema = _compact_schema(tables_info)
    if not schema:
        return None

    prompt = _PLANNER_PROMPT.format(
        query=query.strip(),
        schema=schema,
        defaults_block=render_defaults_for_prompt(),
    )

    plan = await call_json_llm(
        system=_PLANNER_SYSTEM,
        user=prompt,
        timeout_seconds=timeout_seconds,
        max_tokens=4000,
    )
    if not plan:
        return None

    # ── Normalize ─────────────────────────────────────────────────────────
    approach = str(plan.get("approach", "")).strip().lower()
    if approach not in _VALID_APPROACHES:
        approach = APPROACH_SIMPLE_AGGREGATE
    plan["approach"] = approach

    confidence = str(plan.get("confidence", "")).strip().lower()
    if confidence not in _VALID_CONFIDENCE:
        confidence = "medium"
    plan["confidence"] = confidence

    plan["goal"] = str(plan.get("goal", "")).strip()
    plan["method"] = str(plan.get("method", "")).strip()
    plan["chosen_interpretation"] = str(plan.get("chosen_interpretation", "")).strip()

    alts = plan.get("alternative_interpretations") or []
    if not isinstance(alts, list):
        alts = [str(alts)]
    plan["alternative_interpretations"] = [str(a).strip() for a in alts if str(a).strip()]

    assumptions = plan.get("assumptions") or []
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)]
    plan["assumptions"] = [str(a).strip() for a in assumptions if str(a).strip()]

    required_cols = plan.get("required_columns") or []
    if not isinstance(required_cols, list):
        required_cols = [str(required_cols)]
    plan["required_columns"] = [str(c).strip() for c in required_cols if str(c).strip()]

    # Dept MCP is no-clarification: strip any legacy fields if the LLM emitted them.
    plan.pop("needs_clarification", None)
    plan.pop("clarification_question", None)

    logger.info(
        f"📋 [STRUCT_PLAN] approach={plan['approach']} confidence={plan['confidence']} "
        f"chosen={plan['chosen_interpretation']!r} "
        f"rejected={plan['alternative_interpretations']}"
    )
    return plan


# ---------------------------------------------------------------------------
# Guidance text per approach — appended to SQL generation prompt.
# ---------------------------------------------------------------------------

_APPROACH_GUIDANCE = {
    APPROACH_SIMPLE_AGGREGATE: (
        "Use plain GROUP BY with SUM / COUNT / AVG / MIN / MAX. "
        "Do not use window functions unless strictly needed."
    ),
    APPROACH_WINDOW_FUNCTIONS: (
        "Use window functions (SUM() OVER, LAG, LEAD, ROW_NUMBER, etc.) "
        "with appropriate PARTITION BY / ORDER BY. Do NOT approximate stateful "
        "calculations with a plain GROUP BY SUM — that will give wrong results."
    ),
    APPROACH_RECURSIVE_CTE: (
        "Use a WITH RECURSIVE CTE to iterate through rows chronologically and "
        "maintain state across iterations (e.g. FIFO lot queue, running inventory). "
        "Do NOT collapse this into a single GROUP BY."
    ),
    APPROACH_PIVOT: (
        "Use PIVOT / UNPIVOT syntax or conditional aggregation "
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
    """Render a plan as a short prompt block to prepend to the SQL generation prompt."""
    if not plan:
        return ""

    assumptions = plan.get("assumptions") or []
    assumptions_str = "\n    - ".join(assumptions) if assumptions else "(none stated)"
    required_cols = plan.get("required_columns") or []
    cols_str = ", ".join(f'"{c}"' for c in required_cols) if required_cols else "(infer from schema)"
    alts = plan.get("alternative_interpretations") or []
    alts_str = "; ".join(alts) if alts else "(none — question was unambiguous)"
    guidance = _APPROACH_GUIDANCE.get(plan.get("approach", ""), "")

    return f"""APPROACH PLAN (follow strictly — produced by an upstream no-clarification planner):
  Goal: {plan.get("goal", "(unstated)")}
  Chosen interpretation: {plan.get("chosen_interpretation", "(unstated)")}
  Rejected interpretations: {alts_str}
  Method: {plan.get("method", "(unstated)")}
  SQL pattern: {plan.get("approach", "simple_aggregate")} — {guidance}
  Assumptions:
    - {assumptions_str}
  Required columns: {cols_str}
  Planner confidence: {plan.get("confidence", "medium")}

Generate SQL that implements EXACTLY this plan. If the plan calls for window functions or a recursive CTE, do not fall back to a plain GROUP BY.
"""
