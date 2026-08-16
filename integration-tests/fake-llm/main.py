# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Deterministic mock LLM — OpenAI-compatible /v1/chat/completions endpoint.

Pattern-matches the prompt and returns a canned response. Patterns live
in ``prompt_patterns.yaml`` so adding a new test scenario doesn't
require editing this file.

Why not real OpenAI: every CI run shouldn't depend on an external API.
The real-LLM nightly job uses a different env (``LLM_MODE=real``) and
points services at the actual OpenAI endpoint.

The endpoint is intentionally permissive — it accepts whatever fields
the real OpenAI API accepts and returns a minimal but valid response
shape. Tools are echoed back as if the LLM "decided" to call them
when the canned response declares ``tool_calls``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("fake-llm")


_PATTERNS_PATH = Path(__file__).parent / "prompt_patterns.yaml"


def _load_patterns() -> List[Dict[str, Any]]:
    if not _PATTERNS_PATH.exists():
        logger.warning("[fake-llm] no prompt_patterns.yaml; using empty table")
        return []
    raw = yaml.safe_load(_PATTERNS_PATH.read_text()) or {}
    return raw.get("patterns") or []


_PATTERNS: List[Dict[str, Any]] = _load_patterns()
_INVOCATIONS: List[Dict[str, Any]] = []  # capture for test assertions


# ── OpenAI-compatible request/response shapes (minimal subset) ──────────


class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None


# ── Pattern resolution ──────────────────────────────────────────────────


def _flatten_messages(messages: List[ChatMessage]) -> str:
    """Join system + user messages into one searchable string."""
    parts = []
    for m in messages:
        c = m.content or ""
        parts.append(f"[{m.role}] {c}")
    return "\n".join(parts)


def _match_pattern(prompt: str) -> Optional[Dict[str, Any]]:
    """First pattern whose ``match`` regex hits wins."""
    for p in _PATTERNS:
        regex = p.get("match")
        if not regex:
            continue
        try:
            if re.search(regex, prompt, re.IGNORECASE | re.DOTALL):
                return p
        except re.error as exc:
            logger.warning("[fake-llm] bad regex %r: %s", regex, exc)
    return None


def _render_response(pattern: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    """Resolve a pattern's response template against the prompt.

    Two response shapes:
      ``content``: plain text → emit a normal assistant message
      ``tool_call``: dict with name + arguments → emit a tool_calls assistant
    """
    if "content" in pattern:
        return {
            "role": "assistant",
            "content": str(pattern["content"]),
            "tool_calls": None,
        }
    if "content_template" in pattern:
        # Simple ``{{capture}}`` → first regex capture group substitution.
        regex = pattern.get("match", "")
        try:
            m = re.search(regex, prompt, re.IGNORECASE | re.DOTALL)
            content = pattern["content_template"]
            if m:
                for i, group in enumerate(m.groups(), start=1):
                    content = content.replace(f"{{{{${i}}}}}", group or "")
            return {"role": "assistant", "content": content, "tool_calls": None}
        except re.error:
            return {"role": "assistant", "content": pattern["content_template"], "tool_calls": None}
    if "tool_call" in pattern:
        tc = pattern["tool_call"]
        return {
            "role": "assistant",
            "content": tc.get("preface") or "",
            "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("arguments") or {}),
                },
            }],
        }
    return {"role": "assistant", "content": "", "tool_calls": None}


# ── App ─────────────────────────────────────────────────────────────────


app = FastAPI(title="fake-llm", version="0.1.0")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "patterns_loaded": len(_PATTERNS),
        "invocations": len(_INVOCATIONS),
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest) -> Dict[str, Any]:
    prompt = _flatten_messages(req.messages)
    pattern = _match_pattern(prompt)

    # Capture for test assertions.
    _INVOCATIONS.append({
        "model": req.model,
        "prompt_chars": len(prompt),
        "messages": [m.model_dump() for m in req.messages],
        "tools": req.tools or [],
        "matched_pattern_id": (pattern or {}).get("id"),
        "at": time.time(),
    })

    if pattern is None:
        logger.warning("[fake-llm] no pattern matched; defaulting")
        msg = {
            "role": "assistant",
            "content": (
                "I don't have a pattern for this prompt. "
                "(fake-llm — add one in prompt_patterns.yaml.)"
            ),
            "tool_calls": None,
        }
    else:
        msg = _render_response(pattern, prompt)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop",
        }],
        "usage": {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(msg.get("content") or "") // 4,
            "total_tokens": (len(prompt) + len(msg.get("content") or "")) // 4,
        },
    }


@app.get("/admin/invocations")
def get_invocations() -> Dict[str, Any]:
    """Tests assert against this — how many calls, what prompts, what patterns."""
    return {"count": len(_INVOCATIONS), "items": _INVOCATIONS}


@app.post("/admin/reset")
def reset_invocations() -> Dict[str, Any]:
    _INVOCATIONS.clear()
    return {"status": "ok"}


@app.post("/admin/reload")
def reload_patterns() -> Dict[str, Any]:
    global _PATTERNS
    _PATTERNS = _load_patterns()
    return {"status": "ok", "patterns_loaded": len(_PATTERNS)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8600"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
