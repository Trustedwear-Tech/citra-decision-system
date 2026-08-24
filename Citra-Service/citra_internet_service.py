# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
citra_internet_service.py

Internet search capability via multiple providers:
  - grok      — xAI Responses API with native ``web_search`` tool (default)
  - perplexity — Perplexity Sonar models (online search is built-in)
  - openai    — OpenAI Responses API with ``web_search`` tool
  - google    — Google Gemini with ``google_search`` grounding tool

The provider is selected via the ``SEARCH_PROVIDER`` env var.  Each provider
uses its own API contract, so the execution logic branches per provider.

Env vars:
  SEARCH_PROVIDER  — grok (default) | perplexity | openai | google | any registry provider
  SEARCH_API_KEY   — override API key
  SEARCH_BASE_URL  — override base URL
  SEARCH_MODEL     — override model (provider-specific default when omitted)

Provides:
- get_internet_tool_definition(): OpenAI function tool schema for internet search
- execute_internet_search(): Execute search and return results
"""

import os
import re
import json
import logging
import time
from typing import Optional, Dict, Any

from citra_llm import get_search_client, get_search_model

logger = logging.getLogger(__name__)

# Billing
try:
    from middleware.credit_check_middleware import track_query_usage, get_usage_service
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False

# Default model resolved from env / provider registry
INTERNET_MODEL = get_search_model()

# Which search provider to use
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "grok").lower().strip()

# Default search country (ISO 3166-1 alpha-2)
SEARCH_COUNTRY = os.getenv("SEARCH_COUNTRY", "IN")


def get_internet_tool_definition() -> Dict[str, Any]:
    """
    Returns the OpenAI function tool definition for internet search.

    This is registered as a tool with llm_call_with_internet() so the
    model can decide when to search the internet.

    Returns:
        OpenAI function tool definition dict.
    """
    return {
        "type": "function",
        "function": {
            "name": "internet_search",
            "description": (
                "Search the internet for current, real-time information. "
                "Use this when you need up-to-date data, news, prices, facts, "
                "or any information that may have changed recently. "
                "IMPORTANT: Call this tool ONLY ONCE per user request. "
                "Combine all aspects of the user's question into a single comprehensive query — "
                "do NOT call this tool multiple times for different sub-questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A single comprehensive search query covering all relevant aspects of the user's question."
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context about what information is needed and why."
                    }
                },
                "required": ["query"]
            }
        }
    }


def execute_internet_search(
    query: str,
    context: str = "",
    user_id: str = None,
    user_email: str = None,
    model: str = None,
    max_tokens: int = 8000,
    temperature: float = 0.3
) -> str:
    """
    Execute an internet search using the configured provider.

    Provider is chosen via ``SEARCH_PROVIDER`` env var:
      - **grok** (default) — xAI Responses API with ``web_search`` tool
      - **openai** — OpenAI Responses API with ``web_search`` tool
      - **perplexity** — Perplexity Chat Completions (search is built-in)
      - **google** — Google Gemini with ``google_search`` grounding tool

    Args:
        query: The search query.
        context: Additional context for the search.
        user_id: User ID for billing.
        user_email: User email for billing.
        model: Model override (provider-specific default when omitted).
        max_tokens: Maximum tokens for the response.
        temperature: Sampling temperature.

    Returns:
        Text response with internet-sourced data.
    """
    effective_model = model or INTERNET_MODEL

    try:
        client = get_search_client()
    except ValueError as e:
        logger.error(f"❌ [INTERNET_SERVICE] Search client not configured: {e}")
        return f"Internet search unavailable: {e}"

    user_prompt = f"Search Query: {query}"
    if context:
        user_prompt += f"\n\nAdditional Context: {context}"

    try:
        logging.info(
            f"🌐 [INTERNET_SERVICE] Searching: '{query}' for user {user_id} "
            f"via provider={SEARCH_PROVIDER} model={effective_model}"
        )

        if SEARCH_PROVIDER == "perplexity":
            answer, usage_data = _search_via_perplexity(client, user_prompt, effective_model, max_tokens, temperature)
        elif SEARCH_PROVIDER == "google":
            answer, usage_data = _search_via_google(client, user_prompt, effective_model, max_tokens, temperature)
        else:
            # grok, openai, and any Responses-API-compatible provider
            answer, usage_data = _search_via_responses_api(client, user_prompt, effective_model, max_tokens, temperature)

        if not answer.strip():
            return "No results found for the search query."

        # Track billing
        _track_billing(usage_data, effective_model, user_id, user_email)

        logging.info(f"✅ [INTERNET_SERVICE] Search complete for '{query}'")
        return answer

    except Exception as e:
        logger.error(f"❌ [INTERNET_SERVICE] Search failed: {e}")
        return f"Internet search failed: {str(e)}"


# ---------------------------------------------------------------------------
# Provider-specific search implementations
# ---------------------------------------------------------------------------

def _search_via_responses_api(client, user_prompt: str, model: str, max_tokens: int, temperature: float):
    """
    Grok / OpenAI — Responses API with native ``web_search`` tool.
    Both xAI and OpenAI share the same ``client.responses.create`` contract.
    """
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": user_prompt}],
        tools=[
            {
                "type": "web_search",
                "user_location": {
                    "type": "approximate",
                    "country": SEARCH_COUNTRY,
                },
            },
        ],
        max_output_tokens=max_tokens,
        temperature=temperature,
    )

    answer = ""
    for item in response.output:
        if hasattr(item, 'type') and item.type == 'message':
            for content_block in getattr(item, 'content', []):
                if hasattr(content_block, 'text'):
                    answer += content_block.text

    usage = response.usage
    return answer, {
        "input_tokens": getattr(usage, 'input_tokens', 0) or 0,
        "output_tokens": getattr(usage, 'output_tokens', 0) or 0,
    }


def _search_via_perplexity(client, user_prompt: str, model: str, max_tokens: int, temperature: float):
    """
    Perplexity — standard Chat Completions API.
    Sonar-online models automatically perform internet search; no extra
    ``tools`` parameter is needed.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )

    answer = response.choices[0].message.content if response.choices else ""

    usage = response.usage
    return answer, {
        "input_tokens": getattr(usage, 'prompt_tokens', 0) or 0,
        "output_tokens": getattr(usage, 'completion_tokens', 0) or 0,
    }


