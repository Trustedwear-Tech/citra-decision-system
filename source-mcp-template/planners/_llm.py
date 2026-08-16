# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
LLM helper for planner modules.

Uses httpx against the OpenAI-compatible inference gateway configured in
config.Settings, matching the pattern already used by rag/*_engine.py.

RULE #1 — fail loud: this helper RAISES :class:`PlannerLLMError` on any failure
(network, auth, empty/null content, non-JSON body, bad shape) instead of
returning ``None``. A silent ``None`` let callers treat "the planner LLM
failed" as "the planner had nothing to say" and silently degrade to a worse
plan (or fabricate) — the exact bug behind the BSPHCL "weather=5" hallucination.
The raised error carries the real, actionable reason.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import httpx

from config import get_settings

logger = logging.getLogger(__name__)


class PlannerLLMError(RuntimeError):
    """A planner LLM call FAILED — network/auth/timeout, empty (null) content,
    or a non-JSON / malformed body.

    Raised, never swallowed to ``None`` (RULE #1). The message states the real
    reason so the caller surfaces it instead of silently falling back to a
    degraded plan.
    """


async def call_json_llm(
    *,
    system: str,
    user: str,
    timeout_seconds: float = 45.0,
    max_tokens: int = 16000,
) -> Dict[str, Any]:
    """Fire a JSON-mode chat completion and return the parsed dict.

    Raises :class:`PlannerLLMError` on any failure — caller must NOT treat a
    failure as an empty plan. ``max_tokens`` defaults high enough to cover a
    reasoning-model's hidden tokens PLUS the JSON answer; for GLM-class models
    also set ``LLM_EXTRA_BODY={"reasoning":{"exclude":true}}`` so the JSON lands
    in ``content`` rather than a reasoning field (otherwise ``content`` is null).
    """
    cfg = get_settings()
    payload: Dict[str, Any] = {
        "model": cfg.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    # Provider knobs (e.g. OpenRouter reasoning.exclude for GLM-class models, so
    # JSON lands in ``content`` instead of a reasoning field). Merged at TOP
    # LEVEL — this is a raw HTTP POST, not the OpenAI SDK, so an ``extra_body``
    # wrapper key would be ignored by the provider. Empty by default.
    if cfg.llm_extra_body:
        payload.update(cfg.llm_extra_body)
    headers = {
        "Authorization": f"Bearer {cfg.llm_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{cfg.llm_base_url.rstrip('/')}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # network / auth / timeout / non-200
        logger.error(f"📋 [PLANNER_LLM] call failed: {exc!r}")
        raise PlannerLLMError(f"LLM call failed: {type(exc).__name__}: {exc}") from exc

    # Shape guard.
    try:
        choice0 = data["choices"][0]
        content = choice0["message"].get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise PlannerLLMError(f"unexpected LLM response shape: {exc}") from exc

    # Empty/null content — the GLM-class reasoning-model failure mode. Diagnose
    # it precisely (this is what surfaced as a blank JSONDecodeError before).
    finish = choice0.get("finish_reason")
    if not isinstance(content, str) or not content.strip():
        hint = (
            " (truncated at max_tokens — reasoning consumed the budget; raise "
            "max_tokens or set LLM_EXTRA_BODY reasoning.exclude)"
            if finish == "length"
            else " (model emitted reasoning only / null content; set "
            'LLM_EXTRA_BODY={"reasoning":{"exclude":true}})'
        )
        raise PlannerLLMError(f"LLM returned empty content{hint}")

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PlannerLLMError(
            f"LLM returned non-JSON content: {exc}; head={content[:160]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PlannerLLMError(
            f"LLM returned JSON {type(parsed).__name__}, expected an object"
        )
    return parsed
