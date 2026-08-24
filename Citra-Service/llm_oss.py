# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
llm_oss.py

Centralized module for LLM chat completions in Citra AI.
Handles authentication, token usage tracking (billing), function calling (tools),
streaming, and JSON mode.

Provider is fully configurable via environment variables — see llm_client.py.
Set LLM_BASE_URL + LLM_API_KEY + LLM_MODEL to configure.

Used by:
- Report Composer
- Presentation Generator
- Diagram Generator
- Agentic RAG (planner, agents, context merger)
- Query Router
- Document Manager
- Entity Extraction
- OpenAI Compat API
"""

import os
import re
import json
import logging
import time
import asyncio
from typing import Optional, Dict, Any, List, Generator

from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError

from citra_llm import get_llm_client, get_default_model, get_llm_extra_body
from llm.llm_tiers import DEFAULT_TIER, Tier, coerce_tier
from token_utils import MAX_INPUT_TOKENS, truncate_to_token_limit, count_tokens

# Middleware for billing
try:
    from middleware.credit_check_middleware import track_query_usage, get_usage_service, check_user_credits
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False

logger = logging.getLogger(__name__)

# Hard cap for max output tokens. glm-5.1 supports the full 128K context for
# output; the per-request context-window cap below still bounds this to
# (MODEL_CONTEXT_WINDOW - input), so a large prompt naturally limits output.
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", 128000))

# Per-tier output-token caps. Some providers / subscription tiers reject any
# request whose ``max_tokens`` exceeds the model's allowed output. These caps
# are applied AFTER the global ``MAX_OUTPUT_TOKENS``
# and are env-overridable.
_TIER_OUTPUT_CAPS: Dict[str, int] = {
    "small":  int(os.getenv("LLM_SMALL_MAX_OUTPUT_TOKENS",  8000)),
    "medium": int(os.getenv("LLM_MEDIUM_MAX_OUTPUT_TOKENS", 16000)),
    "large":  int(os.getenv("LLM_LARGE_MAX_OUTPUT_TOKENS",  128000)),
}

_TIER_INPUT_CAPS: Dict[str, int] = {
    "small":  int(os.getenv("LLM_SMALL_MAX_INPUT_TOKENS",  30000)),
    "medium": int(os.getenv("LLM_MEDIUM_MAX_INPUT_TOKENS", 30000)),
    "large":  int(os.getenv("LLM_LARGE_MAX_INPUT_TOKENS",  MAX_INPUT_TOKENS)),
}


def _resolve_output_cap(tier: Tier) -> int:
    """Return the smallest of the global cap and the tier-specific cap."""
    return min(MAX_OUTPUT_TOKENS, _TIER_OUTPUT_CAPS.get(tier, MAX_OUTPUT_TOKENS))


def _resolve_input_cap(tier: Tier) -> int:
    """Return the tier-specific input cap."""
    return _TIER_INPUT_CAPS.get(tier, MAX_INPUT_TOKENS)


def _lift_to_ceiling(max_tokens: int, output_cap: int, max_allowed_output: int) -> int:
    """Lift a low per-call ``max_tokens`` to the model's real output ceiling.

    ``max_tokens`` is an UPPER bound, not a target: the model stops at
    ``finish_reason=stop`` when it's done, and billing is on tokens ACTUALLY
    generated — so a generous cap is COST-NEUTRAL for a normal completion and
    only ever PREVENTS truncation. Reasoning models (GLM-5.1, DeepSeek-v4-pro,
    …) spend hidden reasoning tokens against this same budget, so a low per-call
    cap (e.g. 4000) truncates the visible output (``finish_reason=length``) and
    forces a wasted auto-retry — the systemic "max_tokens pain".

    So we IGNORE low per-call values and request the ceiling: the tier output
    cap (small=8K / medium=16K / large=128K — the tier already encodes the
    intended output-size class), bounded by the remaining context window. A
    caller no longer needs to tune ``max_tokens`` per request; pick the tier.
    """
    ceiling = output_cap
    if max_allowed_output > 0:
        ceiling = min(ceiling, max_allowed_output)
    return max(max_tokens, ceiling) if ceiling > 0 else max_tokens


def _get_client(tier: Tier = DEFAULT_TIER) -> OpenAI:
    """Get the configured LLM sync client for the given tier."""
    return get_llm_client(async_=False, tier=tier)


def _resolve_model(model: Optional[str], tier: Tier) -> str:
    """Resolve the effective model name: explicit ``model`` arg wins, else tier default."""
    if model:
        return model
    return get_default_model(tier)


def _check_credits(user_id: str, caller: str = "LLM_OSS") -> None:
    """Credit enforcement disabled — license model."""
    pass


def _track_usage(
    user_id: str,
    user_email: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    internet_grounding_cost: float = 0.0,
    caller: str = "LLM_OSS"
) -> None:
    """Track token usage for billing."""
    if not BILLING_AVAILABLE or not user_id:
        if not user_id:
            logging.warning(
                f"⚠️ [{caller}] BILLING SKIPPED — user_id is None/empty. "
                f"{input_tokens} input + {output_tokens} output tokens went UNBILLED. model={model}"
            )
        return

    try:
        result = track_query_usage(
            user_id=user_id,
            email=user_email or user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            internet_grounding_cost=internet_grounding_cost
        )
        if result and result.get('success'):
            cost_msg = f" + {internet_grounding_cost:.2f} internet credits" if internet_grounding_cost > 0 else ""
            logging.info(f"✅ [{caller}] Usage tracked for {user_id} ({input_tokens}in/{output_tokens}out{cost_msg})")
        else:
            logging.warning(f"⚠️ [{caller}] Usage tracking returned: {result.get('message', 'unknown')} for {user_id}")
    except Exception as e:
        logging.error(f"❌ [{caller}] Failed to track usage: {e}")


def _build_messages(system_prompt: str, user_prompt: str) -> List[Dict[str, str]]:
    """Build the messages array for chat completions."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _execute_tool_calls(tool_calls, user_id: str, user_email: str) -> List[Dict[str, str]]:
    """
    Execute tool calls returned by the model and return tool result messages.
    Currently supports: internet_search (from citra_internet_service).
    """
    tool_messages = []
    for tool_call in tool_calls:
        function_name = tool_call.function.name
        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            arguments = {}

        if function_name == "internet_search":
            try:
                from citra_internet_service import execute_internet_search
                result = execute_internet_search(
                    query=arguments.get("query", ""),
                    context=arguments.get("context", ""),
                    user_id=user_id,
                    user_email=user_email
                )
            except Exception as e:
                logger.error(f"❌ [LLM_OSS] internet_search tool failed: {e}")
                result = f"Error executing internet search: {str(e)}"
        else:
            logger.warning(f"⚠️ [LLM_OSS] Unknown tool call: {function_name}")
            result = f"Unknown tool: {function_name}"

        tool_messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": str(result)
        })

    return tool_messages