def _search_via_google(client, user_prompt: str, model: str, max_tokens: int, temperature: float):
    """
    Google Gemini — Chat Completions API with ``google_search`` grounding tool.
    When accessed through the OpenAI-compatible endpoint
    (``https://generativelanguage.googleapis.com/v1beta/openai/``) Gemini
    accepts the ``tools`` parameter for grounding.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
        tools=[{"type": "function", "function": {"name": "google_search"}}],
    )

    answer = response.choices[0].message.content if response.choices else ""

    usage = response.usage
    return answer, {
        "input_tokens": getattr(usage, 'prompt_tokens', 0) or 0,
        "output_tokens": getattr(usage, 'completion_tokens', 0) or 0,
    }


# ---------------------------------------------------------------------------
# Billing helper
# ---------------------------------------------------------------------------

def _track_billing(usage_data: dict, model: str, user_id: str, user_email: str):
    """Track token usage and internet grounding cost if billing is available."""
    if not BILLING_AVAILABLE or not user_id or not usage_data:
        return

    input_tokens = usage_data.get("input_tokens", 0)
    output_tokens = usage_data.get("output_tokens", 0)

    internet_grounding_cost = 1.0
    try:
        usage_service = get_usage_service()
        pricing = usage_service.get_pricing()
        internet_grounding_cost = (
            pricing.get('token_pricing', {})
            .get('default', {})
            .get('internet_grounding_price', 1.0)
        )
    except Exception:
        pass

    try:
        track_query_usage(
            user_id=user_id,
            email=user_email or user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
            internet_grounding_cost=internet_grounding_cost
        )
        logging.info(
            f"✅ [INTERNET_SERVICE] Usage tracked: {input_tokens}in/{output_tokens}out"
        )
    except Exception as e:
        logging.error(f"❌ [INTERNET_SERVICE] Failed to track usage: {e}")
