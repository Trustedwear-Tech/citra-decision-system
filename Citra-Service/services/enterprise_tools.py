# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Enterprise MCP Tools — Layer 2 of the unified enterprise-MCP architecture.

Surfaces each registered dept-MCP source as an OpenAI function-calling tool
(`dept_<source>`) and provides the dispatch + agentic tool-loop helpers used
by chat (streaming) and composer / presentation / printable (non-streaming).

The low-level discovery + REST plumbing lives in
`services.enterprise_mcp_client` (Layer 1). This module composes those
primitives into LLM-facing patterns.

Public API:
  • build_enterprise_tool_schemas(...)  — discover + emit OpenAI tool schemas
  • dispatch_enterprise_tool(...)       — execute one `dept_*` tool call
  • run_Enterprise_or_Personal_tool(...)      — full agentic loop (LLM picks tools,
                                          dispatches, synthesises answer)
  • _filter_tool_schemas_via_llm(...)   — optional pre-filter when too many
                                          tools would distract the main LLM

Defaults / limits (overridable via env):
  - At most `MAX_TOOLS` schemas exposed (ENTERPRISE_MAX_TOOLS, default 10)
  - Per-call result count capped at `MAX_RESULTS_PER_CALL` (default 25)
  - Per-result text truncated to `MAX_TEXT_CHARS` (default 800)
  - Tool names sanitised to `^[a-zA-Z0-9_-]{1,64}$` (OpenAI requirement)
  - Description truncated to 1024 chars
  - Auto-filter triggers when discovered tools > AUTO_FILTER_THRESHOLD (8)
  - When ``query`` is supplied, the discovered tool list is reranked by
    NL relevance against the reranker-service before truncation, so the
    most relevant ``MAX_TOOLS`` are kept (instead of an arbitrary first-N).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Limits ─────────────────────────────────────────────────────────────────
MAX_TOOLS = int(os.getenv("ENTERPRISE_MAX_TOOLS", "10"))
MAX_RESULTS_PER_CALL = int(os.getenv("ENTERPRISE_MAX_RESULTS_PER_CALL", "25"))
DEFAULT_RESULTS_PER_CALL = int(os.getenv("ENTERPRISE_DEFAULT_RESULTS_PER_CALL", "10"))
MAX_TEXT_CHARS = int(os.getenv("ENTERPRISE_MAX_TEXT_CHARS", "800"))
MAX_DESC_CHARS = 1024

_NAME_SANITISE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slugify_tool_name(raw: str) -> str:
    """Coerce an arbitrary string into an OpenAI-compatible function name fragment."""
    if not raw:
        return "source"
    slug = _NAME_SANITISE_RE.sub("_", raw).strip("_-")
    return slug[:50] or "source"


def _build_tool_description(tool_def: Dict[str, Any]) -> str:
    """Build a description that helps the LLM pick the right source."""
    base = (tool_def.get("description") or "").strip()
    data_types = tool_def.get("data_types") or []
    tags = tool_def.get("tags") or []
    extras: List[str] = []
    if data_types:
        extras.append(f"Data types: {', '.join(str(d) for d in data_types[:8])}")
    if tags:
        extras.append(f"Tags: {', '.join(str(t) for t in tags[:8])}")
    suffix = " | ".join(extras)
    desc = base if not suffix else (f"{base} ({suffix})" if base else suffix)
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[: MAX_DESC_CHARS - 1] + "…"
    return desc or "Enterprise data source"

# ───────────────────────────────────────────────────────────────────────────
# Schema builder
# ───────────────────────────────────────────────────────────────────────────

