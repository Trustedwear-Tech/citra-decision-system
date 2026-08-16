"""
Retrieval Planner (unstructured RAG)
=====================================
Plans how to retrieve and synthesise an answer from a semantic Milvus
collection. Runs BEFORE the vector search so we can:

  • decompose a multi-faceted question into sub-questions (multi-query)
  • bias top_k + reranking based on question scope
  • record synthesis_style so the downstream caller composes the right
    kind of answer (list / comparison / extractive / abstractive)
  • record assumptions + confidence, same no-clarification contract as
    structured_planner.

Dept MCP cannot ask the user for clarification, so any ambiguity is
resolved to a documented default. See `planners.defaults`.

Fallback: any failure returns None and the caller uses default single-query
retrieval.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ._llm import call_json_llm

logger = logging.getLogger(__name__)

STRATEGY_SINGLE = "single_vector"
STRATEGY_MULTI = "multi_query"
STRATEGY_KEYWORD_HYBRID = "keyword_hybrid"
STRATEGY_TIME_SCOPED = "time_scoped"

_VALID_STRATEGIES = {STRATEGY_SINGLE, STRATEGY_MULTI, STRATEGY_KEYWORD_HYBRID, STRATEGY_TIME_SCOPED}

SYNTHESIS_EXTRACTIVE = "extractive"
SYNTHESIS_ABSTRACTIVE = "abstractive"
SYNTHESIS_LIST = "list"
SYNTHESIS_COMPARISON = "comparison"

_VALID_SYNTHESIS = {SYNTHESIS_EXTRACTIVE, SYNTHESIS_ABSTRACTIVE, SYNTHESIS_LIST, SYNTHESIS_COMPARISON}

_VALID_CONFIDENCE = {"high", "medium", "low"}


def _should_plan(query: str) -> bool:
    """Skip trivially short lookups."""
    if not query:
        return False
    q = query.strip()
    if len(q) < 15:
        return False
    # Always plan when the query is multi-clause or contains comparatives /
    # enumeration cues — these are the cases where a single vector search
    # under-retrieves.
    cues = (" and ", " or ", ",", " vs ", " versus ", "compare", "differences", "list", "all ", "between")
    return len(q) >= 40 or any(c in q.lower() for c in cues)


_PLANNER_SYSTEM = (
    "You are a strict JSON-only retrieval planner for a departmental RAG "
    "service that CANNOT ask the user for clarification. Pick sensible "
    "defaults when scope is ambiguous."
)

_PLANNER_PROMPT = """You plan HOW to retrieve an answer from a semantic vector store for a departmental data service. You cannot ask the user for clarification.

User question:
{query}

Source context (the collection we will search):
  source_id: {source_id}
  description: {source_description}

Respond with a JSON object of this exact shape (no prose, JSON only):

{{
  "goal": "<one sentence restating what the user wants>",
  "sub_questions": ["<atomic sub-question>", ...],
  "retrieval_strategy": "<one of: single_vector | multi_query | keyword_hybrid | time_scoped>",
  "filters": {{
    "metadata": {{}},
    "time_range": null
  }},
  "top_k": <integer 3-20>,
  "rerank": <true|false>,
  "synthesis_style": "<one of: extractive | abstractive | list | comparison>",
  "assumptions": ["<definitional or scope assumption>", ...],
  "confidence": "<high | medium | low>"
}}

Rules:
- `sub_questions`: if the question has exactly one atomic intent, emit a single entry (the original question). Otherwise decompose into 2–5 minimally-overlapping sub-questions. Each sub-question must be directly answerable by vector search.
- `retrieval_strategy`:
  * single_vector — one query embedding is enough (use for focused factual questions)
  * multi_query — execute each sub-question as a separate vector search and union results (use when sub_questions has ≥2 entries)
  * keyword_hybrid — question contains rare proper nouns or exact phrases that vector search may miss (names, IDs, exact codes)
  * time_scoped — question asks for "latest", "most recent", "in 2024", etc.
