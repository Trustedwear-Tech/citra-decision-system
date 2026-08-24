# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Streaming Response Module
=========================

This module provides async generators for streaming LLM responses from various providers.
It enables real-time token-by-token response delivery to clients via Server-Sent Events (SSE).

Architecture:
- Unified streaming via OpenAI-compatible API (on-prem LLM)
- All functions are async generators that yield response chunks
- Token tracking is performed after stream completion
- Special handling for mermaid diagrams and citations
"""

import asyncio
import logging
import os
import json
import re
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from citra_llm import get_llm_client, get_default_model, get_llm_extra_body
from llm.llm_tiers import DEFAULT_TIER, Tier, coerce_tier
from token_utils import MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS_DEFAULT, MAX_OUTPUT_TOKENS_CHAT, MAX_OUTPUT_TOKENS_CEILING, count_tokens, truncate_to_token_limit

logger = logging.getLogger(__name__)
logger.info("🔄 [STREAMING_RESPONSE] module loaded (rev: timing-markers-v2)")

# ═══════════════════════════════════════════════════════════════════════════════════════
# STREAMING EVENT TYPES
# ═══════════════════════════════════════════════════════════════════════════════════════

class StreamEventType(str, Enum):
    """Event types for SSE streaming"""
    CONTEXT_READY = "context_ready"      # Context retrieval complete
    CHUNK = "chunk"                       # Text chunk from LLM
    MERMAID_BLOCK = "mermaid_block"       # Complete mermaid diagram
    CITATIONS = "citations"               # Citation data
    GROUNDING = "grounding"                # Anti-hallucination telemetry (has_context, scores, internet_used, critic)
    USAGE = "usage"                        # Token usage stats
    OUTPUT_FILES = "output_files"          # Downloadable output files from code execution
    ARTIFACT = "artifact"                  # Single structured downloadable artifact ({kind, title, filename, url, size, summary}) — emitted when a request is bridged to Action Chat / Deep Research so the originating chat surface can render a download card inline. Distinct from OUTPUT_FILES (batch, code-exec) to keep payload shapes unambiguous.
    TOOL_STATUS = "tool_status"              # Tool execution status (running/complete)
    SUGGESTION_CHIPS = "suggestion_chips"    # Clickable suggestions for ambiguous queries
    STAGE = "stage"                        # High-level processing stage for the live timeline UI
    PLAN = "plan"                          # Captured <plan>...</plan> block as a structured event
    REASONING = "reasoning"                # Model chain-of-thought / thinking tokens, streamed as they arrive. Surfaced in a collapsible "Reasoning" card (mirrors Action Chat's reasoning panel). Gated by LLM_EXPOSE_REASONING.
    OPEN_BUILDER = "open_builder"          # Hand off to the presentation / printable composer UI. Payload: {builder: "presentation"|"report", goal, slide_count, prefetched_corpus: [{text, title?}]}. QUICK CHAT ONLY (api/quick_chat.py) — MAIN CHAT NEVER EMITS THIS: the builder tools were removed from stream_llm_response_impl on purpose, so "create a presentation / report" in main chat is answered inline and launches no composer.
    ERROR = "error"                        # Error occurred
    DONE = "done"                          # Stream complete


@dataclass
class StreamEvent:
    """Represents a streaming event"""
    event_type: StreamEventType
    data: Dict[str, Any]

    def to_sse(self) -> str:
        """Convert to SSE format"""
        return f"event: {self.event_type.value}\ndata: {json.dumps(self.data)}\n\n"


def reasoning_exposed() -> bool:
    """Whether the model's reasoning/thinking tokens should be surfaced to the
    UI as ``reasoning`` SSE events (Action-Chat-style collapsible panel).

    Controlled by ``LLM_EXPOSE_REASONING`` (default ``true``). When disabled,
    reasoning stays excluded at the provider (``reasoning.exclude=true``) and is
    only used as a last-resort empty-content fallback — the historical
    behaviour. Set to ``false`` for deployments where surfacing chain-of-thought
    on tool-planning rounds is a governance concern.
    """
    return os.getenv("LLM_EXPOSE_REASONING", "true").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


def extract_reasoning_delta(delta) -> str:
    """Pull reasoning/thinking text out of a streaming ``delta`` regardless of
    where the provider puts it: ``delta.reasoning`` / ``delta.reasoning_content``
    (string), or ``delta.reasoning_details`` (OpenRouter's list of structured
    dicts). Also peeks into pydantic ``model_extra`` for SDK-undeclared fields.
    Returns ``""`` when the delta carries no reasoning.
    """
    _extra_fields = getattr(delta, "model_extra", None) or {}
    _r = (
        getattr(delta, "reasoning", None)
        or getattr(delta, "reasoning_content", None)
        or _extra_fields.get("reasoning")
        or _extra_fields.get("reasoning_content")
    )
    if isinstance(_r, str) and _r:
        return _r
    _rd = getattr(delta, "reasoning_details", None) or _extra_fields.get("reasoning_details")
    if isinstance(_rd, list):
        parts = []
        for item in _rd:
            if isinstance(item, dict):
                txt = item.get("text") or item.get("summary") or item.get("content")
            else:
                txt = (
                    getattr(item, "text", None)
                    or getattr(item, "summary", None)
                    or getattr(item, "content", None)
                )
            if isinstance(txt, str) and txt:
                parts.append(txt)
        return "".join(parts)
    return ""


def apply_reasoning_visibility(extra_body: dict) -> dict:
    """Return a copy of ``extra_body`` with ``reasoning.exclude`` set to match
    the :func:`reasoning_exposed` policy.

    - exposed  → ``reasoning.exclude=false`` so the provider streams thinking
      tokens on ``delta.reasoning*`` (we emit them as REASONING events).
    - hidden   → ``reasoning.exclude=true`` (defense-in-depth: keeps hybrid
      reasoning models from starving ``content`` by dumping the answer onto
      ``reasoning_details``).

    ``reasoning.enabled`` / ``effort`` are left untouched (env-driven), so even
    when hidden the model can still think briefly before answering.
    """
    if not extra_body:
        return extra_body
    out = dict(extra_body)
    if isinstance(out.get("reasoning"), dict):
        out["reasoning"] = {**out["reasoning"], "exclude": not reasoning_exposed()}
    return out


@dataclass
class StreamingState:
    """Tracks state during streaming for mermaid/citation detection"""
    buffer: str = ""
    in_mermaid_block: bool = False
    mermaid_buffer: str = ""
    total_output_tokens: int = 0
    mermaid_blocks: List[Dict[str, Any]] = field(default_factory=list)


class _PlanBlockFilter:
    """Streaming filter that strips ``<plan>...</plan>`` blocks from text and
    captures their content as structured payloads.

    The plan is ALWAYS stripped from the visible CHUNK stream so it does not
    render as raw tags inside the assistant's answer. Captured plan blocks
    are exposed via :meth:`pop_plans` so the caller can re-emit them as a
    typed ``plan`` SSE event for the timeline / plan-card UI.

    The legacy ``suppress`` flag still controls whether captured plans are
    surfaced to ``pop_plans`` (used to suppress duplicate plans the LLM may
    re-emit on later tool rounds). When ``suppress=True`` the plan is still
    stripped but NOT surfaced.

    Handles tags landing on chunk boundaries via a small carry-over buffer.
    """

    _OPEN = "<plan>"
    _CLOSE = "</plan>"

    def __init__(self, suppress: bool = False):
        # `suppress` is now "don't surface captured plans to pop_plans". The
        # filter ALWAYS strips <plan> blocks from visible output.
        self.suppress = suppress
        self.in_plan = False
        self.carry = ""
        self._block_buffer = ""
        self._captured: List[str] = []

    def feed(self, chunk: str) -> str:
        text = self.carry + chunk
        self.carry = ""
        out = []
        while text:
            if self.in_plan:
                idx = text.find(self._CLOSE)
                if idx == -1:
                    keep = len(self._CLOSE) - 1
                    if len(text) > keep:
                        # Stash everything except the carry-over tail in the
                        # block buffer so we can surface it later.
                        self._block_buffer += text[:-keep]
                        self.carry = text[-keep:]
                    else:
                        self.carry = text
                    return "".join(out)
                # Plan block closed in this chunk.
                self._block_buffer += text[:idx]
                if not self.suppress and self._block_buffer.strip():
                    self._captured.append(self._block_buffer.strip())
                self._block_buffer = ""
                text = text[idx + len(self._CLOSE):]
                self.in_plan = False
            else:
                idx = text.find(self._OPEN)
                if idx == -1:
                    keep = len(self._OPEN) - 1
                    if len(text) > keep:
                        out.append(text[:-keep])
                        self.carry = text[-keep:]
                    else:
                        self.carry = text
                    return "".join(out)
                out.append(text[:idx])
                text = text[idx + len(self._OPEN):]
                self.in_plan = True
        return "".join(out)

    def flush(self) -> str:
        if self.in_plan:
            # Drop any unterminated plan block; do NOT surface incomplete plans.
            self._block_buffer = ""
            self.carry = ""
            return ""
        out = self.carry
        self.carry = ""
        return out

    def pop_plans(self) -> List[str]:
        """Return and clear any complete plan blocks captured since last call."""
        out = self._captured
        self._captured = []
        return out


class ClarifyBlockFilter:
    """Streaming filter that strips ``<clarify>...</clarify>`` JSON blocks.

    The LLM emits a structured JSON block of the form::

        <clarify>{"message": "...", "options": [...]}</clarify>

    when the user's intent or a key value (filter literal, metric, date range)
    is ambiguous. The block is removed from the visible assistant text and the
    parsed JSON payload is exposed to the caller via :meth:`pop_payloads` so it
    can be re-emitted as a typed ``suggestion_chips`` SSE event.

    Handles tags landing on chunk boundaries via a small carry-over buffer.
    Non-JSON or malformed blocks are silently dropped (they won't render as
    text).
    """

    _OPEN = "<clarify>"
    _CLOSE = "</clarify>"

    def __init__(self) -> None:
        self.in_block = False
        self.block_buffer = ""
        self.carry = ""
        self._payloads: List[Dict[str, Any]] = []

    def feed(self, chunk: str) -> str:
        text = self.carry + chunk
        self.carry = ""
        out: List[str] = []
        while text:
            if self.in_block:
                idx = text.find(self._CLOSE)
                if idx == -1:
                    # Accumulate, but keep tail that could be a partial close tag.
                    keep = len(self._CLOSE) - 1
                    if len(text) > keep:
                        self.block_buffer += text[:-keep]
                        self.carry = text[-keep:]
                    else:
                        self.carry = text
                    return "".join(out)
                self.block_buffer += text[:idx]
                self._finalize_block()
                text = text[idx + len(self._CLOSE):]
                self.in_block = False
            else:
                idx = text.find(self._OPEN)
                if idx == -1:
                    keep = len(self._OPEN) - 1
                    if len(text) > keep:
                        out.append(text[:-keep])
                        self.carry = text[-keep:]
                    else:
                        self.carry = text
                    return "".join(out)
                out.append(text[:idx])
                text = text[idx + len(self._OPEN):]
                self.in_block = True
                self.block_buffer = ""
        return "".join(out)

    def flush(self) -> str:
        # If we're still inside a block at flush time, the LLM never closed
        # it — drop the partial JSON.
        if self.in_block:
            self.block_buffer = ""
            self.carry = ""
            self.in_block = False
            return ""
        out = self.carry
        self.carry = ""
        return out

    def _finalize_block(self) -> None:
        raw = (self.block_buffer or "").strip()
        self.block_buffer = ""
        if not raw:
            return
        # Tolerate fenced code blocks inside the tag, just in case.
        if raw.startswith("```"):
            raw = raw.strip("`")
            # drop leading "json\n" if present
            if raw.lower().startswith("json\n"):
                raw = raw[5:]
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                f"⚠️ [CLARIFY] Failed to parse <clarify> block: {exc}; raw={raw[:200]!r}"
            )
            return
        if not isinstance(payload, dict):
            logger.warning(
                f"⚠️ [CLARIFY] Expected dict in <clarify> block, got {type(payload).__name__}"
            )
            return
        # Lightweight schema validation; reject if no usable options.
        options = payload.get("options")
        if not isinstance(options, list) or not options:
            logger.warning("⚠️ [CLARIFY] <clarify> block has no options; ignoring")
            return
        normalized_options = []
        for idx, opt in enumerate(options):
            if isinstance(opt, str):
                normalized_options.append({
                    "id": f"opt_{idx}",
                    "label": opt,
                    "value": opt,
                })
                continue
            if not isinstance(opt, dict):
                continue
            value = opt.get("value") or opt.get("label")
            if not value:
                continue
            normalized_options.append({
                "id": str(opt.get("id") or f"opt_{idx}"),
                "label": str(opt.get("label") or value),
                "value": str(value),
                **({"description": str(opt["description"])} if opt.get("description") else {}),
                **({"follow_up_query": str(opt["follow_up_query"])} if opt.get("follow_up_query") else {}),
                **({"count": int(opt["count"])} if isinstance(opt.get("count"), (int, float)) else {}),
            })
        if not normalized_options:
            logger.warning("⚠️ [CLARIFY] <clarify> block options were all invalid; ignoring")
            return
        normalized = {
            "message": str(payload.get("message") or "Which one did you mean?"),
            "reason": str(payload.get("reason") or "ambiguous_intent"),
            "options": normalized_options,
            "allow_freeform": bool(payload.get("allow_freeform", True)),
            "source": "llm",
        }
        if payload.get("column"):
            normalized["column"] = str(payload["column"])
        if payload.get("user_input"):
            normalized["user_input"] = str(payload["user_input"])
        self._payloads.append(normalized)
        logger.info(
            f"💬 [CLARIFY] Parsed clarify block ({len(normalized_options)} options, "
            f"reason={normalized['reason']})"
        )

    def pop_payloads(self) -> List[Dict[str, Any]]:
        """Return any clarify payloads parsed so far and clear the queue."""
        out, self._payloads = self._payloads, []
        return out

# ═══════════════════════════════════════════════════════════════════════════════════════
# MERMAID BLOCK DETECTION
# ═══════════════════════════════════════════════════════════════════════════════════════

MERMAID_START_PATTERN = re.compile(r'```mermaid\s*\n?', re.IGNORECASE)
CODE_BLOCK_END = '```'

def detect_mermaid_in_chunk(state: StreamingState, chunk: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Detect and extract mermaid blocks from streaming chunks.
    OPTIMIZED: Emit chunks IMMEDIATELY for real-time streaming.
    Only buffer when actively looking for mermaid pattern.
    
    Returns:
        Tuple of (text_to_emit, completed_mermaid_block_or_none)
    """
    # If we're in a mermaid block, accumulate until we find the closing ```
    if state.in_mermaid_block:
        state.mermaid_buffer += chunk
        
        # Check if mermaid block is complete
        if CODE_BLOCK_END in state.mermaid_buffer:
            # Find the end of mermaid block
            end_idx = state.mermaid_buffer.find(CODE_BLOCK_END)
            mermaid_code = state.mermaid_buffer[:end_idx].strip()
            remaining = state.mermaid_buffer[end_idx + len(CODE_BLOCK_END):]
            
            # Reset mermaid state
            state.in_mermaid_block = False
            state.mermaid_buffer = ""
            state.buffer = ""
            
            # Create mermaid block event
            mermaid_block = {
                "code": mermaid_code,
                "index": len(state.mermaid_blocks)
            }
            state.mermaid_blocks.append(mermaid_block)
            
            logger.info(f"📊 [STREAMING] Completed mermaid block #{len(state.mermaid_blocks)}")
            
            # Return remaining text and the completed mermaid block
            return remaining, mermaid_block
        
        # Still accumulating mermaid, emit nothing (placeholder already shown)
        return "", None
    
    # FAST PATH: If chunk doesn't contain backtick and no pending backtick in buffer
    # Emit immediately without buffering
    if '`' not in chunk and '`' not in state.buffer:
        emit_text = state.buffer + chunk
        state.buffer = ""
        return emit_text, None
    
    # Add chunk to buffer for mermaid detection
    state.buffer += chunk
    
    # Check if a mermaid block is starting
    match = MERMAID_START_PATTERN.search(state.buffer)
    if match:
        # Split at mermaid start
        before_mermaid = state.buffer[:match.start()]
        after_match = state.buffer[match.end():]
        
        # Enter mermaid accumulation mode
        state.in_mermaid_block = True
        state.mermaid_buffer = after_match
        state.buffer = ""
        
        logger.info(f"📊 [STREAMING] Detected mermaid block start")
        
        # Emit text before mermaid, add placeholder
        return before_mermaid + "\n[Diagram rendering...]\n", None
    
    # Buffer has backtick but might not be mermaid yet
    # Keep minimal lookahead (10 chars = len("```mermaid"))
    LOOKAHEAD_SIZE = 10
    
    if len(state.buffer) > LOOKAHEAD_SIZE:
        # Safe to emit everything except the lookahead
        emit_text = state.buffer[:-LOOKAHEAD_SIZE]
        state.buffer = state.buffer[-LOOKAHEAD_SIZE:]
        return emit_text, None
    
    # Small buffer with backtick - wait for more data
    return "", None