async def build_enterprise_tool_schemas(
    *,
    org_id: Optional[str],
    dept_id: Optional[str] = None,
    dept_ids: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
    jwt_token: Optional[str] = None,
    max_results_cap: Optional[int] = None,
    query: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Discover enterprise MCP tools for this user scope and emit OpenAI function-
    calling schemas.

    ``max_results_cap`` lets the caller (composer/presentation/printable)
    tighten the per-call result budget below the global default. Falls back
    to ``MAX_RESULTS_PER_CALL`` when None.

    ``query`` (the user's turn / section prompt) enables NL relevance
    reranking against tool descriptions so the most relevant ``MAX_TOOLS``
    are exposed to the LLM. Without ``query`` (or for short / trivial
    queries) the first ``MAX_TOOLS`` in discovery order are used.

    Returns:
        (schemas, name_map) where:
          - schemas: list of OpenAI tool defs (`{type: "function", function: {...}}`)
          - name_map: tool_name → original tool_def (for dispatch lookup)

    Returns ([], {}) on any failure or when no tools are discovered.
    """
    try:
        from services.enterprise_mcp_client import (
            discover_tools as _discover_tools,
            search_tools as _search_tools,
            DiscoveryUnavailableError,
        )
    except Exception as exc:
        logger.warning(f"⚠️ [ENT_TOOLS] Could not import discovery helpers: {exc}")
        return [], {}

    if dept_ids is None:
        dept_ids = [dept_id] if dept_id else []

    # Query-aware ranking via discovery-service's /tools/search. Falls
    # back to the legacy list-all-then-truncate path when:
    #   * no query is supplied (e.g. composer warm-up at session start)
    #   * query is too short for embedding to be useful (< 8 chars —
    #     "hi", "test" produce noisy similarity scores)
    #   * /tools/search is unreachable (returns [] → we fall back)
    q = (query or "").strip()
    tool_defs: List[Dict[str, Any]] = []
    if len(q) >= 8:
        tool_defs = await _search_tools(
            query=q,
            top_k=MAX_TOOLS,
            jwt_token=jwt_token,
        )

    if not tool_defs:
        try:
            tool_defs = await _discover_tools(
                org_id=org_id,
                dept_ids=dept_ids,
                roles=roles or ["user"],
                jwt_token=jwt_token,
            )
        except DiscoveryUnavailableError:
            # Discovery is DOWN (outage), not "no sources". Fail loud — do NOT
            # return [], {} here: that would silently drop every enterprise tool
            # and let the chat answer from general knowledge as if the org had no
            # data. Propagate so the caller surfaces it to the user (RULE #1).
            logger.error("❌ [ENT_TOOLS] Discovery unavailable — propagating (not degrading to zero tools)")
            raise
        except Exception as exc:
            # Any other discovery failure is still a real failure → fail loud.
            logger.error(f"❌ [ENT_TOOLS] Discovery failed: {exc}")
            raise DiscoveryUnavailableError(f"discovery failed: {exc}") from exc
        tool_defs = tool_defs[:MAX_TOOLS]

    if not tool_defs:
        # No structured MCP tools — but semantic (RAG) sources are discovered
        # separately below (from dept_sources, not the MCP), so DON'T return here.
        logger.info("ℹ️ [ENT_TOOLS] No structured MCP tools for this scope; "
                    "checking semantic sources.")

    effective_cap = max(1, min(int(max_results_cap or MAX_RESULTS_PER_CALL), MAX_RESULTS_PER_CALL))
    effective_default = min(DEFAULT_RESULTS_PER_CALL, effective_cap)

    schemas: List[Dict[str, Any]] = []
    name_map: Dict[str, Dict[str, Any]] = {}
    used_names: set = set()

    def _alloc_tool_name(sid: str) -> str:
        slug = _slugify_tool_name(sid)
        tool_name = f"dept_{slug}"[:64]
        suffix = 2
        while tool_name in used_names:  # disambiguate duplicates
            tool_name = f"dept_{slug}_{suffix}"[:64]
            suffix += 1
        used_names.add(tool_name)
        return tool_name

    def _semantic_params() -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural-language search query for this document / policy "
                        "source. Pass the user's question (or a focused sub-question) "
                        "verbatim; the platform reranks the most relevant passages."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        f"Number of passages to return (default {effective_default}, "
                        f"max {effective_cap})."
                    ),
                    "minimum": 1,
                    "maximum": effective_cap,
                },
                "doc_path": {
                    "type": "string",
                    "description": (
                        "Optional. To read or summarize an ENTIRE specific document, "
                        "pass its doc_path (found in a prior result's metadata.doc_path, "
                        "e.g. 'policy/dt_failure_response_sop.md'). This returns ALL "
                        "sections of that ONE document in order — use it instead of a "
                        "keyword query when the user wants the whole document, since a "
                        "plain search only returns the top-matching passages and can miss "
                        "a document's later sections."
                    ),
                },
            },
            "required": ["query"],
        }

    # Single discovery-driven loop. Discovery IS chat's source of truth: the MCP
    # publishes every source there at boot, each flagged with source_type. We
    # branch on that flag — NOT on the presence of a query_endpoint — so a RAG
    # source is never mistaken for a structured one:
    #   • source_type == "semantic" → RAG corpus. Answered IN-PROCESS by the
    #     platform reader (Milvus direct), NEVER the dept-MCP /query (pure
    #     disconnect). The registration carries rag_collection + dept, so we read
    #     the published Milvus collection directly.
    #   • everything else → structured MCP tool, dispatched to the dept-MCP /query.
    sem_count = 0
    for tool_def in tool_defs[:MAX_TOOLS]:
        source_id = tool_def.get("source_id") or tool_def.get("name") or ""
        if not source_id:
            continue
        kind = str(tool_def.get("source_type") or tool_def.get("kind") or "").strip().lower()

        if kind == "semantic":
            tool_name = _alloc_tool_name(source_id)
            dept_ids_for_tool = tool_def.get("dept_ids") or []
            sem_def = {
                "source_id": source_id,
                "kind": "semantic",
                "dept_id": dept_ids_for_tool[0] if dept_ids_for_tool else tool_def.get("dept_id"),
                # org bounds the Milvus read (data-layer isolation).
                "org_ids": tool_def.get("org_ids") or [],
                # The MCP publishes the authoritative Milvus collection to fetch
                # directly — no /query round-trip. None ⇒ reader derives it.
                "rag_collection": tool_def.get("rag_collection"),
                "name": tool_def.get("name"),
                "description": tool_def.get("description"),
                "data_types": tool_def.get("data_types"),
                "tags": tool_def.get("tags"),
                "taxonomy": tool_def.get("taxonomy"),
            }
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": _build_tool_description(sem_def),
                    "parameters": _semantic_params(),
                },
            })
            name_map[tool_name] = sem_def
            sem_count += 1
            continue

        # Structured MCP tool — must carry a query_endpoint to be callable.
        if not tool_def.get("query_endpoint"):
            continue
        tool_name = _alloc_tool_name(source_id)
        schemas.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": _build_tool_description(tool_def),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Natural-language search query for this enterprise data "
                                "source. Pass the user's question (or a focused sub-question) "
                                "verbatim; the source's own retriever will rank rows/chunks."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Number of results to return (default {effective_default}, "
                                f"max {effective_cap})."
                            ),
                            "minimum": 1,
                            "maximum": effective_cap,
                        },
                        # Phase B3 — catalogue-keyed dataset pinning. The LLM
                        # may pass specific dataset_ids it already knows it
                        # needs (e.g. from a prior describe call or from the
                        # action's data_bindings). When omitted, the dept-mcp
                        # planner picks datasets via the catalogue index.
                        "dataset_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of catalogue dataset_ids to pin the planner to. "
                                "Leave empty to let the dept-MCP catalogue index pick."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
        })
        name_map[tool_name] = tool_def

    logger.info(
        f"✅ [ENT_TOOLS] Built {len(schemas)} enterprise tool schemas from discovery "
        f"({len(schemas) - sem_count} structured + {sem_count} semantic, capped={MAX_TOOLS})"
    )
    return schemas, name_map


# ───────────────────────────────────────────────────────────────────────────
# Dispatcher
# ───────────────────────────────────────────────────────────────────────────

def _truncate_text(text: Any, limit: int = MAX_TEXT_CHARS) -> str:
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _shape_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Compress a single MCP result dict into something LLM-friendly."""
    out: Dict[str, Any] = {
        "text": _truncate_text(raw.get("text", "")),
        "score": raw.get("score"),
        "source": raw.get("source"),
    }
    md = raw.get("metadata") or {}
    if isinstance(md, dict) and md:
        # Keep only small scalars and short lists; drop huge nested blobs
        compact_md: Dict[str, Any] = {}
        for k, v in md.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                if isinstance(v, str) and len(v) > 200:
                    compact_md[k] = v[:199] + "…"
                else:
                    compact_md[k] = v
            elif isinstance(v, (list, tuple)) and len(v) <= 10:
                compact_md[k] = list(v)[:10]
        if compact_md:
            out["metadata"] = compact_md
    return out


async def dispatch_enterprise_tool(
    *,
    name: str,
    args: Dict[str, Any],
    name_map: Dict[str, Dict[str, Any]],
    jwt_token: Optional[str] = None,
    user_id: Optional[str] = None,
    max_results_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Execute one `dept_*` tool call against the registered MCP endpoint.

    Always returns a dict; never raises. Errors are surfaced as
    `{"success": False, "error": "..."}` so the LLM can recover.

    ``max_results_cap`` lets the caller enforce a tighter per-call budget
    than the global default (composer/presentation/printable use this).
    """
    tool_def = name_map.get(name)
    if not tool_def:
        return {"success": False, "error": f"Unknown enterprise tool: {name}"}

    query = (args or {}).get("query", "")
    # A whole-document read (doc_path) needs no query — the tool description tells
    # the LLM to pass doc_path INSTEAD of a query. Only SEMANTIC tools expose
    # doc_path, so accept a doc_path-only call ONLY for them; a structured tool
    # (or a hallucinated doc_path on one) still requires a real query.
    _is_semantic = str((tool_def or {}).get("kind") or "").lower() == "semantic"
    _dp = (args or {}).get("doc_path")
    _has_doc_path = _is_semantic and isinstance(_dp, str) and bool(_dp.strip())
    if (not isinstance(query, str) or not query.strip()) and not _has_doc_path:
        return {"success": False, "error": "Missing 'query' (or 'doc_path' for a whole-document read)."}

    effective_cap = max(1, min(int(max_results_cap or MAX_RESULTS_PER_CALL), MAX_RESULTS_PER_CALL))
    effective_default = min(DEFAULT_RESULTS_PER_CALL, effective_cap)
    try:
        max_results = int((args or {}).get("max_results") or effective_default)
    except (TypeError, ValueError):
        max_results = effective_default
    max_results = max(1, min(max_results, effective_cap))

    # ── RAG short-circuit: a kind=semantic tool is answered IN-PROCESS ─────────
    # by the platform reader (this runs inside Citra-Service), NEVER the dept-MCP
    # (pure disconnect). The source was authz'd at surface time by discovery's
    # visibility gate (_tool_visible_to) — same gate as structured tools — so it
    # only became a tool for callers allowed to see it.
    if str((tool_def or {}).get("kind") or "").lower() == "semantic":
        sem_source_id = tool_def.get("source_id", "")
        # The MCP published the exact Milvus collection (rag_collection); None ⇒
        # the reader derives <prefix>_<dept>_<source>.
        rag_collection = tool_def.get("rag_collection")
        # dept + org bound the Milvus read to the caller's own data (data-layer
        # isolation) — discovery already gave us the source's authoritative scope.
        sem_dept_id = tool_def.get("dept_id")
        _org_ids = tool_def.get("org_ids") or []
        sem_org_id = _org_ids[0] if _org_ids else tool_def.get("org_id")
        # A doc_path scopes to ONE whole document (all sections, ordered) — the
        # "read/summarize the entire SOP" path that top-k search can't satisfy.
        doc_path = (args or {}).get("doc_path")
        doc_path = doc_path.strip() if isinstance(doc_path, str) else ""
        try:
            if doc_path:
                from semantic_reader import fetch_document
                # A whole-document read returns ALL sections — do NOT clamp to the
                # display max_results (that would drop later sections). The reader
                # pages the full doc up to its own safety cap.
                chunks = await fetch_document(
                    source_id=sem_source_id, dept_id=sem_dept_id, org_id=sem_org_id,
                    doc_path=doc_path, collection=rag_collection,
                )
            else:
                from semantic_reader import semantic_search
                chunks = await semantic_search(
                    source_id=sem_source_id, dept_id=sem_dept_id, org_id=sem_org_id,
                    query=query.strip(), top_k=max_results, collection=rag_collection,
                )
        except Exception as exc:  # noqa: BLE001 — surface as tool error, never crash the loop
            logger.error(f"❌ [ENT_TOOLS] {name} semantic {'doc-fetch' if doc_path else 'search'} failed: {exc}")
            return {"success": False, "error": f"Semantic retrieval failed: {exc}",
                    "source_id": sem_source_id}
        shaped = [_shape_result(c) for c in chunks if isinstance(c, dict)]
        logger.info(
            f"✅ [ENT_TOOLS] {name} → {len(shaped)} passages "
            f"(semantic, source_id={sem_source_id}, user={user_id})")
        return {"success": True, "source_id": sem_source_id, "results": shaped,
                "total": len(chunks), "returned": len(shaped), "truncated": False}

    try:
        from services.enterprise_mcp_client import call_tool as _call_dept_mcp, service_api_key as _service_api_key
    except Exception as exc:
        logger.error(f"❌ [ENT_TOOLS] Could not import enterprise_mcp_client: {exc}")
        return {"success": False, "error": "Enterprise dispatch unavailable."}

    # Forward the caller's HS256 session token straight through as X-User-JWT.
    # The dept-mcp verifies it against the shared Citra HS256 secret; the
    # service-to-service Authorization key (api_key below) is the second guard.
    user_jwt_for_mcp = jwt_token

    api_key = _service_api_key()
    query_endpoint = tool_def.get("query_endpoint", "")
    source_id = tool_def.get("source_id", "")

    # Optional catalogue dataset_ids hint from the LLM.
    raw_dataset_ids = (args or {}).get("dataset_ids")
    dataset_ids: Optional[List[str]] = None
    if isinstance(raw_dataset_ids, list):
        dataset_ids = [str(d) for d in raw_dataset_ids if isinstance(d, (str, int))]
        dataset_ids = [d for d in dataset_ids if d.strip()] or None

    try:
        raw_results = await _call_dept_mcp(
            query_endpoint=query_endpoint,
            query=query.strip(),
            source_id=source_id,
            api_key=api_key,
            tool_def=tool_def,
            tool_id=tool_def.get("tool_id"),
            user_jwt=user_jwt_for_mcp,
            max_results=max_results,
            dataset_ids=dataset_ids,
        )
    except Exception as exc:
        logger.error(f"❌ [ENT_TOOLS] {name} dispatch failed: {exc}")
        return {"success": False, "error": f"Tool call failed: {exc}", "source_id": source_id}

    raw_results = raw_results or []
    total = len(raw_results)
    truncated = total > max_results
    sliced = raw_results[:max_results]
    shaped = [_shape_result(r) for r in sliced if isinstance(r, dict)]

    logger.info(
        f"✅ [ENT_TOOLS] {name} → {len(shaped)}/{total} results "
        f"(source_id={source_id}, user={user_id})"
    )
    return {
        "success": True,
        "source_id": source_id,
        "results": shaped,
        "total": total,
        "returned": len(shaped),
        "truncated": truncated,
    }


# ───────────────────────────────────────────────────────────────────────────
# Layer 2 — high-level agentic tool-loop helper
#
# Used by composer / presentation / printable (non-streaming features) so they
# share the chat's `dept_*` tool-call pattern. Main chat keeps its own loop in
# streaming_response.py because it needs SSE event yielding; both paths call
# the same Layer 1 primitives (discover + dispatch), so they remain aligned
# at the protocol layer.
# ───────────────────────────────────────────────────────────────────────────

# Default complexity threshold above which we LLM-pre-filter the tool list.
# Below this, the main LLM can scan all tools cheaply; above, the filter call
# pays for itself in saved main-LLM tokens and reduced distraction.
AUTO_FILTER_THRESHOLD = int(os.getenv("ENTERPRISE_AUTO_FILTER_THRESHOLD", "8"))


async def _filter_tool_schemas_via_llm(
    query: str,
    schemas: List[Dict[str, Any]],
    name_map: Dict[str, Dict[str, Any]],
    *,
    tier: str = "small",
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Single small-LLM call to pick which dept_* tools are relevant for `query`.

    Returns ``(filtered_schemas, filtered_name_map)``. On any failure or
    when the LLM picks none, falls back to the original (unfiltered) lists
    so the main LLM still has a shot at finding the right source.
    """
    if not schemas or len(schemas) <= AUTO_FILTER_THRESHOLD:
        return schemas, name_map
    try:
        import json
        from citra_llm import get_llm_client, get_llm_model
        client = get_llm_client(async_=True, tier=tier)
        model = get_llm_model(tier=tier)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a tool router. Given the user's query, call ONLY the "
                    "tools whose descriptions indicate they contain relevant data. "
                    "If multiple tools look relevant, call them all in parallel. "
                    "If none are relevant, do not call any. Pass the user's query "
                    "verbatim as the `query` argument."
                ),
            },
            {"role": "user", "content": query},
        ]
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=schemas,
            tool_choice="auto",
            temperature=0,
            # Generous, cost-neutral cap: on a reasoning model the tool-selection
            # step still spends hidden reasoning tokens, and 2048 could truncate
            # before the tool_calls land. The model stops when done.
            max_tokens=16000,
        )
        msg = response.choices[0].message if response.choices else None
        picked: List[str] = []
        if msg and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                fn_name = getattr(tc.function, "name", None)
                if fn_name and fn_name in name_map and fn_name not in picked:
                    picked.append(fn_name)
        if not picked:
            logger.info(
                f"🤖 [ENT_TOOLS] Filter LLM picked 0/{len(schemas)} — "
                f"falling back to full tool list"
            )
            return schemas, name_map
        filtered_schemas = [
            s for s in schemas if s.get("function", {}).get("name") in picked
        ]
        filtered_name_map = {n: name_map[n] for n in picked if n in name_map}
        logger.info(
            f"🤖 [ENT_TOOLS] Filter LLM picked {len(filtered_schemas)}/{len(schemas)} "
            f"tools: {picked}"
        )
        return filtered_schemas, filtered_name_map
    except Exception as exc:
        logger.warning(
            f"⚠️ [ENT_TOOLS] Tool-filter LLM call failed ({exc}); "
            f"using all {len(schemas)} discovered tools"
        )
        return schemas, name_map