- `filters.metadata`: map of chunk-metadata field → required value. Only include fields you are certain exist in the collection schema. Empty map if none.
- `filters.time_range`: null, OR an object {{"from": "ISO-8601", "to": "ISO-8601"}} when the question is time-scoped.
- `top_k`:
  * 3–5 for single focused questions
  * 8–15 for list / enumeration / comparison questions
- `rerank`: true unless you need raw vector scores (e.g. exact-match keyword hybrid where rerank would hurt).
- `synthesis_style`:
  * extractive — paste verbatim passages (citations, quotes)
  * abstractive — synthesise a short narrative answer
  * list — enumerate items (for "list", "what are", "name all")
  * comparison — side-by-side diff (for "vs", "compare", "differences")
- `confidence`:
  * high — question is focused, single clear intent
  * medium — you decomposed into sub-questions or picked a default scope
  * low — scope is genuinely ambiguous; caller should treat results as tentative
- Keep every field short. No prose outside the JSON."""


async def plan_retrieval(
    query: str,
    *,
    source_id: str = "",
    source_description: str = "",
    timeout_seconds: float = 45.0,
) -> Optional[Dict[str, Any]]:
    """Produce a retrieval plan for an unstructured semantic query.

    Returns a normalized dict or None when the planner is skipped / fails.
    """
    if not _should_plan(query):
        return None

    prompt = _PLANNER_PROMPT.format(
        query=query.strip(),
        source_id=source_id or "(unspecified)",
        source_description=(source_description or "(no description provided)")[:400],
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
    plan["goal"] = str(plan.get("goal", "")).strip()

    subs = plan.get("sub_questions") or []
    if not isinstance(subs, list):
        subs = [str(subs)]
    subs = [str(s).strip() for s in subs if str(s).strip()]
    if not subs:
        subs = [query.strip()]
    plan["sub_questions"] = subs[:5]

    strategy = str(plan.get("retrieval_strategy", "")).strip().lower()
    if strategy not in _VALID_STRATEGIES:
        strategy = STRATEGY_MULTI if len(plan["sub_questions"]) > 1 else STRATEGY_SINGLE
    plan["retrieval_strategy"] = strategy

    synthesis = str(plan.get("synthesis_style", "")).strip().lower()
    if synthesis not in _VALID_SYNTHESIS:
        synthesis = SYNTHESIS_ABSTRACTIVE
    plan["synthesis_style"] = synthesis

    confidence = str(plan.get("confidence", "")).strip().lower()
    if confidence not in _VALID_CONFIDENCE:
        confidence = "medium"
    plan["confidence"] = confidence

    # top_k clamp
    try:
        top_k = int(plan.get("top_k") or 0)
    except (TypeError, ValueError):
        top_k = 0
    if top_k < 3:
        top_k = 5
    if top_k > 20:
        top_k = 20
    plan["top_k"] = top_k

    plan["rerank"] = bool(plan.get("rerank", True))

    # filters
    filters = plan.get("filters") or {}
    if not isinstance(filters, dict):
        filters = {}
    metadata = filters.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    time_range = filters.get("time_range")
    if not isinstance(time_range, dict):
        time_range = None
    plan["filters"] = {"metadata": metadata, "time_range": time_range}

    assumptions = plan.get("assumptions") or []
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)]
    plan["assumptions"] = [str(a).strip() for a in assumptions if str(a).strip()]

    logger.info(
        f"📋 [RETR_PLAN] strategy={plan['retrieval_strategy']} "
        f"sub_questions={len(plan['sub_questions'])} top_k={plan['top_k']} "
        f"synthesis={plan['synthesis_style']} confidence={plan['confidence']}"
    )
    return plan


def format_retrieval_plan_for_log(plan: Optional[Dict[str, Any]]) -> str:
    """Short one-line summary for logs / audit entries."""
    if not plan:
        return "(no retrieval plan)"
    return (
        f"strategy={plan.get('retrieval_strategy')} "
        f"subs={len(plan.get('sub_questions') or [])} "
        f"top_k={plan.get('top_k')} "
        f"synthesis={plan.get('synthesis_style')} "
        f"confidence={plan.get('confidence')}"
    )
