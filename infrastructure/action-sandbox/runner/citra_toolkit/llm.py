# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""LLM client for Deep Analytics Chat.

Routes every call to a single large model — whichever is configured via
``LLM_LARGE_MODEL`` (or the legacy ``LLM_MODEL``) in Citra-Service. The
specific model is injected at deploy time via the action-chat-service
``.env``; the toolkit makes no assumption about which one. The legacy
``small`` / ``medium`` / ``large`` shims still exist for source
compatibility but every call routes to the same large LLM —
Citra-Service ignores the ``tier`` field. Prefer ``llm.complete`` or
``llm.stream`` directly; the tiered helpers are kept only so older
agent code keeps working.

Streaming is supported by ``stream=True`` (returns the underlying SSE
``response.iter_lines()`` so the caller can parse OpenAI-format chunks).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Literal

import httpx

from ._proxy import proxy_url
from .client import _bearer_headers


Tier = Literal["small", "medium", "large"]


@dataclass
class CompletionResult:
    text: str
    role: str
    raw: dict[str, Any]
    model: str
    usage: dict[str, Any]


def _normalize_messages(messages: Any) -> list[dict[str, Any]]:
    """Accept either OpenAI ``[{role,content}, ...]`` shape OR a bare
    string (auto-wrapped to a single user message). Strings are the
    most common shape the agent's generated Python emits, so silently
    promoting them keeps ``llm.complete("summarise this")`` working
    instead of returning HTTP 400. List inputs pass through untouched.
    """
    if isinstance(messages, str):
        text = messages.strip()
        return [{"role": "user", "content": text}] if text else []
    if isinstance(messages, dict):
        # Single message dict — wrap into a list.
        return [messages]
    if isinstance(messages, list):
        return messages
    raise TypeError(
        f"messages must be a list of {{role,content}} dicts or a string; "
        f"got {type(messages).__name__}"
    )


def complete(messages: Any, *,
             tier: Tier = "large",
             temperature: float | None = None,
             max_tokens: int | None = None,
             response_format: dict[str, Any] | None = None,
             timeout: float = 180.0) -> CompletionResult:
    """Non-streaming chat completion.

    ``messages`` may be either the standard OpenAI list-of-dicts shape
    (``[{"role": "user", "content": "..."}]``) or a bare string (which
    is auto-wrapped to a single user message). ``tier`` is accepted but
    ignored — Deep Analytics Chat is locked to the configured large LLM.
    """
    body: dict[str, Any] = {
        "tier": "large",
        "messages": _normalize_messages(messages),
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    if max_tokens is not None:
        body["max_tokens"] = int(max_tokens)
    if response_format is not None:
        body["response_format"] = response_format
    with httpx.Client(timeout=timeout, headers=_bearer_headers()) as c:
        r = c.post(proxy_url("llm"), json=body)
        r.raise_for_status()
        data = r.json() or {}
    choices = data.get("choices") or []
    msg = (choices[0] or {}).get("message") if choices else {}
    msg = msg or {}
    return CompletionResult(
        text=str(msg.get("content") or ""),
        role=str(msg.get("role") or "assistant"),
        raw=data,
        model=str(data.get("model") or ""),
        usage=data.get("usage") or {},
    )


def stream(messages: Any, *,
           tier: Tier = "large",
           temperature: float | None = None,
           max_tokens: int | None = None,
           timeout: float | None = None) -> Iterator[str]:
    """Streaming completion. Yields raw SSE lines (``data: {...}``).

    ``messages`` accepts either the standard list-of-dicts shape or
    a bare string (auto-wrapped to a single user message). ``tier`` is
    accepted but ignored — locked to the large LLM."""
    body: dict[str, Any] = {
        "tier": "large",
        "messages": _normalize_messages(messages),
        "stream": True,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    if max_tokens is not None:
        body["max_tokens"] = int(max_tokens)
    with httpx.Client(timeout=timeout, headers=_bearer_headers()) as c:
        with c.stream("POST", proxy_url("llm"), json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    yield line


# All three tier shims route to the same large LLM. Kept for source
# compatibility with older skills/snippets — new code should call
# ``llm.complete`` or ``llm.stream`` directly.
def small(messages: list[dict[str, Any]], **kw: Any) -> CompletionResult:
    kw.pop("tier", None)
    return complete(messages, tier="large", **kw)


def medium(messages: list[dict[str, Any]], **kw: Any) -> CompletionResult:
    kw.pop("tier", None)
    return complete(messages, tier="large", **kw)


def large(messages: list[dict[str, Any]], **kw: Any) -> CompletionResult:
    kw.pop("tier", None)
    return complete(messages, tier="large", **kw)