def flush_buffer(state: StreamingState) -> str:
    """Flush any remaining buffer at end of stream"""
    remaining = state.buffer
    state.buffer = ""
    return remaining


# ═══════════════════════════════════════════════════════════════════════════════════════
# LLM STREAMING
# ═══════════════════════════════════════════════════════════════════════════════════════

# ── Enterprise discovery with a hard timeout ─────────────────────────────────
# Discovery is a network round-trip to discovery-service. On the streaming path
# it sits between context-prep and the first answer token, so a slow/hung
# discovery would stall first-token latency. Bound it: on timeout/error return
# failed=True so the caller surfaces "sources unreachable" (RULE #1 — fail loud,
# never silently answer as if the org had no data).
ENTERPRISE_DISCOVERY_TIMEOUT_S = float(os.getenv("ENTERPRISE_DISCOVERY_TIMEOUT_S", "6"))


async def discover_enterprise_tools(
    *, org_id=None, dept_id=None, dept_ids=None, roles=None, jwt_token=None, query="",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    """Discover dept_* tool schemas, bounded by a hard timeout. Returns
    ``(schemas, name_map, failed)`` and never raises. ``failed=True`` on timeout
    or any error so the caller can surface a service outage instead of degrading
    to "no enterprise data". Safe to run concurrently with context pre-fetch."""
    try:
        from services.enterprise_tools import build_enterprise_tool_schemas
        schemas, name_map = await asyncio.wait_for(
            build_enterprise_tool_schemas(
                org_id=org_id, dept_id=dept_id, dept_ids=dept_ids,
                roles=roles, jwt_token=jwt_token, query=query,
            ),
            timeout=ENTERPRISE_DISCOVERY_TIMEOUT_S,
        )
        return schemas, name_map, False
    except asyncio.TimeoutError:
        logger.error(
            f"❌ [ENT_DISCOVERY] discovery timed out after "
            f"{ENTERPRISE_DISCOVERY_TIMEOUT_S}s — surfacing as unreachable (not degrading silently)"
        )
        return [], {}, True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ [ENT_DISCOVERY] discovery failed: {exc}")
        return [], {}, True


async def stream_llm_response_impl(
    prompt: str,
    system: str = None,
    model: str = None,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    enable_internet_search: bool = False,
    user_id: str = None,
    user_email: str = None,
    profession: str = None,
    dynamic_context: str = None,
    chat_session_id: str = None,
    files: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    tier: Tier = DEFAULT_TIER,
    use_enterprise_data: bool = False,
    org_id: Optional[str] = None,
    dept_id: Optional[str] = None,
    dept_ids: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
    jwt_token: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    use_personal_data: bool = False,
    selected_folder_ids: Optional[List[str]] = None,
    persona_data: Optional[Dict[str, Any]] = None,
    enterprise_prefetch: Optional[Dict[str, Any]] = None,
    vault_summary: Optional[Dict[str, int]] = None,
) -> AsyncGenerator[StreamEvent, None]:
    """
    Stream response using OpenAI-compatible SDK.

    Supports tool-call loop for execute_code and internet_search.
    Files (if provided) are mounted in a Docker sandbox for code execution.

    ``max_output_tokens`` (optional) overrides ``MAX_OUTPUT_TOKENS_DEFAULT``
    for this request. Clamped to ``[1, MAX_OUTPUT_TOKENS_CEILING]``.
    """
    # Resolve effective output-token cap (per-request override, clamped to
    # the hard ceiling). Falls back to the chat-specific default.
    if max_output_tokens is not None and max_output_tokens > 0:
        effective_max_output_tokens = min(int(max_output_tokens), MAX_OUTPUT_TOKENS_CEILING)
    else:
        effective_max_output_tokens = MAX_OUTPUT_TOKENS_CHAT
    logger.info(
        f"🎯 [LLM_STREAM] effective max_output_tokens={effective_max_output_tokens} "
        f"(requested={max_output_tokens}, chat_default={MAX_OUTPUT_TOKENS_CHAT}, ceiling={MAX_OUTPUT_TOKENS_CEILING})"
    )
    logger.info(f"🏷️ [LLM_STREAM] tier={tier} model={model or '(default for tier)'}")
    logger.debug(f"🔵 [LLM_STREAM] ========== FUNCTION ENTERED ==========")
    logger.debug(f"🔵 [LLM_STREAM] prompt length: {len(prompt) if prompt else 0}")
    logger.debug(f"🔵 [LLM_STREAM] model: {model}")
    logger.debug(f"🔵 [LLM_STREAM] context_chunks: {len(context_chunks) if context_chunks else 0}")
    logger.debug(f"🔵 [LLM_STREAM] user_id: {user_id}")
    logger.debug(f"🔵 [LLM_STREAM] files: {len(files) if files else 0}")

    MAX_TOOL_ROUNDS = 8
    MAX_SCRIPT_SIZE = 50_000

    try:
        tier = coerce_tier(tier)
        _llm_async_client = get_llm_client(async_=True, tier=tier)
        _llm_default_model = get_default_model(tier)
    except ValueError as _cfg_err:
        yield StreamEvent(StreamEventType.ERROR, {"message": f"LLM not configured: {_cfg_err}"})
        return

    # Build tools list
    chat_tools = []

    if enable_internet_search:
        from citra_internet_service import get_internet_tool_definition
        chat_tools.append(get_internet_tool_definition())
        logger.debug("🌐 [LLM_STREAM] Internet search tool enabled")

    # Detect context flags — used to tailor the execute_code usage prompt.
    # The tool itself is always available; these flags just help the LLM pick
    # the right approach.
    has_structured_data = bool(
        context_chunks and any(
            (chunk.get('source') == 'saas') or
            (chunk.get('metadata', {}).get('source_type', '').startswith('structured'))
            for chunk in context_chunks
        )
    )
    # has_files must reflect EVERY file that will actually be mounted into the
    # sandbox at execute_code time (see files_for_docker construction below).
    # Otherwise the LLM is told `files=False` while the runtime mounts vault
    # files, causing the model to hardcode inline data from context chunks
    # instead of reading /workspace/input/<filename>.
    #
    # SaaS vault chunks (metadata.source_type ∈ {structured_vault, unstructured_vault})
    # are emitted by SaaSDataTool after the LLM-based file_relevance_scorer has
    # picked relevant files and carry filename + s3_key directly. These chunks
    # are exempt from cross-encoder reranking in agentic_rag/context_merger.py
    # so they reliably reach this code path.
    vault_file_names: List[str] = []
    if context_chunks:
        for _chunk in context_chunks:
            _meta = _chunk.get('metadata') or {}
            if _meta.get('source_type') in ('structured_vault', 'unstructured_vault'):
                _fn = _meta.get('filename')
                if _fn and _fn not in vault_file_names:
                    vault_file_names.append(_fn)

    # Vault chunks can only reach here from the SaaSDataTool pre-fetch, which is
    # itself gated on use_personal_data — off for main chat. If any turn up, the
    # personal source is leaking back in: mounting them would put personal files
    # in front of an enterprise-only surface, and the prompt would point the LLM
    # at `personal_data_tool`, which is no longer registered. Drop them loudly.
    if vault_file_names and not use_personal_data:
        logger.warning(
            f"🚫 [LLM_STREAM] Dropping {len(vault_file_names)} personal-vault file(s) "
            f"from the mount list on an enterprise-only surface: {vault_file_names}. "
            "Something upstream produced vault context with use_personal_data=False."
        )
        vault_file_names = []

    all_mounted_filenames: List[str] = list(vault_file_names)
    has_files = bool(files) or bool(all_mounted_filenames)
    if all_mounted_filenames:
        logger.info(
            f"📂 [LLM_STREAM] resolved {len(all_mounted_filenames)} mountable vault file(s) "
            f"from context: {all_mounted_filenames}"
        )

    # execute_code is ALWAYS available — the LLM decides when to use it based
    # on the user's intent (file generation, conversion, computation on attached
    # files, custom algorithms, etc.).
    from services.code_executor import build_execute_code_tool
    chat_tools.append(build_execute_code_tool())
    logger.info(
        f"⚙️ [LLM_STREAM] execute_code tool enabled "
        f"(files={has_files}, structured_data={has_structured_data})"
    )

    # (The deep_research_handoff tool was removed — main chat no longer bridges
    # to action-chat. Deep-research asks are answered inline by this loop.)

    # ── NO BUILDER TOOLS ON MAIN CHAT ────────────────────────────────────────
    # Main chat is DISCONNECTED from the presentation / printable / report
    # composers. The `make_presentation` / `make_report` tools and the
    # OPEN_BUILDER handoff they emitted were removed here on purpose: typing
    # "create a presentation" or "write a report" in main chat must NOT launch
    # a composer UI. The corresponding home cards are hidden too
    # (Citra-UI/config/operationsCapabilities.js → hiddenOnHome).
    #
    # Quick chat (api/quick_chat.py) still owns that flow — the StreamEventType
    # .OPEN_BUILDER member stays defined for that surface. Do NOT re-add the
    # builder tools to this function; tests/test_no_action_chat_redirect.py
    # guards against it.

    # ── NO PERSONAL VAULT TOOLS ON MAIN CHAT ────────────────────────────────
    # Main chat is an ENTERPRISE-ONLY search surface: dept_* MCP sources and the
    # enterprise SOP library. The personal vault (a user's own uploaded folders)
    # is NOT a source here, so `list_vault_files` and `personal_data_tool` are
    # not registered — the same treatment the make_presentation / make_report
    # builder tools got above.
    #
    # The UI has no upload "+", no folder panel and no Data Store toggle
    # (Citra-UI/config/featureFlags.js → PERSONAL_VAULT_ENABLED), and
    # /query/stream forces use_personal_data=False before it ever gets here
    # (query.py). The composers (report / presentation / printable) still read
    # the vault through their own code paths — this removal is scoped to chat.
    #
    # `use_personal_data` / `selected_folder_ids` / `vault_summary` stay in the
    # signature because callers still pass them; they are inert here. A truthy
    # value means an upstream gate leaked, so say so rather than fail quietly.
    if use_personal_data:
        logger.warning(
            "🚫 [LLM_STREAM] use_personal_data=True reached stream_llm_response_impl "
            f"(folders={selected_folder_ids or 'all-vault'}) — there is no personal "
            "vault any more; the flag is inert and nothing extra will be searched."
        )

    # ── Enterprise MCP tools ─────────────────────────────────────────────────
    # Surface each registered dept-MCP source as a `dept_<source>` function
    # the LLM can call mid-conversation. This is now the SOLE path for
    # enterprise data — the legacy orchestrator pre-fetch (hybrid /
    # context_only modes) has been retired.
    enterprise_name_map: Dict[str, Dict[str, Any]] = {}
    # Set when the user asked for enterprise data but discovery / the sources are
    # UNREACHABLE (outage) — distinct from a scope that genuinely has no sources.
    # We must NOT silently answer from general knowledge as if the org had no
    # data; the notice injected below makes the model tell the user instead.
    enterprise_sources_unreachable = False
    if use_enterprise_data:
        # Prefer schemas the caller already discovered CONCURRENTLY with context
        # prep (query_stream runs discover_enterprise_tools alongside the
        # orchestrator so the discovery round-trip overlaps the vault pre-fetch).
        # Fall back to discovering here — with a hard timeout — for callers that
        # didn't prefetch (e.g. the document reader). A discovery/source OUTAGE
        # must surface as `enterprise_sources_unreachable`, never silently degrade
        # to "no enterprise data" (RULE #1).
        if enterprise_prefetch is not None:
            ent_schemas = enterprise_prefetch.get("schemas") or []
            enterprise_name_map = enterprise_prefetch.get("name_map") or {}
            enterprise_sources_unreachable = bool(enterprise_prefetch.get("failed"))
        else:
            ent_schemas, enterprise_name_map, enterprise_sources_unreachable = (
                await discover_enterprise_tools(
                    org_id=org_id, dept_id=dept_id, dept_ids=dept_ids,
                    roles=roles, jwt_token=jwt_token, query=prompt,
                )
            )
        if ent_schemas:
            chat_tools.extend(ent_schemas)
            logger.info(
                f"🏢 [LLM_STREAM] Enterprise tools enabled: {len(ent_schemas)} dept_* "
                f"tool(s) added (tools_only mode)"
            )

    # Build messages
    messages = []

    effective_system = system or "You are a helpful AI assistant."

    # ── CURRENT-DATE ANCHOR ───────────────────────────────────────────────────
    # Without this the model falls back to its training cutoff when constructing
    # time-sensitive `internet_search` queries (e.g. emits "latest news March 2025"
    # in 2026) and returns stale results.
    _now = datetime.now(timezone.utc)
    effective_system = (
        f"Today's date is {_now.strftime('%A, %B %d, %Y')} (UTC, ISO {_now.strftime('%Y-%m-%d')}). "
        "When the user asks for 'latest', 'today', 'current', 'recent', or 'now', use this "
        "actual current date in any internet_search query — do NOT substitute an older date "
        "from your training data.\n\n"
        + effective_system
    )

    # ── CAPABILITIES PREAMBLE ─────────────────────────────────────────────────
    # Two LLM-callable tools: internet_search and execute_code. All structured
    # analytics on uploaded CSV/Excel/JSON now flow through execute_code in the
    # sandbox — no upstream SQL pre-computation.
    capability_lines = ["\n\n**🧰 YOUR CAPABILITIES IN THIS CONVERSATION:**"]
    if enable_internet_search:
        capability_lines.append(
            "- **`internet_search` (tool you can call)** — real-time web search for current events, "
            "latest news, live data, or anything likely newer than your training cutoff."
        )
    capability_lines.append(
        "- **`execute_code` (tool you can call)** — run Python in a sandboxed Docker container. "
        "Use for file generation, format conversion, multi-row computation on attached structured "
        "files (CSV/Excel/JSON), and custom algorithms."
    )
    # Main chat has NO composer builder tools — see the "NO BUILDER TOOLS ON
    # MAIN CHAT" block above. Say so explicitly so the LLM answers in chat
    # instead of promising a deck / report UI it cannot open.
    capability_lines.append(
        "- **You CANNOT open a presentation, slide-deck, printable or report composer.** "
        "There is no `make_presentation` / `make_report` tool on this surface. If the user "
        "asks for a deck, slides, a PPT, a visual report or a Word-style report, answer "
        "inline in chat with the structured content instead (headings, sections, bullet "
        "points, tables), grounded on their data. Never claim a builder or composer is opening."
    )
    if has_structured_data:
        capability_lines.append(
            "- **Structured-file context below is a PARTIAL preview** — the chunks shown for any "
            "CSV / Excel / JSON file are only sample rows selected by vector search to convey schema "
            "and shape. They are NEVER the full dataset. For ANY question that reads, lists, filters, "
            "modifies, transforms, or computes on such a file you MUST call `execute_code` to process "
            "the FULL file from `/workspace/input/`. Answering from the sample chunks will produce "
            "incomplete or wrong results — they exist only to help you understand the file's structure."
        )
    effective_system += "\n".join(capability_lines)
    # ──────────────────────────────────────────────────────────────────────────

    if enable_internet_search:
        effective_system += (
            "\n\n**🌐 INTERNET SEARCH AVAILABLE**: You have access to an `internet_search` tool. "
            "It is for **time-sensitive / live / post-training-cutoff** information only — not a default fallback.\n\n"
            "**Call `internet_search` when:**\n"
            "- The query needs live data (prices, exchange rates, weather, sports scores, schedules).\n"
            "- The query is about current events, breaking news, or recent releases/regulations/statistics.\n"
            "- The query asks about a real-world entity whose state changes over time (current CEO, current price/spec, current role).\n"
            "- The user explicitly says \"latest\", \"current\", \"live\", \"today\", \"now\", \"this year\", \"real-time\", \"up to date\", or \"recent\".\n\n"
            "**Do NOT call `internet_search` for:**\n"
            "- Stable factual / conceptual / definitional / educational questions answerable from training knowledge "
            "(e.g., \"what is a black hole\", \"explain quicksort\", \"difference between TCP and UDP\", \"how does photosynthesis work\").\n"
            "- General how-to, reasoning, math, or coding help.\n"
            "- Questions that are clearly answerable from the document context provided in this turn AND that context is on-topic for the question.\n\n"
            "**Decision rule:** If a well-informed expert could answer correctly from general knowledge that doesn't change month-to-month, answer directly from training. Reach for `internet_search` only when the answer would be wrong or stale without fresh data.\n\n"
            "**Context-relevance check:** If the retrieved document context is unrelated to the user's question (e.g., user asks about \"energy outlook\" but only a tradebook CSV is attached), IGNORE the irrelevant context and decide based on the question alone — either answer from training or call `internet_search`. Do NOT refuse, do NOT ask the user for permission to search, and do NOT say \"I don't have documents on this topic\". Just call `internet_search` directly when the query needs fresh data.\n\n"
            "**IMPORTANT**: When you do call `internet_search`, call it ONLY ONCE per user request. Combine all aspects of the question into a single comprehensive query — do NOT call it multiple times for different sub-questions."
        )

    # Unified execute_code guidance — scope-aware based on what's present in this turn.
    effective_system += """

**⚙️ CODE EXECUTION AVAILABLE**: You have access to an `execute_code` tool to run Python scripts in a sandboxed Docker container. Decide when to use it based on the user's intent.

**When to use `execute_code`:**
- The user asks to generate a downloadable file (Excel, PPTX, PDF, CSV, JSON, DOCX, image).
- The user asks to convert content from one format to another and wants a file back (e.g., "convert this to JSON and give me a download").
- **"Convert this to that" referring to an attached file OR to text/data already in the conversation context (uploaded docs, prior tool output, retrieved chunks) — PREFER `execute_code`.** The "this" is not literally in the user's current message; it lives in `/workspace/input/` or in earlier context, so a script must read/transform it deterministically rather than the LLM reproducing it from memory (which risks truncation, hallucination, or losing fidelity on tabular/structured data).
- **ANY task that reads, lists, filters, modifies, or computes on an attached structured file (CSV/Excel/JSON)** — this includes listing all rows, adding/renaming columns, filtering, counting, summing, grouping, time-series, FIFO/LIFO, P&L, reconciliation. Read the FULL file from `/workspace/input/`. NEVER answer such questions from the sample text chunks shown in context — those are a partial preview only (typically <30 chunks per file, NEVER the full data).
- Custom algorithms or stateful transformations hard to express in plain text (e.g., FIFO matching on tradebooks, regex extraction across a large payload).

**When NOT to use `execute_code`:**
- Plain conversational / knowledge questions ("what is X", "summarize this paragraph").
- **"Convert this to that" where "this" is short text the user pasted directly inside the current query** (e.g., a small JSON/YAML/SQL/code snippet they typed in-line and want reformatted) — just produce the converted content directly in your reply. The text is fully visible in the message, so a sandbox round-trip adds latency with no fidelity benefit.
- Inline conversions the user only wants to read (not download) and that are small — just answer with the converted content in your text reply.
- Charts and visualizations — output ```chartjs code blocks with valid Chart.js v4 JSON config; the UI renders them client-side.
- Answering questions from prose document chunks (PDF/DOCX/TXT) already provided in context.

**🔁 CONVERSION DECISION RULE (quick check):**
1. Is the source content typed verbatim inside the user's current message and small enough to handle inline? → Convert it yourself in the reply.
2. Otherwise (it's an attached file, an earlier upload, a long pasted blob, tabular/structured data, or anything where exact fidelity matters) → Call `execute_code` to read and convert it.

**Script rules:**
- Available libraries: pandas, openpyxl, xlrd, python-docx, python-pptx, Pillow, xlsxwriter, reportlab, pdfplumber, jsonschema.
- Input files (when attached to this turn) are at `/workspace/input/`. Write outputs to `/workspace/output/` ONLY when the user explicitly wants a downloadable file (see FILE OUTPUT POLICY below).
- If no files are attached, embed any needed data directly in your script (string literals, dicts, DataFrames).
- Do NOT use subprocess, os.system, eval, exec, network libraries, or any package not listed above.
- When the script produces output files, mention them with the download links provided back to you.

**📤 FILE OUTPUT POLICY (CRITICAL — file generation is OPT-IN):**
Do NOT write to `/workspace/output/` by default. Analytical questions ("net P&L", "top 5 symbols", "how many", "summarize") want the answer **inline in chat**, NOT a download link. Producing an unrequested JSON/Excel file is a bug.

**Write a file ONLY when the user explicitly asks for one:**
- Conversion: "convert this to JSON / Excel / CSV / PDF / DOCX / PPTX", "save as xlsx", "export to csv".
- Explicit download / file ask: "give me a file", "download", "send me the spreadsheet", "generate a report document".
- Generation tasks where a file is the natural deliverable: "create a PPTX", "build me an Excel template", "PDF invoice".

For analytical queries that compute on a large file via `execute_code` (e.g., FIFO P&L), `print()` results to stdout so they come back to you, and do NOT write anything to `/workspace/output/`. Put the numbers in your chat reply.

**Choosing the format when a file IS warranted:**
- Match what the user explicitly named ("as xlsx" → `.xlsx`, "as JSON" → `.json`, "PDF" → `.pdf`).
- If unspecified, pick the format that best fits the content:
  - Tabular data → `.xlsx` (preferred) or `.csv` for a single flat table.
  - Structured / nested / API-style payloads → `.json`.
  - Long-form written docs, letters, reports → `.docx`.
  - Presentations / slides → `.pptx`.
  - Print-ready fixed-layout → `.pdf`.
  - Image files → `.png`.
- Do NOT default to `.json` for tabular data (trades, transactions, lists) — humans want `.xlsx` or `.csv`.

**🚫 NEVER-DUMP-SCRIPT:** For file-generation requests, always call `execute_code` — never paste the generator as a ```python``` block in chat, even on failure (apologise and ask for retry instead).

**🩺 PREFLIGHT:** Before a long script using reportlab / python-pptx / python-docx / openpyxl / xlsxwriter / Pillow / pdfplumber, first run a one-line `execute_code` probe (e.g. `import reportlab`); if it fails, reply "sandbox missing `<lib>`, ask admin to rebuild quick-chat-sandbox" and stop.

**🔍 AMBIGUOUS ANALYTICAL QUERIES — use the standard default, do NOT pick the simplest interpretation:**
- **"Profit and loss" / "P&L" / "net profit"** on trade or transaction data (tradebook, broker statement, equity/F&O trades, buy-sell history) → **DEFAULT to realized P&L using FIFO (first-in-first-out) lot matching per symbol.** This is the standard accounting and tax method. Do NOT use `total sells − total buys` (cash-flow basis) — that is only correct when every position is fully closed, which is almost never true in a real tradebook. After computing FIFO P&L, mention in a short footer that the user can ask for LIFO or weighted-average cost if preferred.
- **"Growth rate"** → CAGR vs YoY vs MoM — ask in one short sentence if unspecified.
- **"Average"** on potentially skewed data → ask mean vs median if distribution is unclear.
- For metrics with a clear standard default (FIFO for trade P&L), USE THAT DEFAULT and proceed; mention the chosen method in the final-answer footer so the user can correct you.

**🧮 PLAN-FIRST RULE (for computation / analysis questions):**
Before invoking `execute_code` for any analytical question on the user's data, emit a short plan block at the very start of your response:

```
<plan>
Goal: <one sentence restating what the user is asking>
Method: <the specific method — e.g. "FIFO lot matching per symbol">
Assumptions:
  - <definitional or data assumption>
Steps:
  1. <first concrete step — e.g. "read full CSV from /workspace/input/trades.csv">
  2. <next step>
Validation: <how you will sanity-check>
</plan>
```

Rules:
- Emit the plan block exactly ONCE on your first turn for this question. Do NOT re-emit it after a tool call.
- After `execute_code` returns successfully, your next turn must be the **final natural-language answer with the computed numbers**, with NO further tool calls.
- For trade/tradebook P&L the Method MUST be "FIFO lot matching per symbol". Cash-flow basis is forbidden as a default."""

    if use_enterprise_data:
        # State the absence explicitly. Without this the model happily offers to
        # "check your uploaded documents" and then has no tool to do it with.
        #
        # Scoped to the enterprise chat surface on purpose. Other callers of this
        # function (api/reader.py chats about a web page or a single document)
        # also run with the vault tools off, and telling THEM they search "the
        # organisation's governed sources" would be a lie about their own scope.
        effective_system += """

**📚 NO PERSONAL DATA STORE ON THIS SURFACE**: this chat searches the organisation's governed sources only — the `dept_*` MCP sources and the enterprise SOP library. There is no personal file upload here and no tool that reads a user's own uploaded folders. Never offer to check "your uploaded documents", never ask the user to attach or upload a file, and never claim a document is unavailable "because it has not been uploaded" — say which enterprise source you looked in instead."""

    # ── Enterprise tools nudge ───────────────────────────────────────────────
    # Only added when at least one dept_* tool was successfully registered.
    # Enterprise data is served exclusively via these tools — there is no
    # orchestrator pre-fetch anymore.
    if enterprise_name_map:
        _ent_tool_list = ", ".join(sorted(enterprise_name_map.keys())[:10])
        _ent_more = "" if len(enterprise_name_map) <= 10 else f" (and {len(enterprise_name_map) - 10} more)"
        _ent_lead = (
            "**🏢 ENTERPRISE DATA SOURCES (tools you MUST call for any enterprise data)**: "
            "No enterprise context has been pre-loaded into this conversation. To answer "
            "any question about this organisation's data you MUST call the appropriate "
            f"`dept_*` tool below. Available: {_ent_tool_list}{_ent_more}."
        )
        effective_system += f"""

{_ent_lead}

- Each `dept_*` tool fetches FRESH rows/chunks from a registered enterprise data source via `query` (natural-language) and optional `max_results`.
- Pick the tool whose description matches the user's question. For cross-source questions, call multiple `dept_*` tools (in separate tool calls) and merge the results in your answer.
- For ANY analytical question on the returned rows (totals, averages, group-by, joins, time series), chain into `execute_code`: print the rows from your tool call as input data inside the script (or paste them into a `pandas.DataFrame`), then compute. Do NOT eyeball aggregates from the raw tool response.

**🚫 GROUND EVERY ENTERPRISE FIGURE — NEVER FABRICATE:**
- Every number, count, total, table cell, status, date, or named record about this organisation MUST come from a SUCCESSFUL `dept_*` tool result in THIS conversation. If you did not retrieve a value, you do not have it — do NOT estimate it, infer it, interpolate it, or fill it from general knowledge or training data.
- A tool result with `success: false` (or a non-empty `error`) means the call FAILED — it is NOT the same as "zero rows". Do NOT treat a failed call as empty data. When a call fails: retry ONCE with a rephrased query; if it still fails, tell the user plainly that the data could not be retrieved (briefly state why, e.g. "the outage source timed out"), and report ONLY the figures you DID successfully retrieve. Never silently substitute a guess for missing data, and never present a partial dataset as if it were the full picture.
- A SUCCESSFUL result with an empty `results` array genuinely means "no matching rows" — say so; do not invent rows to fill it.
- If you cannot fully answer because some calls failed, an honest partial answer (with an explicit note about what's missing) is REQUIRED — a confident complete-looking answer built on fabricated numbers is a serious error."""
    # ──────────────────────────────────────────────────────────────────────────

    # ── Enterprise sources UNREACHABLE notice ────────────────────────────────
    # The user asked for enterprise data but discovery / the sources are down
    # (outage — not "no sources"). enterprise_name_map is empty, so the nudge
    # above didn't fire. Make the model SAY SO rather than silently answering
    # from general knowledge as if the org had no data (RULE #1 / circuit-breaker
    # failure surfaced to the user instead of swallowed).
    if enterprise_sources_unreachable:
        effective_system += (
            "\n\n**🚨 ENTERPRISE DATA SOURCES ARE CURRENTLY UNREACHABLE** — the "
            "discovery/data service is DOWN this turn (a service OUTAGE, NOT 'the "
            "organisation has no data'), so you have NO `dept_*` tools available. "
            "If the user's question depends on their organisation's data, you MUST "
            "tell them plainly that their data sources could not be reached right "
            "now and that you cannot answer from their data until the service is "
            "restored. Do NOT answer such questions from general knowledge as if it "
            "were their data, and do NOT imply the data is empty or zero. For "
            "purely general/conceptual questions you may still answer normally."
        )

    # ── Mounted file manifest ────────────────────────────────────────────────
    # Tell the LLM the EXACT filenames available under /workspace/input/. Without
    # this the model often hardcodes inline data from the retrieval chunks (which
    # are partial previews) and emits 8KB+ tool-call args full of unescaped chars
    # that fail JSON parsing — exactly what we saw on the FIFO P&L turn.
    mounted_files: List[str] = []
    seen_mount_names = set()
    if files:
        for _f in files:
            _fn = _f.get('filename')
            if _fn and _fn not in seen_mount_names:
                mounted_files.append(_fn)
                seen_mount_names.add(_fn)
    for _fn in vault_file_names:
        if _fn not in seen_mount_names:
            mounted_files.append(_fn)
            seen_mount_names.add(_fn)
    if mounted_files:
        _STRUCT_EXT = ('.xlsx', '.xls', '.csv', '.json')
        _struct_files = [fn for fn in mounted_files if fn.lower().endswith(_STRUCT_EXT)]
        _unstruct_files = [fn for fn in mounted_files if not fn.lower().endswith(_STRUCT_EXT)]
        _sections = []
        if _struct_files:
            _sf = "\n".join(f"  - /workspace/input/{fn}" for fn in _struct_files)
            _sections.append(
                "**📂 STRUCTURED MOUNTED FILES (read via `execute_code` — full file available at the path):**\n"
                f"{_sf}\n\n"
                "- Open with pandas/openpyxl/json. For ANY computation, aggregation, FIFO/P&L, conversion, "
                "summary, or row-level analysis, read the FULL file from `/workspace/input/` inside `execute_code`. "
                "Retrieval chunks for structured files are PARTIAL previews and only safe for schema discovery.\n"
                "- NEVER paste chunk text into an inline list/DataFrame — that loses fidelity and breaks JSON encoding."
            )
        if _unstruct_files:
            _uf = "\n".join(f"  - {fn}" for fn in _unstruct_files)
            _sections.append(
                "**📂 UNSTRUCTURED VAULT FILES (fetch via `personal_data_tool` — do NOT open via `execute_code`):**\n"
                f"{_uf}\n\n"
                "- These are PDF/DOCX/TXT documents. Their chunks live in the vault index (Milvus) and are reranked for the user's query. "
                "To get passages, call `personal_data_tool(query=..., max_results=...)` — do NOT use `execute_code` with pdfplumber/python-docx/open() on these files. "
                "Reading raw bytes bypasses retrieval and produces worse answers."
            )
        if _sections:
            effective_system += "\n\n" + "\n\n".join(_sections)

    messages.append({"role": "system", "content": effective_system})

    # Add context chunks (typed source-origin headers when available)
    if context_chunks:
        for idx, chunk in enumerate(context_chunks, 1):
            chunk_text = chunk.get('text', '')
            chunk_id = chunk.get('chunk_id', 'unknown')
            score = chunk.get('score', 0.0)
            topic = chunk.get('topic', 'Unknown Document')
            document_id = chunk.get('document_id', '')
            metadata = chunk.get('metadata', {}) or {}
            source_type = metadata.get('source_type', '')
            source_origin = chunk.get('source_origin') or metadata.get('source_origin')

            # Tag structured-vault chunks so the LLM treats them as schema-only — the
            # raw file is mounted into /workspace/input/ for execute_code to read.
            # Unstructured vault chunks (PDF/DOCX/TXT) MUST be fetched via
            # personal_data_tool, NOT execute_code — chunks already live in Milvus.
            if source_type == 'structured_vault':
                prefix = "[Structured Vault File (schema-only — call execute_code on the mounted file for any computation)"
            elif source_type == 'unstructured_vault':
                prefix = "[Unstructured Vault File (summary+tags only — call personal_data_tool to fetch passages from the vault; do NOT use execute_code on PDF/DOCX/TXT)"
            elif chunk.get('source') == 'saas':
                _fn = (metadata.get('filename') or topic or '').lower()
                _is_struct = _fn.endswith(('.xlsx', '.xls', '.csv', '.json'))
                if _is_struct:
                    prefix = "[Structured Vault File (schema-only — call execute_code on the mounted file for any computation)"
                else:
                    prefix = "[Unstructured Vault File (summary+tags only — call personal_data_tool to fetch passages from the vault; do NOT use execute_code on PDF/DOCX/TXT)"
            elif source_type.startswith('structured') or source_origin == 'structured':
                prefix = "[Structured File Sample (PARTIAL preview — call execute_code for full computation)"
            elif source_origin == 'internet':
                prefix = "[Internet Source"
            elif source_origin == 'enterprise':
                prefix = "[Enterprise Source"
            elif source_origin == 'vault':
                prefix = "[Vault Document"
            else:
                prefix = "[Context Chunk"

            origin_line = f"\nSource Origin: {source_origin}" if source_origin else ""
            chunk_message = f"""{prefix} {idx}/{len(context_chunks)}]
Score: {score:.2f} | Document: {topic}{origin_line}
Chunk ID: {chunk_id}
Document ID: {document_id}

{chunk_text}"""
            messages.append({"role": "user", "content": chunk_message})

    # Add conversation history
    if conversation_history:
        for hist_msg in conversation_history:
            role = hist_msg.get('role', 'user')
            content = hist_msg.get('content', '').strip()
            if content:
                messages.append({"role": role if role in ('user', 'assistant') else 'user', "content": content})

    # Add dynamic context if provided
    if dynamic_context and dynamic_context.strip():
        dynamic_msg = f"""=== DYNAMIC PROJECT DATA ===
{dynamic_context}
=== END DYNAMIC PROJECT DATA ==="""
        messages.append({"role": "user", "content": dynamic_msg})

    # ── Handle attachments: extract text from images (Qwen OCR) and audio (Whisper) ──
    attachment_parts = []
    attachments = attachments or []
    if attachments:
        import base64 as b64mod
        from qwen_ocr_proxy import extract_text_from_image
        from whisper_proxy import transcribe_audio as whisper_transcribe_audio

        logger.info(f"📎 [LLM_STREAM] Processing {len(attachments)} attachment(s)")
        for idx, attachment in enumerate(attachments, 1):
            base64_data = (
                attachment.get("base64")
                or attachment.get("data")
                or attachment.get("base64Data")
                or attachment.get("content")
            )
            mime_type = attachment.get("mimeType") or attachment.get("type")
            att_name = attachment.get("name", f"attachment-{idx}")

            if not base64_data or not mime_type:
                logger.warning(f"⚠️ [LLM_STREAM] Attachment #{idx} missing data or mime; skipping")
                continue

            try:
                raw_bytes = b64mod.b64decode(base64_data) if isinstance(base64_data, str) else base64_data
            except Exception as e:
                logger.error(f"❌ [LLM_STREAM] Failed to decode attachment '{att_name}': {e}")
                continue

            mime_lower = (mime_type or "").lower()

            if mime_lower.startswith("audio/"):
                # Transcribe audio via Whisper (sync call → run in thread)
                try:
                    transcript = await asyncio.to_thread(
                        whisper_transcribe_audio,
                        audio_bytes=raw_bytes,
                        mime_type=mime_type,
                        filename=att_name,
                        user_id=user_id,
                        user_email=user_email,
                    )
                    if transcript and transcript.strip():
                        attachment_parts.append(
                            f"=== AUDIO TRANSCRIPT: {att_name} ===\n{transcript.strip()}\n=== END AUDIO TRANSCRIPT ==="
                        )
                        logger.info(f"✅ [LLM_STREAM] Transcribed audio '{att_name}': {len(transcript)} chars")
                except Exception as e:
                    logger.error(f"❌ [LLM_STREAM] Audio transcription failed for '{att_name}': {e}")
            else:
                # Extract text from image / document via Qwen OCR (sync call → run in thread)
                try:
                    description = await asyncio.to_thread(
                        extract_text_from_image,
                        image_bytes=raw_bytes,
                        filename=att_name,
                        mime_type=mime_type,
                        user_id=user_id,
                        user_email=user_email,
                    )
                    if description and description.strip():
                        attachment_parts.append(
                            f"=== ATTACHMENT: {att_name} ===\n{description}\n=== END ATTACHMENT ==="
                        )
                        logger.info(f"✅ [LLM_STREAM] Extracted text from '{att_name}': {len(description)} chars")
                except Exception as e:
                    logger.error(f"❌ [LLM_STREAM] OCR failed for '{att_name}': {e}")

    # Build final user message with attachment context prepended
    if attachment_parts:
        final_prompt = "\n\n".join(attachment_parts) + f"\n\n--- CURRENT QUERY ---\n{prompt}"
    else:
        final_prompt = prompt

    # ── Step 1: Reserve budget for the user prompt (query + OCR/attachments) ──
    # The user prompt is the highest-priority content. RAG context chunks are
    # lower priority — trim them first to make room rather than truncating the
    # user's question or the OCR'd text from pasted images.
    user_prompt_tokens = count_tokens(final_prompt)
    # Cap the reservation so an enormous paste can't entirely starve RAG context.
    MAX_USER_PROMPT_SHARE = max(int(MAX_INPUT_TOKENS * 0.6), 4000)
    reserved_for_user = min(user_prompt_tokens, MAX_USER_PROMPT_SHARE)

    existing_tokens = sum(count_tokens(m.get("content") or "") for m in messages)
    if existing_tokens + reserved_for_user > MAX_INPUT_TOKENS:
        pre_trimmed = 0
        original_existing = existing_tokens
        i = len(messages) - 1
        while i >= 0 and existing_tokens + reserved_for_user > MAX_INPUT_TOKENS:
            content = messages[i].get("content") or ""
            if content.startswith("[Context Chunk"):
                existing_tokens -= count_tokens(content)
                messages.pop(i)
                pre_trimmed += 1
            i -= 1
        if pre_trimmed:
            logger.warning(
                f"⚠️ [LLM_STREAM] Pre-trimmed {pre_trimmed} context chunk(s) to preserve "
                f"user prompt+attachments ({user_prompt_tokens:,} tok): "
                f"{original_existing:,} → {existing_tokens:,} tokens."
            )

    # Fit the user prompt into whatever budget remains.
    prompt_budget = MAX_INPUT_TOKENS - existing_tokens
    final_prompt = truncate_to_token_limit(final_prompt, max(prompt_budget, 1000), label="final_prompt")

    # Add current query
    messages.append({"role": "user", "content": final_prompt})

    # ── Step 2: Hard-cap safety net — should rarely trigger after Step 1,
    #    but drops lowest-priority context chunks from the back if anything
    #    still exceeds the limit (e.g., large system prompts or history).
    total_input_tokens = sum(count_tokens(m.get("content") or "") for m in messages)
    if total_input_tokens > MAX_INPUT_TOKENS:
        trimmed_count = 0
        original_total = total_input_tokens
        i = len(messages) - 1
        while i >= 0 and total_input_tokens > MAX_INPUT_TOKENS:
            content = messages[i].get("content") or ""
            if content.startswith("[Context Chunk"):
                total_input_tokens -= count_tokens(content)
                messages.pop(i)
                trimmed_count += 1
            i -= 1
        if trimmed_count:
            logger.warning(
                f"⚠️ [LLM_STREAM] Hard-capped input: removed {trimmed_count} context chunk(s). "
                f"Total tokens: {original_total:,} → {total_input_tokens:,} "
                f"(limit: {MAX_INPUT_TOKENS:,})."
            )

    logger.debug(f"🚀 [LLM_STREAM] Starting streaming with {len(chat_tools)} tools...")

    # Initialize streaming state
    state = StreamingState()
    start_time = time.time()
    actual_input_tokens = 0
    actual_output_tokens = 0
    all_output_files = []
    internet_was_used = False
    # Cap runaway research. On a broad ask the model tends to call
    # internet_search every round and exhaust MAX_TOOL_ROUNDS before it ever
    # answers. Once this many searches have run in a turn, internet_search is
    # dropped from the offered tools so the model must finish.
    _internet_search_calls = 0
    INTERNET_SEARCH_CAP = int(os.getenv("CHAT_INTERNET_SEARCH_CAP", "3"))
    _build_nudged = False

    client = _llm_async_client

    try:
        plan_already_streamed = False
        for round_num in range(MAX_TOOL_ROUNDS + 1):
            logger.debug(f"🔄 [LLM_STREAM] Round {round_num + 1}/{MAX_TOOL_ROUNDS + 1}")

            # 🎬 STAGE: only emit "thinking" on round 0. The transition to
            # "generating_answer" is emitted lazily on the first visible
            # content delta below — that way short answers without tools
            # still show a clean Thinking → Generating answer transition,
            # and tool rounds show calling_tool → tool_complete in between.
            if round_num == 0:
                yield StreamEvent(StreamEventType.STAGE, {
                    "stage": "thinking",
                    "label": "Thinking",
                })

            # Track whether we've announced "generating_answer" yet for THIS
            # round. Reset every iteration so each tool-loop round can emit
            # the transition once content starts flowing.
            generating_answer_emitted = False

            kwargs = {
                "model": model or _llm_default_model,
                "messages": messages,
                "max_tokens": effective_max_output_tokens,
                "temperature": float(temperature) if temperature is not None else 0.2,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            # Reasoning visibility: when LLM_EXPOSE_REASONING is on (default) we
            # set `reasoning.exclude=false` so thinking tokens stream on
            # `delta.reasoning*` and we surface them as REASONING events
            # (Action-Chat-style panel). When off, `exclude=true` keeps hybrid
            # reasoning models (deepseek-v4-pro via OpenRouter) from starving
            # `content` by dumping the answer onto `reasoning_details`. Either
            # way `enabled`/`effort` come from env so the model still thinks.
            _expose_reasoning = reasoning_exposed()
            _extra = apply_reasoning_visibility(get_llm_extra_body(model or _llm_default_model, tier=tier))
            if _extra:
                kwargs["extra_body"] = _extra
            if chat_tools:
                # Drop internet_search once the per-turn cap is hit so the
                # model can't keep researching instead of building.
                if _internet_search_calls >= INTERNET_SEARCH_CAP:
                    _round_tools = [
                        t for t in chat_tools
                        if (t.get("function") or {}).get("name") != "internet_search"
                    ]
                else:
                    _round_tools = chat_tools
                if _round_tools:
                    kwargs["tools"] = _round_tools
                    kwargs["tool_choice"] = "auto"

            full_text = ""
            reasoning_text = ""  # captured as last-resort fallback
            tool_calls_acc = {}  # {index: {id, name, arguments}}
            finish_reason = None
            # Suppress duplicate <plan>...</plan> blocks across tool rounds.
            plan_filter = _PlanBlockFilter(suppress=plan_already_streamed)
            # Strip <clarify>{...}</clarify> blocks and re-emit as
            # `suggestion_chips` SSE events for chip-style UX.
            clarify_filter = ClarifyBlockFilter()

            _t_round = time.time()
            logger.info(f"⏱️ [LLM_STREAM] Round {round_num + 1} → calling LLM (model={kwargs['model']}, tier={tier}, msgs={len(messages)}, tools={len(chat_tools)})")
            # 🔬 DIAG: log the extra_body actually sent so we can verify
            # provider routing config (ignore/order) is taking effect.
            try:
                _eb_log = kwargs.get("extra_body")
                if _eb_log:
                    logger.info(f"🔬 [LLM_STREAM_DIAG] Round {round_num + 1} extra_body={json.dumps(_eb_log)[:500]}")
            except Exception:
                pass
            stream = await client.chat.completions.create(**kwargs)
            _t_create_done = time.time()
            logger.info(f"⏱️ [LLM_STREAM] Round {round_num + 1} create() returned in {_t_create_done - _t_round:.2f}s, awaiting first chunk…")

            round_input_tokens = 0
            round_output_tokens = 0
            _first_chunk_logged = False
            _delta_shape_logged = False
            _chunks_with_no_content_or_tools = 0
            _chunk_count = 0
            _raw_chunks_logged = 0

            async for chunk in stream:
                _chunk_count += 1
                # 🔬 DIAG: dump raw chunk shape for the first ~6 chunks so we
                # can see provider-side error envelopes that the SDK silently
                # forwards (openrouter sometimes returns error/finish without
                # content when upstream truncates).
                if _raw_chunks_logged < 6:
                    try:
                        _raw = chunk.model_dump(exclude_none=True) if hasattr(chunk, "model_dump") else None
                    except Exception:
                        _raw = None
                    if _raw is not None:
                        logger.info(f"🔬 [LLM_STREAM_DIAG] Round {round_num + 1} chunk #{_chunk_count} keys={list(_raw.keys())} sample={str(_raw)[:700]}")
                        _raw_chunks_logged += 1

                if not _first_chunk_logged:
                    logger.info(f"⏱️ [LLM_STREAM] Round {round_num + 1} first chunk in {time.time() - _t_create_done:.2f}s (TTFB from create)")
                    _first_chunk_logged = True
                # Usage in final chunk
                if chunk.usage:
                    round_input_tokens = chunk.usage.prompt_tokens or 0
                    round_output_tokens = chunk.usage.completion_tokens or 0

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

                # 🔬 DIAG: dump delta shape on first non-empty delta to find
                # where the answer actually arrives (deepseek/openrouter
                # leakage hunt). Uses model_dump to bypass pydantic field gating.
                if not _delta_shape_logged:
                    try:
                        _dump = delta.model_dump(exclude_none=True) if hasattr(delta, "model_dump") else None
                    except Exception:
                        _dump = None
                    if _dump:
                        logger.info(f"🔬 [LLM_STREAM_DIAG] Round {round_num + 1} delta keys={list(_dump.keys())} sample={str(_dump)[:600]}")
                        _delta_shape_logged = True

                # Accumulate tool calls
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or "",
                                "name": tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                                "arguments": ""
                            }
                            logger.info(f"🔧 [TOOL_ACC] New tool call idx={idx}, id={tc_delta.id}, name={tool_calls_acc[idx]['name']}")
                        if tc_delta.id:
                            tool_calls_acc[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                arg_chunk = tc_delta.function.arguments
                                arg_type = type(arg_chunk).__name__
                                # Some providers may return arguments as dict instead of string
                                if not isinstance(arg_chunk, str):
                                    logger.warning(f"⚠️ [TOOL_ACC] Non-string arg chunk (type={arg_type}), converting to JSON")
                                    arg_chunk = json.dumps(arg_chunk)
                                tool_calls_acc[idx]["arguments"] += arg_chunk
                                logger.debug(f"🔧 [TOOL_ACC] idx={idx} arg chunk ({arg_type}, {len(arg_chunk)} chars), total so far: {len(tool_calls_acc[idx]['arguments'])} chars")

                # Stream text content
                if delta.content:
                    full_text += delta.content
                    visible = plan_filter.feed(delta.content)
                    # Surface any complete <plan>...</plan> blocks captured in
                    # this feed as a typed PLAN event so the UI can render
                    # them in a dedicated card instead of inline raw tags.
                    for plan_text in plan_filter.pop_plans():
                        yield StreamEvent(StreamEventType.PLAN, {
                            "plan_text": plan_text,
                            "round": round_num + 1,
                        })
                    if visible:
                        visible = clarify_filter.feed(visible)
                        for payload in clarify_filter.pop_payloads():
                            yield StreamEvent(StreamEventType.SUGGESTION_CHIPS, payload)
                        if visible:
                            # 🎬 First visible answer chunk in this round:
                            # transition the timeline to "Generating answer".
                            if not generating_answer_emitted:
                                generating_answer_emitted = True
                                yield StreamEvent(StreamEventType.STAGE, {
                                    "stage": "generating_answer",
                                    "label": "Generating answer",
                                })
                            emit_text, mermaid_block = detect_mermaid_in_chunk(state, visible)
                            if emit_text:
                                yield StreamEvent(StreamEventType.CHUNK, {"text": emit_text})
                            if mermaid_block:
                                yield StreamEvent(StreamEventType.MERMAID_BLOCK, mermaid_block)
                    state.total_output_tokens += len(delta.content) // 4
                else:
                    # Capture reasoning deltas as a fallback in case the
                    # provider ignores reasoning.exclude and emits the whole
                    # answer there (deepseek/openrouter quirk).
                    _r = (
                        getattr(delta, "reasoning", None)
                        or getattr(delta, "reasoning_content", None)
                    )
                    # Also peek into pydantic's `model_extra` (extra fields the
                    # SDK schema doesn't declare — OpenRouter ships reasoning
                    # there for many models).
                    _extra_fields = getattr(delta, "model_extra", None) or {}
                    if not isinstance(_r, str) or not _r:
                        _r = (
                            _extra_fields.get("reasoning")
                            or _extra_fields.get("reasoning_content")
                            or _r
                        )
                    if isinstance(_r, str) and _r:
                        reasoning_text += _r
                        # Surface thinking tokens live as a typed REASONING
                        # event so the UI can render a "Reasoning" panel.
                        if _expose_reasoning:
                            yield StreamEvent(StreamEventType.REASONING, {
                                "text": _r,
                                "round": round_num + 1,
                            })
                    else:
                        # OpenRouter streams reasoning as a list of structured
                        # dicts on `reasoning_details` (e.g. deepseek-v3.1).
                        _rd = (
                            getattr(delta, "reasoning_details", None)
                            or _extra_fields.get("reasoning_details")
                        )
                        if isinstance(_rd, list):
                            for item in _rd:
                                if isinstance(item, dict):
                                    txt = (
                                        item.get("text")
                                        or item.get("summary")
                                        or item.get("content")
                                    )
                                else:
                                    txt = (
                                        getattr(item, "text", None)
                                        or getattr(item, "summary", None)
                                        or getattr(item, "content", None)
                                    )
                                if isinstance(txt, str) and txt:
                                    reasoning_text += txt
                                    if _expose_reasoning:
                                        yield StreamEvent(StreamEventType.REASONING, {
                                            "text": txt,
                                            "round": round_num + 1,
                                        })
                        elif not delta.tool_calls:
                            # Truly empty delta — track it; if all chunks are
                            # like this, log a warning at end of round.
                            _chunks_with_no_content_or_tools += 1
                            if _chunks_with_no_content_or_tools <= 3:
                                try:
                                    _d2 = delta.model_dump(exclude_none=True) if hasattr(delta, "model_dump") else {}
                                except Exception:
                                    _d2 = {}
                                logger.info(f"🔬 [LLM_STREAM_DIAG] empty delta #{_chunks_with_no_content_or_tools} keys={list(_d2.keys())} extra_keys={list(_extra_fields.keys())} sample={str(_d2)[:400]}")

            # Flush plan-filter carry-over and feed it through mermaid detector.
            tail = plan_filter.flush()
            # Surface any plan blocks that closed exactly at the stream end.
            for plan_text in plan_filter.pop_plans():
                yield StreamEvent(StreamEventType.PLAN, {
                    "plan_text": plan_text,
                    "round": round_num + 1,
                })
            if tail:
                tail = clarify_filter.feed(tail)
                for payload in clarify_filter.pop_payloads():
                    yield StreamEvent(StreamEventType.SUGGESTION_CHIPS, payload)
                if tail:
                    emit_text, mermaid_block = detect_mermaid_in_chunk(state, tail)
                    if emit_text:
                        yield StreamEvent(StreamEventType.CHUNK, {"text": emit_text})
                    if mermaid_block:
                        yield StreamEvent(StreamEventType.MERMAID_BLOCK, mermaid_block)
            # Drain any residual clarify carry.
            tail2 = clarify_filter.flush()
            for payload in clarify_filter.pop_payloads():
                yield StreamEvent(StreamEventType.SUGGESTION_CHIPS, payload)
            if tail2:
                yield StreamEvent(StreamEventType.CHUNK, {"text": tail2})

            if "<plan>" in full_text and "</plan>" in full_text:
                plan_already_streamed = True

            actual_input_tokens += round_input_tokens
            actual_output_tokens += round_output_tokens

            logger.info(f"📊 [LLM_STREAM] Round {round_num + 1} complete in {time.time() - _t_round:.2f}s: finish_reason={finish_reason}, text={len(full_text)} chars, reasoning={len(reasoning_text)} chars, chunks={_chunk_count}, empty_deltas={_chunks_with_no_content_or_tools}, tool_calls={len(tool_calls_acc)}, tokens={round_input_tokens}in/{round_output_tokens}out")
            if tool_calls_acc:
                for idx, tc in tool_calls_acc.items():
                    logger.info(f"📊 [TOOL_ACC] Final idx={idx}: name={tc['name']}, id={tc['id']}, args={len(tc['arguments'])} chars")
                    logger.debug(f"📊 [TOOL_ACC] idx={idx} raw args: {tc['arguments'][:500]}")

            # Detect truncation — if finish_reason is 'length', args may be incomplete
            if finish_reason == "length" and tool_calls_acc:
                logger.warning(f"⚠️ [LLM_STREAM] Stream truncated (finish_reason=length) with pending tool calls — arguments may be incomplete")

            # Process tool calls if any
            if tool_calls_acc:
                # Build assistant message with tool_calls for the messages array
                assistant_tool_calls = []
                for idx in sorted(tool_calls_acc.keys()):
                    tc = tool_calls_acc[idx]
                    raw_args = tc["arguments"]
                    # Ensure arguments is a valid JSON string (OpenAI tool-call contract)
                    try:
                        json.loads(raw_args)
                        valid_args = raw_args
                    except (json.JSONDecodeError, TypeError):
                        # Attempt to repair truncated JSON by closing open braces/brackets/strings
                        logger.warning(f"⚠️ [LLM_STREAM] Invalid JSON in tool args for {tc['name']} ({len(raw_args)} chars), attempting repair")
                        repaired = raw_args
                        # Close any unclosed string literal
                        if repaired.count('"') % 2 != 0:
                            repaired += '"'
                        # Close open brackets/braces
                        open_braces = repaired.count('{') - repaired.count('}')
                        open_brackets = repaired.count('[') - repaired.count(']')
                        repaired += ']' * max(0, open_brackets)
                        repaired += '}' * max(0, open_braces)
                        try:
                            json.loads(repaired)
                            logger.info(f"✅ [LLM_STREAM] JSON repair succeeded for {tc['name']}")
                            valid_args = repaired
                        except (json.JSONDecodeError, TypeError):
                            logger.error(f"❌ [LLM_STREAM] JSON repair failed for {tc['name']}, using empty args. First 200 chars: {raw_args[:200]}")
                            valid_args = json.dumps({})
                    assistant_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": valid_args}
                    })

                messages.append({
                    "role": "assistant",
                    "content": full_text if full_text else None,
                    "tool_calls": assistant_tool_calls
                })

                has_execute_code = False
                for tc_info in assistant_tool_calls:
                    fn_name = tc_info["function"]["name"]
                    tc_id = tc_info["id"]
                    raw_args = tc_info["function"]["arguments"]

                    try:
                        args = json.loads(raw_args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}

                    if fn_name == "internet_search":
                        internet_was_used = True
                        _internet_search_calls += 1
                        yield StreamEvent(StreamEventType.STAGE, {
                            "stage": "calling_tool",
                            "label": "Searching the internet",
                            "tool": "internet_search",
                        })
                        yield StreamEvent(StreamEventType.TOOL_STATUS, {"tool": "internet_search", "status": "running"})
                        _t_isearch = time.time()
                        logger.info(f"⏱️ [LLM_STREAM] internet_search START query={args.get('query','')!r}")
                        try:
                            from citra_internet_service import execute_internet_search
                            result = execute_internet_search(
                                query=args.get("query", ""),
                                context=args.get("context", ""),
                                user_id=user_id,
                                user_email=user_email
                            )
                        except Exception as e:
                            logger.error(f"❌ [LLM_STREAM] internet_search failed: {e}")
                            result = f"Error: {e}"
                        logger.info(f"⏱️ [LLM_STREAM] internet_search END took {time.time() - _t_isearch:.2f}s, result_len={len(str(result))}")
                        yield StreamEvent(StreamEventType.TOOL_STATUS, {"tool": "internet_search", "status": "complete"})
                        yield StreamEvent(StreamEventType.STAGE, {
                            "stage": "tool_complete",
                            "label": "Internet search done",
                            "tool": "internet_search",
                        })
                        _result_text = str(result)
                        # Once the research cap is hit, internet_search is removed
                        # from next round's tools — tell the model so it stops
                        # trying to search and answers. Without this it spends its
                        # reasoning planning another search that will never be offered.
                        if _internet_search_calls >= INTERNET_SEARCH_CAP and not _build_nudged:
                            _build_nudged = True
                            _result_text += (
                                "\n\n[SYSTEM: Research budget reached — internet_search is no "
                                "longer available this turn. Answer the user now using what you "
                                "have gathered above.]"
                            )
                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": _result_text})

                    elif fn_name == "execute_code":
                        has_execute_code = True
                        yield StreamEvent(StreamEventType.STAGE, {
                            "stage": "calling_tool",
                            "label": "Running code",
                            "tool": "execute_code",
                        })
                        yield StreamEvent(StreamEventType.TOOL_STATUS, {"tool": "execute_code", "status": "running"})
                        await asyncio.sleep(0)  # flush SSE events before tool execution

                        script = args.get('script', '').strip()
                        output_filename = args.get('output_filename', 'output.txt')

                        if not script:
                            messages.append({"role": "tool", "tool_call_id": tc_id,
                                             "content": json.dumps({"success": False, "stdout": "", "stderr": "Script is empty."})})
                            continue
                        if len(script) > MAX_SCRIPT_SIZE:
                            messages.append({"role": "tool", "tool_call_id": tc_id,
                                             "content": json.dumps({"success": False, "stdout": "", "stderr": f"Script too large ({len(script)} chars). Max {MAX_SCRIPT_SIZE}."})})
                            continue

                        from services.code_executor import execute_code

                        files_for_docker = []
                        seen_filenames = set()
                        # 1) Quick-chat-style attachments passed via the `files` arg.
                        if files:
                            for f in files:
                                fname = f.get('filename')
                                s3k = f.get('s3_key')
                                if not fname or not s3k or fname in seen_filenames:
                                    continue
                                files_for_docker.append({'filename': fname, 's3_key': s3k})
                                seen_filenames.add(fname)
                        # There is no second source any more: the vault mount
                        # (input_files -> resolve_vault_files) went with the
                        # personal vault, so attachments are all execute_code gets.
                        if files_for_docker:
                            logger.info(
                                f"⚙️ [LLM_STREAM] execute_code mounting {len(files_for_docker)} file(s): "
                                f"{[f['filename'] for f in files_for_docker]}"
                            )

                        session_for_exec = chat_session_id or f"stream_{user_id}_{int(time.time())}"

                        from services.sandbox_progress_relay import sandbox_progress_relay
                        async with sandbox_progress_relay() as (progress_cb, drain):
                            exec_task = asyncio.create_task(execute_code(
                                script=script,
                                session_id=session_for_exec,
                                files=files_for_docker,
                                output_filename=output_filename,
                                progress_cb=progress_cb,
                            ))
                            async for relay_evt in drain(exec_task):
                                yield relay_evt
                            exec_result = await exec_task

                        if exec_result.get('output_files'):
                            all_output_files.extend(exec_result['output_files'])

                        sanitized_stderr = re.sub(r'/tmp/qc_[a-f0-9_]+/', '/sandbox/', exec_result.get('stderr', ''))
                        tool_result_data = {
                            "success": exec_result['success'],
                            "stdout": exec_result.get('stdout', ''),
                            "stderr": sanitized_stderr,
                        }
                        if exec_result.get('output_files'):
                            tool_result_data["output_files"] = [
                                {"filename": f['filename'], "download_url": f['download_url']}
                                for f in exec_result['output_files']
                            ]
                        yield StreamEvent(StreamEventType.TOOL_STATUS, {"tool": "execute_code", "status": "complete"})
                        yield StreamEvent(StreamEventType.STAGE, {
                            "stage": "tool_complete",
                            "label": "Code run finished",
                            "tool": "execute_code",
                        })
                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps(tool_result_data)})

                    else:
                        logger.warning(f"⚠️ [LLM_STREAM] Unknown tool: {fn_name}")
                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": f"Unknown tool: {fn_name}"})

                # Continue loop for model follow-up after tool execution
                if round_num < MAX_TOOL_ROUNDS:
                    continue

            # No tool calls or max rounds — done
            # If the model stopped because it hit the output-token cap (and we
            # actually streamed visible content this round), surface that to
            # the user so they know the answer is incomplete.
            if finish_reason == "length" and not tool_calls_acc and full_text.strip():
                logger.warning(
                    f"⚠️ [LLM_STREAM] Final answer truncated (finish_reason=length) at "
                    f"~{effective_max_output_tokens} output tokens"
                )
                yield StreamEvent(
                    StreamEventType.CHUNK,
                    {"text": f"\n\n_[response truncated — output token limit ({effective_max_output_tokens}) reached. Ask a follow-up to continue.]_"}
                )
            break

        # If all rounds were tool calls and no real answer was produced, do one
        # final call WITHOUT tools to force the model to generate an answer.
        # Strip <plan>...</plan> blocks before checking — a plan-only output is
        # not a real answer (smaller models sometimes loop by re-emitting the
        # plan on every round instead of summarising results).
        _answer_text = re.sub(r"<plan>.*?</plan>", "", full_text, flags=re.DOTALL).strip()
        if not _answer_text:
            # If the previous round buried the answer in reasoning tokens,
            # surface that to the user instead of doing a redundant LLM call.
            if reasoning_text.strip():
                logger.info(
                    f"🔄 [LLM_STREAM] Round produced reasoning-only output ({len(reasoning_text)} chars) — surfacing as answer"
                )
                full_text = reasoning_text
                emit_text, mermaid_block = detect_mermaid_in_chunk(state, reasoning_text)
                if emit_text:
                    yield StreamEvent(StreamEventType.CHUNK, {"text": emit_text})
                if mermaid_block:
                    yield StreamEvent(StreamEventType.MERMAID_BLOCK, mermaid_block)
            else:
                logger.info("🔄 [LLM_STREAM] All rounds used for tool calls — final text-only round")
                kwargs_final = {
                    "model": model or _llm_default_model,
                    "messages": messages,
                    "max_tokens": effective_max_output_tokens,
                    "temperature": float(temperature) if temperature is not None else 0.2,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                _extra_final = get_llm_extra_body(model or _llm_default_model, tier=tier)
                if _extra_final:
                    _extra_final = dict(_extra_final)
                    if isinstance(_extra_final.get("reasoning"), dict):
                        _extra_final["reasoning"] = {
                            **_extra_final["reasoning"],
                            "exclude": True,
                        }
                    kwargs_final["extra_body"] = _extra_final
                full_text = ""
                _final_reasoning = ""
                stream = await client.chat.completions.create(**kwargs_final)
                async for chunk in stream:
                    if chunk.usage:
                        actual_input_tokens += chunk.usage.prompt_tokens or 0
                        actual_output_tokens += chunk.usage.completion_tokens or 0
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_text += delta.content
                        emit_text, mermaid_block = detect_mermaid_in_chunk(state, delta.content)
                        if emit_text:
                            yield StreamEvent(StreamEventType.CHUNK, {"text": emit_text})
                        if mermaid_block:
                            yield StreamEvent(StreamEventType.MERMAID_BLOCK, mermaid_block)
                    else:
                        _r = (
                            getattr(delta, "reasoning", None)
                            or getattr(delta, "reasoning_content", None)
                        )
                        if isinstance(_r, str) and _r:
                            _final_reasoning += _r
                        else:
                            _rd = getattr(delta, "reasoning_details", None)
                            if isinstance(_rd, list):
                                for item in _rd:
                                    if isinstance(item, dict):
                                        txt = (
                                            item.get("text")
                                            or item.get("summary")
                                            or item.get("content")
                                        )
                                    else:
                                        txt = (
                                            getattr(item, "text", None)
                                            or getattr(item, "summary", None)
                                            or getattr(item, "content", None)
                                        )
                                    if isinstance(txt, str) and txt:
                                        _final_reasoning += txt
                # Final fallback: surface reasoning if even the no-tools retry
                # only produced reasoning tokens.
                if not full_text.strip() and _final_reasoning.strip():
                    logger.info(
                        f"🔄 [LLM_STREAM] Final retry reasoning-only ({len(_final_reasoning)} chars) — surfacing"
                    )
                    full_text = _final_reasoning
                    emit_text, mermaid_block = detect_mermaid_in_chunk(state, _final_reasoning)
                    if emit_text:
                        yield StreamEvent(StreamEventType.CHUNK, {"text": emit_text})
                    if mermaid_block:
                        yield StreamEvent(StreamEventType.MERMAID_BLOCK, mermaid_block)

        # Flush remaining buffer
        remaining_text = flush_buffer(state)
        if remaining_text:
            yield StreamEvent(StreamEventType.CHUNK, {"text": remaining_text})

        # Emit output files as download links + dedicated event
        if all_output_files:
            yield StreamEvent(StreamEventType.CHUNK, {"text": "\n\n"})
            for of in all_output_files:
                yield StreamEvent(StreamEventType.CHUNK, {
                    "text": f"📎 <!-- download -->[Download {of['filename']}]({of['download_url']})\n"
                })
            yield StreamEvent(StreamEventType.OUTPUT_FILES, {
                "files": [
                    {"filename": f['filename'], "download_url": f['download_url']}
                    for f in all_output_files
                ]
            })

        elapsed_time = time.time() - start_time
        logger.info(f"✅ [LLM_STREAM] Stream complete in {elapsed_time:.2f}s, {len(all_output_files)} output files")

        # Track billing
        try:
            from middleware.credit_check_middleware import track_query_usage
            track_query_usage(
                user_id=user_id,
                email=user_email or user_id,
                model=model or _llm_default_model,
                input_tokens=actual_input_tokens,
                output_tokens=actual_output_tokens,
                cached_tokens=0,
            )
        except Exception as billing_err:
            logger.warning(f"⚠️ [LLM_STREAM] Billing failed: {billing_err}")

        # Yield usage event
        yield StreamEvent(StreamEventType.USAGE, {
            "estimated_output_tokens": state.total_output_tokens,
            "actual_input_tokens": actual_input_tokens,
            "actual_output_tokens": actual_output_tokens,
            "processing_time": elapsed_time,
            "mermaid_blocks_count": len(state.mermaid_blocks)
        })

        # Yield done event
        yield StreamEvent(StreamEventType.DONE, {
            "processing_time": elapsed_time,
            "user_id": user_id
        })

    except Exception as e:
        logger.error(f"❌ [LLM_STREAM] Error: {e}")
        yield StreamEvent(StreamEventType.ERROR, {"message": str(e)})


# ═══════════════════════════════════════════════════════════════════════════════════════
# UNIFIED STREAMING FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════════════

async def stream_llm_response(
    prompt: str,
    model: str = None,
    system: str = None,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    enable_internet_search: bool = False,
    user_id: str = None,
    user_email: str = None,
    profession: str = None,
    dynamic_context: str = None,
    chat_session_id: str = None,
    files: Optional[List[Dict[str, str]]] = None,
    temperature: Optional[float] = None,
    tier: Tier = DEFAULT_TIER,
    use_enterprise_data: bool = False,
    org_id: Optional[str] = None,
    dept_id: Optional[str] = None,
    dept_ids: Optional[List[str]] = None,
    roles: Optional[List[str]] = None,
    jwt_token: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    use_personal_data: bool = False,
    selected_folder_ids: Optional[List[str]] = None,
    persona_data: Optional[Dict[str, Any]] = None,
    enterprise_prefetch: Optional[Dict[str, Any]] = None,
    vault_summary: Optional[Dict[str, int]] = None,
) -> AsyncGenerator[StreamEvent, None]:
    """
    Unified streaming function — routes to stream_llm_response_impl.
    
    Yields:
        StreamEvent objects with response chunks and metadata
    """
    logger.info(f"🚀 [STREAM_LLM] Starting streaming (model={model}, tier={tier})")
    logger.debug(f"🔵 [STREAM_LLM] prompt len: {len(prompt) if prompt else 0}")
    logger.debug(f"🔵 [STREAM_LLM] system len: {len(system) if system else 0}")
    logger.debug(f"🔵 [STREAM_LLM] context_chunks: {len(context_chunks) if context_chunks else 0}")
    logger.debug(f"🔵 [STREAM_LLM] attachments: {len(attachments) if attachments else 0}")
    
    event_count = 0
    try:
        async for event in stream_llm_response_impl(
            prompt=prompt,
            system=system,
            model=model,
            context_chunks=context_chunks,
            conversation_history=conversation_history,
            attachments=attachments,
            enable_internet_search=enable_internet_search,
            user_id=user_id,
            user_email=user_email,
            profession=profession,
            dynamic_context=dynamic_context,
            chat_session_id=chat_session_id,
            files=files,
            temperature=temperature,
            tier=tier,
            use_enterprise_data=use_enterprise_data,
            org_id=org_id,
            dept_id=dept_id,
            dept_ids=dept_ids,
            roles=roles,
            jwt_token=jwt_token,
            max_output_tokens=max_output_tokens,
            use_personal_data=use_personal_data,
            selected_folder_ids=selected_folder_ids,
            persona_data=persona_data,
            enterprise_prefetch=enterprise_prefetch,
            vault_summary=vault_summary,
        ):
            event_count += 1
            logger.debug(f"🔵 [STREAM_LLM] yielded event #{event_count}: {event.event_type.value}")
            yield event
    except Exception as stream_error:
        logger.error(f"🔴 [STREAM_LLM] Stream error: {stream_error}", exc_info=True)
        yield StreamEvent(StreamEventType.ERROR, {"message": f"Stream error: {stream_error}"})
        return
    logger.debug(f"🔵 [STREAM_LLM] Stream complete. Total events: {event_count}")


# ═══════════════════════════════════════════════════════════════════════════════════════
# CITATION EXTRACTION FROM COMPLETE RESPONSE
# ═══════════════════════════════════════════════════════════════════════════════════════

def _tag_citation_origins(citations: Dict[str, Any]) -> Dict[str, Any]:
    """
    Walk the citation block and stamp each entry with `source_origin`. Lets the
    UI render distinct chips for vault docs / web sources / structured data
    without re-inferring on the client.
    """
    if not isinstance(citations, dict):
        return citations
    out = {}
    for bucket, entries in citations.items():
        bucket_l = str(bucket).lower()
        if bucket_l in {"documents", "doc", "docs"}:
            origin = "vault"
        elif bucket_l in {"chunks", "chunk"}:
            origin = "vault"
        elif bucket_l in {"web", "internet", "links"}:
            origin = "internet"
        elif bucket_l in {"structured", "saas", "tables"}:
            origin = "structured"
        elif bucket_l in {"enterprise"}:
            origin = "enterprise"
        else:
            origin = bucket_l or "unknown"

        if isinstance(entries, list):
            tagged_entries = []
            for e in entries:
                if isinstance(e, dict):
                    if "source_origin" not in e:
                        e = {**e, "source_origin": origin}
                    tagged_entries.append(e)
                else:
                    tagged_entries.append(e)
            out[bucket] = tagged_entries
        else:
            out[bucket] = entries
    return out


def extract_citations_from_response(full_response: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Extract citation JSON block from the end of a response.
    
    Returns:
        Tuple of (cleaned_response, citations_dict_or_none)
    """
    if not full_response:
        return full_response, None
    
    # Look for JSON citation block at end
    # Pattern: ```json { "citations": {...} } ```
    json_block_pattern = re.compile(
        r'```(?:json)?\s*(\{[\s\S]*?"citations"\s*:\s*\{[\s\S]*?\}[\s\S]*?\})\s*```\s*$',
        re.IGNORECASE
    )
    
    match = json_block_pattern.search(full_response)
    if match:
        try:
            citations = json.loads(match.group(1).strip())
            cleaned = full_response[:match.start()].strip()
            citations_inner = citations.get('citations', citations)
            citations_inner = _tag_citation_origins(citations_inner)
            logger.info(f"📚 [CITATIONS] Extracted citations from JSON block (origins tagged)")
            return cleaned, citations_inner
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ [CITATIONS] Failed to parse citation JSON: {e}")
    
    # Look for WEB_SOURCES block
    web_sources_pattern = re.compile(
        r'\n\s*---(?:WEB_SOURCES|LLM_CITATIONS)---\s*\n([\s\S]*?)\n\s*---END_(?:WEB_SOURCES|LLM_CITATIONS)---',
        re.IGNORECASE
    )
    
    match = web_sources_pattern.search(full_response)
    if match:
        try:
            web_citations = json.loads(match.group(1).strip())
            cleaned = web_sources_pattern.sub('', full_response).strip()
            web_list = web_citations.get('web', [])
            tagged_web = []
            for entry in web_list:
                if isinstance(entry, dict) and "source_origin" not in entry:
                    entry = {**entry, "source_origin": "internet"}
                tagged_web.append(entry)
            logger.info(f"🌐 [CITATIONS] Extracted {len(tagged_web)} web citations")
            return cleaned, {'web': tagged_web}
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ [CITATIONS] Failed to parse web sources JSON: {e}")
    
    return full_response, None