async def run_Enterprise_or_Personal_tool(
    prompt: str,
    *,
    system: str = "You are a helpful AI assistant.",
    org_id: Optional[str] = None,
    dept_id: Optional[str] = None,
    dept_ids: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    jwt_token: Optional[str] = None,
    extra_tools: Optional[List[Dict[str, Any]]] = None,
    extra_tool_dispatch=None,
    max_rounds: int = 5,
    tier: str = "large",
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 16000,
    filter_tools: Any = "auto",
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    use_personal_data: bool = False,
    selected_folder_ids: Optional[List[str]] = None,
    persona_data: Optional[Dict[str, Any]] = None,
    max_results_cap: Optional[int] = None,
    expose_enterprise_tools: bool = True,
    has_unstructured_files: Optional[bool] = None,
    personal_tool_expand_subqueries: bool = True,
) -> str:
    """
    Run a one-shot LLM call backed by `dept_*` MCP tools (and any extras) and
    return the final answer text. Non-streaming.

    Use this from features that don't need SSE streaming (composer sections,
    presentation outline expansion, printable generation). For chat use the
    streaming path in `streaming_response.py` — both share Layer 1 primitives,
    so the architecture is aligned at the protocol layer.

    Parameters
    ----------
    prompt : str
        The user-level instruction (becomes the final `user` message).
    system : str
        System prompt for this LLM call.
    org_id, dept_id, dept_ids, roles, jwt_token : str | list[str]
        Identity scope passed to discovery and propagated to dept-MCPs.
    extra_tools : list[dict]
        Additional OpenAI-format tool schemas (e.g. web_search, execute_code).
        These are appended to the dept_* schemas; the model can call them
        interchangeably.
    extra_tool_dispatch : async callable
        ``async def dispatch(name, args) -> str`` — called when the LLM picks
        a tool that ISN'T in `name_map`. The returned string is sent back as
        the tool-call result. None means non-dept tool calls return an error.
    max_rounds : int
        Max LLM round-trips before forcing a final non-tool answer.
    tier, model, temperature, max_tokens : LLM call parameters.
    filter_tools : True | False | "auto"
        Pre-filter the tool list via a small LLM call. ``"auto"`` filters when
        discovered tool count > AUTO_FILTER_THRESHOLD; else exposes all.
    conversation_history : list[dict]
        Optional prior turns prepended before the final user prompt.

    Returns
    -------
    str : the model's final answer (concatenated text from all rounds).
    """
    import json
    from citra_llm import get_llm_client, get_llm_model, get_llm_extra_body

    # 1. Discover + (optionally) filter dept_* tool schemas. The per-call
    # result cap is applied to BOTH the schema (so the LLM sees the lower
    # max_results in its tool description) and the dispatcher (so the cap
    # is enforced even if the LLM ignores it in args).
    if expose_enterprise_tools:
        dept_schemas, name_map = await build_enterprise_tool_schemas(
            org_id=org_id,
            dept_id=dept_id,
            dept_ids=dept_ids,
            roles=roles,
            jwt_token=jwt_token,
            max_results_cap=max_results_cap,
            query=prompt,
        )
        if filter_tools is True or (filter_tools == "auto" and len(dept_schemas) > AUTO_FILTER_THRESHOLD):
            dept_schemas, name_map = await _filter_tool_schemas_via_llm(
                prompt, dept_schemas, name_map
            )
    else:
        dept_schemas, name_map = [], {}

    tools: List[Dict[str, Any]] = list(dept_schemas)

    # personal_data_tool is not registered any more: the personal vault was
    # removed, so the only tools on this surface are the dept_* MCP tools and
    # whatever the caller passes as extra_tools.

    if extra_tools:
        tools.extend(extra_tools)

    # 2. Build messages
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    if conversation_history:
        for h in conversation_history:
            role = h.get("role", "user")
            content = (h.get("content") or "").strip()
            if content:
                messages.append({"role": role if role in ("user", "assistant") else "user", "content": content})
    messages.append({"role": "user", "content": prompt})

    # 3. Tool-call loop
    client = get_llm_client(async_=True, tier=tier)
    use_model = model or get_llm_model(tier=tier)
    extra_body = get_llm_extra_body(use_model, tier=tier)

    final_text_parts: List[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    for round_num in range(max_rounds + 1):
        kwargs: Dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error(f"❌ [ENT_TOOLS] LLM call failed (round {round_num}): {exc}")
            break

        usage = getattr(resp, "usage", None)
        if usage:
            total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
            total_output_tokens += getattr(usage, "completion_tokens", 0) or 0

        choice = resp.choices[0] if resp.choices else None
        if not choice:
            break
        msg = choice.message
        text = (msg.content or "").strip() if msg else ""
        tool_calls = getattr(msg, "tool_calls", None) if msg else None

        if text:
            final_text_parts.append(text)

        if not tool_calls:
            break  # Model has produced its final answer
        if round_num >= max_rounds:
            break  # Out of rounds; whatever text we have is the answer

        # Append the assistant message (with tool_calls) so the model can see
        # its own request when it processes the tool results in the next round.
        messages.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        # Dispatch each tool call
        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if fn_name in name_map:
                result = await dispatch_enterprise_tool(
                    name=fn_name,
                    args=args,
                    name_map=name_map,
                    jwt_token=jwt_token,
                    user_id=user_id,
                    max_results_cap=max_results_cap,
                )
                content_str = json.dumps(result)
            elif extra_tool_dispatch is not None:
                try:
                    content_str = await extra_tool_dispatch(fn_name, args)
                except Exception as exc:
                    content_str = json.dumps({"success": False, "error": str(exc)})
                if not isinstance(content_str, str):
                    content_str = json.dumps(content_str)
            else:
                content_str = json.dumps(
                    {"success": False, "error": f"Unknown tool: {fn_name}"}
                )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": content_str,
            })

    # If the model only emitted tool calls and never produced text, run one
    # final tool-less round to force a synthesis. This mirrors the chat path's
    # forced-final fallback.
    if not final_text_parts:
        try:
            resp = await client.chat.completions.create(
                model=use_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body or None,
            )
            choice = resp.choices[0] if resp.choices else None
            if choice and choice.message and choice.message.content:
                final_text_parts.append(choice.message.content.strip())
            usage = getattr(resp, "usage", None)
            if usage:
                total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
        except Exception as exc:
            logger.error(f"❌ [ENT_TOOLS] Final synthesis call failed: {exc}")

    # Track usage for billing (best-effort; never raises)
    if user_id and (total_input_tokens or total_output_tokens):
        try:
            from middleware.credit_check_middleware import track_query_usage
            track_query_usage(
                user_id=user_id,
                email=user_email or user_id,
                model=use_model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cached_tokens=0,
                internet_grounding_cost=0.0,
            )
        except Exception as exc:
            logger.debug(f"[ENT_TOOLS] usage tracking skipped: {exc}")

    return "\n\n".join(p for p in final_text_parts if p)


__all__ = [
    "build_enterprise_tool_schemas",
    "dispatch_enterprise_tool",
    "run_Enterprise_or_Personal_tool",
    "AUTO_FILTER_THRESHOLD",
    "MAX_TOOLS",
    "MAX_RESULTS_PER_CALL",
]