def llm_call(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    user_id: str = None,
    user_email: str = None,
    max_tokens: int = 16000,
    temperature: float = 0.7,
    top_p: float = None,
    tools: List[Dict] = None,
    tool_choice: str = "auto",
    json_mode: bool = False,
    tier: Tier = DEFAULT_TIER,
    reasoning_effort: Optional[str] = None,
) -> str:
    """
    Execute an LLM chat completion call with token tracking.

    Args:
        system_prompt: The system instruction.
        user_prompt: The user query + context.
        model: Model ID (from LLM_MODEL env var).
        user_id: User ID for billing.
        user_email: User Email for billing.
        max_tokens: Maximum tokens to generate (default: 20000).
        temperature: Sampling temperature (default: 0.7).
        top_p: Nucleus sampling (default: None, API default).
        tools: Optional list of OpenAI function tool definitions.
        tool_choice: Tool choice strategy ("auto", "none", "required").
        json_mode: If True, request JSON output format.

    Returns:
        The generated text response.
    """
    tier = coerce_tier(tier)
    model = _resolve_model(model, tier)
    _check_credits(user_id, "LLM_OSS")

    # Truncate user_prompt so total input stays within tier's input cap
    _input_cap = _resolve_input_cap(tier)
    sys_tokens = count_tokens(system_prompt or "")
    user_budget = _input_cap - sys_tokens
    user_prompt = truncate_to_token_limit(user_prompt, user_budget, label="user_prompt")

    # Hard cap: enforce API tier output token limit (per-tier)
    _output_cap = _resolve_output_cap(tier)
    if max_tokens > _output_cap:
        logger.info(f"📏 [HARD_CAP] Capping max_tokens from {max_tokens:,} → {_output_cap:,} (tier={tier} API limit)")
        max_tokens = _output_cap

    # Cap max_tokens so prompt + output fits within model context window
    MODEL_CONTEXT_WINDOW = int(os.getenv("MODEL_CONTEXT_WINDOW", 202752))
    actual_input_tokens = sys_tokens + count_tokens(user_prompt or "")
    max_allowed_output = MODEL_CONTEXT_WINDOW - actual_input_tokens - 1000  # 1K safety buffer
    if max_tokens > max_allowed_output > 0:
        logger.info(f"📏 [TOKEN_CAP] Capping max_tokens from {max_tokens:,} → {max_allowed_output:,} "
                     f"(input={actual_input_tokens:,}, context_window={MODEL_CONTEXT_WINDOW:,})")
        max_tokens = max_allowed_output

    # Lift a low per-call cap to the model's real ceiling so reasoning tokens
    # can't truncate the visible output (see _lift_to_ceiling). Cost-neutral.
    max_tokens = _lift_to_ceiling(max_tokens, _output_cap, max_allowed_output)

    client = _get_client(tier)
    messages = _build_messages(system_prompt, user_prompt)

    # Build kwargs
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    _extra = get_llm_extra_body(model, tier=tier)
    if reasoning_effort:
        _existing_reasoning = _extra.get("reasoning") if isinstance(_extra.get("reasoning"), dict) else {}
        _extra["reasoning"] = {**_existing_reasoning, "effort": reasoning_effort, "exclude": _existing_reasoning.get("exclude", False)}
    if _extra:
        kwargs["extra_body"] = _extra

    if top_p is not None:
        kwargs["top_p"] = top_p

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice

    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0
    max_tool_rounds = 5

    for attempt in range(3):
        try:
            logging.info(f"🚀 [LLM_OSS] Calling {model} for user {user_id} (attempt {attempt + 1})")

            response = client.chat.completions.create(**kwargs)

            # Handle tool calls in a loop
            tool_round = 0
            while response.choices[0].finish_reason == "tool_calls" and tool_round < max_tool_rounds:
                tool_round += 1
                logging.info(f"🔧 [LLM_OSS] Tool call round {tool_round}")

                # Track tokens from this round
                if response.usage:
                    total_input_tokens += response.usage.prompt_tokens or 0
                    total_output_tokens += response.usage.completion_tokens or 0
                    if hasattr(response.usage, 'prompt_tokens_details') and response.usage.prompt_tokens_details:
                        total_cached_tokens += getattr(response.usage.prompt_tokens_details, 'cached_tokens', 0)

                # Add assistant message with tool calls
                assistant_msg = response.choices[0].message
                messages.append(assistant_msg)

                # Execute tool calls
                tool_messages = _execute_tool_calls(
                    assistant_msg.tool_calls,
                    user_id=user_id,
                    user_email=user_email
                )
                messages.extend(tool_messages)

                # Call model again with tool results
                kwargs["messages"] = messages
                response = client.chat.completions.create(**kwargs)

            # Final response
            answer = response.choices[0].message.content or ""

            # Surface output truncation explicitly so callers don't mistake
            # "model burned all output tokens on excluded reasoning then
            # hit the cap mid-JSON" for "model returned nothing". This
            # bites for reasoning models (Claude w/ thinking, GLM-4.6/5.1,
            # DeepSeek-R1, QwQ) where hidden reasoning tokens count against
            # max_tokens but never reach `content`.
            _finish_reason = getattr(response.choices[0], "finish_reason", None)
            if _finish_reason == "length":
                _content_len = len(answer.strip())
                logging.warning(
                    f"⚠️ [LLM_OSS] Output truncated by max_tokens "
                    f"(finish_reason=length, content_chars={_content_len}, "
                    f"max_tokens={max_tokens}, model={model}). "
                    f"If content is empty/short, bump max_tokens — "
                    f"reasoning tokens are likely eating the budget."
                )

                # AUTO-RECOVERY: when the visible content is suspiciously
                # short relative to max_tokens (≤25% of the budget at the
                # generous 4 chars/token estimate), the reasoning trace ate
                # nearly all the budget. Retry ONCE with doubled max_tokens
                # so the model has room for both reasoning AND the full
                # visible output. Caller's `reasoning.effort` setting is
                # preserved — we intentionally do NOT disable reasoning on
                # retry (reasoning is load-bearing for high-quality JSON
                # generation; turning it off can degrade output structure).
                # Capped at 120,000 to stay inside the model's 128K context
                # window (input for these calls is small — a few thousand
                # tokens — so output can use almost the whole window).
                _expected_chars = max_tokens * 4  # ~chars in non-reasoning output
                # The retry ceiling must respect the CONTEXT WINDOW remainder
                # (max_allowed_output) — after the lift, max_tokens may already
                # equal it, and doubling past it turns a degraded-but-usable
                # truncated answer into a hard provider 400.
                _retry_ceiling = min(
                    128_000,
                    max_allowed_output if max_allowed_output > 0 else 128_000,
                )
                _retry_truncation = (
                    _content_len <= _expected_chars * 0.25
                    and attempt < 2
                    and max_tokens < _retry_ceiling
                )
                if _retry_truncation:
                    _bumped = min(max_tokens * 2, _retry_ceiling)
                    logging.warning(
                        f"🔁 [LLM_OSS] Auto-retrying truncated call with "
                        f"max_tokens {max_tokens}→{_bumped} "
                        f"(reasoning ate ~{max_tokens - _content_len // 4} tokens on first attempt)"
                    )
                    kwargs["max_tokens"] = _bumped
                    # Account for the wasted tokens from this attempt before
                    # the retry — the billing system shouldn't lose them.
                    if response.usage:
                        total_input_tokens += response.usage.prompt_tokens or 0
                        total_output_tokens += response.usage.completion_tokens or 0
                        if hasattr(response.usage, 'prompt_tokens_details') and response.usage.prompt_tokens_details:
                            total_cached_tokens += getattr(response.usage.prompt_tokens_details, 'cached_tokens', 0)
                    # Fall through to the retry loop (no time.sleep — this
                    # is a deliberate retry, not a backoff scenario).
                    continue

            # Accumulate final round tokens
            if response.usage:
                total_input_tokens += response.usage.prompt_tokens or 0
                total_output_tokens += response.usage.completion_tokens or 0
                if hasattr(response.usage, 'prompt_tokens_details') and response.usage.prompt_tokens_details:
                    total_cached_tokens += getattr(response.usage.prompt_tokens_details, 'cached_tokens', 0)

            logging.info(f"💰 [LLM_OSS] Usage: {total_input_tokens} in ({total_cached_tokens} cached), {total_output_tokens} out.")

            _track_usage(
                user_id=user_id,
                user_email=user_email,
                model=model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cached_tokens=total_cached_tokens,
                caller="LLM_OSS"
            )

            return answer

        except RateLimitError as e:
            if attempt < 2:
                wait_time = 2 ** (attempt + 1)
                logging.warning(f"⚠️ [LLM_OSS] Rate limited, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logging.error(f"❌ [LLM_OSS] Rate limit exceeded after 3 attempts: {e}")
                raise

        except (APIError, APITimeoutError, APIConnectionError) as e:
            if attempt < 2 and (hasattr(e, 'status_code') and e.status_code in (503, 502, 500)):
                wait_time = 2 ** (attempt + 1)
                logging.warning(f"⚠️ [LLM_OSS] Server error, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logging.error(f"❌ [LLM_OSS] API error after retries: {e}")
                raise

        except Exception as e:
            logger.error(f"❌ [LLM_OSS] Error: {e}")
            raise

    return ""


def llm_call_streaming(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    user_id: str = None,
    user_email: str = None,
    max_tokens: int = 16000,
    temperature: float = 0.7,
    top_p: float = None,
    json_mode: bool = False,
    tier: Tier = DEFAULT_TIER,
) -> Generator[str, None, None]:
    """
    Execute a streaming LLM chat completion call.

    Yields text chunks as they are generated. Tracks usage at the end.

    Args:
        system_prompt: The system instruction.
        user_prompt: The user query + context.
        model: Model ID (from LLM_MODEL env var).
        user_id: User ID for billing.
        user_email: User Email for billing.
        max_tokens: Maximum tokens to generate (default: 20000).
        temperature: Sampling temperature (default: 0.7).
        top_p: Nucleus sampling (default: None, API default).
        json_mode: If True, request JSON output format.

    Yields:
        Text chunks as they are generated.
    """
    tier = coerce_tier(tier)
    model = _resolve_model(model, tier)
    _check_credits(user_id, "LLM_OSS_STREAM")

    # Truncate user_prompt so total input stays within tier's input cap
    _input_cap = _resolve_input_cap(tier)
    sys_tokens = count_tokens(system_prompt or "")
    user_budget = _input_cap - sys_tokens
    user_prompt = truncate_to_token_limit(user_prompt, user_budget, label="user_prompt")

    # Hard cap: enforce API tier output token limit (per-tier)
    _output_cap = _resolve_output_cap(tier)
    if max_tokens > _output_cap:
        logger.info(f"📏 [HARD_CAP] Capping max_tokens from {max_tokens:,} → {_output_cap:,} (tier={tier} API limit)")
        max_tokens = _output_cap

    # Cap max_tokens so prompt + output fits within model context window
    MODEL_CONTEXT_WINDOW = int(os.getenv("MODEL_CONTEXT_WINDOW", 202752))
    actual_input_tokens = sys_tokens + count_tokens(user_prompt or "")
    max_allowed_output = MODEL_CONTEXT_WINDOW - actual_input_tokens - 1000  # 1K safety buffer
    if max_tokens > max_allowed_output > 0:
        logger.info(f"📏 [TOKEN_CAP] Capping max_tokens from {max_tokens:,} → {max_allowed_output:,} "
                     f"(input={actual_input_tokens:,}, context_window={MODEL_CONTEXT_WINDOW:,})")
        max_tokens = max_allowed_output

    # Lift a low per-call cap to the model's real ceiling so reasoning tokens
    # can't truncate the visible output (see _lift_to_ceiling). Cost-neutral.
    max_tokens = _lift_to_ceiling(max_tokens, _output_cap, max_allowed_output)

    client = _get_client(tier)
    messages = _build_messages(system_prompt, user_prompt)

    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    _extra = get_llm_extra_body(model, tier=tier)
    if _extra:
        kwargs["extra_body"] = _extra

    if top_p is not None:
        kwargs["top_p"] = top_p

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    total_input_tokens = 0
    total_output_tokens = 0

    for attempt in range(3):
        try:
            logging.info(f"🚀 [LLM_OSS_STREAM] Streaming {model} for user {user_id} (attempt {attempt + 1})")

            stream = client.chat.completions.create(**kwargs)

            for chunk in stream:
                # Usage comes in the final chunk
                if chunk.usage:
                    total_input_tokens = chunk.usage.prompt_tokens or 0
                    total_output_tokens = chunk.usage.completion_tokens or 0

                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

            # Track usage after streaming completes
            logging.info(f"💰 [LLM_OSS_STREAM] Usage: {total_input_tokens} in, {total_output_tokens} out.")
            _track_usage(
                user_id=user_id,
                user_email=user_email,
                model=model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                caller="LLM_OSS_STREAM"
            )

            return  # Generator done — exit retry loop

        except RateLimitError as e:
            if attempt < 2:
                wait_time = 2 ** (attempt + 1)
                logging.warning(f"⚠️ [LLM_OSS_STREAM] Rate limited, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logging.error(f"❌ [LLM_OSS_STREAM] Rate limit exceeded after 3 attempts: {e}")
                raise

        except (APIError, APITimeoutError, APIConnectionError) as e:
            if attempt < 2 and (hasattr(e, 'status_code') and e.status_code in (503, 502, 500)):
                wait_time = 2 ** (attempt + 1)
                logging.warning(f"⚠️ [LLM_OSS_STREAM] Server error, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                logging.error(f"❌ [LLM_OSS_STREAM] API error after retries: {e}")
                raise

        except Exception as e:
            logger.error(f"❌ [LLM_OSS_STREAM] Error: {e}")
            raise


def llm_call_with_internet(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    user_id: str = None,
    user_email: str = None,
    max_tokens: int = 16000,
    temperature: float = 0.7,
    tier: Tier = DEFAULT_TIER,
) -> str:
    """
    Execute an LLM call WITH internet search as a function tool.

    The model decides when to search the internet via the internet_search tool.
    Internet search is powered by the Citra Internet Data Service.

    Args:
        system_prompt: The system instruction.
        user_prompt: The user query describing what data to fetch.
        model: Model ID (from LLM_MODEL env var).
        user_id: User ID for billing.
        user_email: User Email for billing.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature.

    Returns:
        The generated text response with internet-sourced data.
    """
    tier = coerce_tier(tier)
    model = _resolve_model(model, tier)
    from citra_internet_service import get_internet_tool_definition

    internet_tool = get_internet_tool_definition()

    # Add internet search instruction to system prompt
    internet_instruction = (
        "\n\n**🌐 INTERNET SEARCH AVAILABLE**: You have access to an internet_search tool "
        "for current, real-time information. Use internet_search when you need accurate, "
        "up-to-date data that isn't in the provided context. Cite sources where relevant."
    )
    enhanced_system = (system_prompt or "") + internet_instruction

    return llm_call(
        system_prompt=enhanced_system,
        user_prompt=user_prompt,
        model=model,
        user_id=user_id,
        user_email=user_email,
        max_tokens=max_tokens,
        temperature=temperature,
        tools=[internet_tool],
        tool_choice="auto",
        tier=tier,
    )
