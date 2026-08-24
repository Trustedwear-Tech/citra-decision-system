# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
llm_client.py

Unified LLM client factory for Citra AI.

All configuration is via .env variables â€” no hardcoded provider registry.

â”€â”€ Chat Completion â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  LLM_BASE_URL   base URL of the endpoint          e.g. https://openrouter.ai/api/v1
  LLM_API_KEY    API key (optional for local)       e.g. sk-or-...
  LLM_MODEL      model name to pass in the request  e.g. z-ai/glm-5.1

â”€â”€ Embedding â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  EMBEDDING_BASE_URL   endpoint base URL
  EMBEDDING_API_KEY    API key (optional)
  EMBEDDING_MODEL      model name  default: baai/bge-m3
  EMBEDDING_DIMENSION  default: 768

â”€â”€ Vision / OCR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  VISION_BASE_URL  endpoint base URL
  VISION_API_KEY   API key (optional for local)
  VISION_MODEL     model name  default: qwen/qwen3-vl-32b-instruct

â”€â”€ Internet Search â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  SEARCH_BASE_URL  endpoint base URL
  SEARCH_API_KEY   API key (optional)
  SEARCH_MODEL     model name
  SEARCH_PROVIDER  provider style: grok|openai|perplexity|google (controls which API contract to use)
"""

import logging
import os
from typing import Dict, Optional

from openai import AsyncOpenAI, OpenAI

from citra_llm.llm.llm_tiers import DEFAULT_TIER, Tier, coerce_tier, get_tier_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
# LLM clients are keyed by tier ("small" | "medium" | "large") because
# different tiers may point at different providers / models (e.g. GLM-4.7-Flash vs GLM-5.1)
# with different base_urls and api keys.

_sync_clients: Dict[Tier, OpenAI] = {}
_async_clients: Dict[Tier, AsyncOpenAI] = {}
_embedding_client: Optional[AsyncOpenAI] = None
_vision_client: Optional[OpenAI] = None
_search_client: Optional[OpenAI] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_client(base_url: str, api_key: str, async_: bool = False):
    """
    Build an OpenAI SDK client pointed at base_url.
    api_key is optional â€” self-hosted/keyless endpoints work with a placeholder.
    """
    kwargs = {
        "api_key": api_key or "no-key",
        "base_url": base_url,
        "max_retries": 5,
    }
    return AsyncOpenAI(**kwargs) if async_ else OpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_default_model(tier: Tier = DEFAULT_TIER) -> str:
    """Return the configured LLM model name for the given tier."""
    return get_tier_config(tier)["model"]


def get_llm_client(async_: bool = False, tier: Tier = DEFAULT_TIER):
    """
    Get (or lazily create) the LLM client for the given tier.

    Each tier resolves its own ``LLM_<TIER>_BASE_URL`` / ``LLM_<TIER>_API_KEY``
    env vars (falling back to legacy ``LLM_BASE_URL`` / ``LLM_API_KEY`` when
    a tier-specific value is missing).
    """
    tier = coerce_tier(tier)
    cfg = get_tier_config(tier)
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]

    if async_:
        client = _async_clients.get(tier)
        if client is None:
            client = _make_client(base_url, api_key, async_=True)
            _async_clients[tier] = client
            logger.info("[LLM_CLIENT] Async LLM client (tier=%s) → %s", tier, base_url)
        return client

    client = _sync_clients.get(tier)
    if client is None:
        client = _make_client(base_url, api_key, async_=False)
        _sync_clients[tier] = client
        logger.info("[LLM_CLIENT] Sync LLM client (tier=%s) → %s", tier, base_url)
    return client


def get_llm_model(tier: Tier = DEFAULT_TIER) -> str:
    """Return the configured LLM model name for the given tier."""
    return get_default_model(tier)


def get_llm_extra_body(model: Optional[str] = None, tier: Tier = DEFAULT_TIER) -> dict:
    """
    Return the provider-specific ``extra_body`` payload to merge into every
    chat-completion call.

    Reads from ``LLM_<TIER>_EXTRA_BODY`` (or legacy ``LLM_EXTRA_BODY``). Lets
    you configure provider-specific knobs (OpenRouter ``reasoning`` /
    ``provider`` routing, Anthropic thinking budgets, etc.) without touching
    code. Returns ``{}`` if unset or invalid.

    Model-aware override: when ``model`` is any DeepSeek reasoning model
    (matched by ``"deepseek" in model.lower()`` — works across versions),
    force ``reasoning.effort='low'`` so we get speed-of-execution latency
    by default. Any explicit ``reasoning`` block in the env JSON is
    preserved; the override only fills in missing fields.

    OpenRouter "nitro" routing: when the tier's base_url points at
    OpenRouter (openrouter.ai) and no explicit ``provider`` block is set
    in env extra_body, inject ``provider.sort = "throughput"``. This is
    OpenRouter's documented way to route to the fastest available
    upstream provider for the chosen model (equivalent to appending
    ``:nitro`` to the model slug). Cuts cold-path latency materially for
    long generations on slow upstreams without forcing the model-slug
    suffix everywhere. Override per-tier by setting
    ``LLM_<TIER>_EXTRA_BODY`` with an explicit ``provider`` block.
    """
    tier = coerce_tier(tier)
    try:
        tier_config = get_tier_config(tier)
        raw = tier_config["extra_body_raw"]
        base_url = tier_config["base_url"]
    except ValueError:
        raw = ""
        base_url = ""

    body: dict = {}
    if raw:
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                body = parsed
            else:
                logger.warning("[LLM_CLIENT] LLM_EXTRA_BODY (tier=%s) is not a JSON object; ignoring", tier)
        except Exception as e:
            logger.warning("[LLM_CLIENT] Failed to parse LLM_EXTRA_BODY (tier=%s): %s", tier, e)

    # Resolve model if not given so callers that don't pass it still benefit.
    if not model:
        try:
            model = get_tier_config(tier)["model"]
        except ValueError:
            model = ""
    effective_model = (model or "").lower()
    # Force LOW reasoning effort by default for known reasoning-mode models.
    # Reasoning is kept ENABLED (load-bearing for JSON quality) but capped at
    # the lowest tier so it doesn't run away with the output budget. Surfaces
    # that need deeper deliberation (action-chat, smart-app) can override by
    # setting `reasoning.effort=medium` explicitly in LLM_<TIER>_EXTRA_BODY
    # or by passing `reasoning_effort=` to the call. We never disable
    # reasoning here — the retry path in llm_oss.py also preserves whatever
    # effort the caller chose; it only bumps max_tokens on truncation.
    _is_reasoning_model = (
        "deepseek" in effective_model
        or "z-ai/glm" in effective_model         # GLM-4.5+ / 4.6 / 5.1 — hybrid reasoning
        or "zai-org/glm" in effective_model      # alternate slug
        or "qwen3" in effective_model            # Qwen3 thinking models
        or "qwq" in effective_model              # QwQ reasoning
        or effective_model.startswith("openai/o") # o1 / o3 / o-series
        or effective_model.startswith("openai/gpt-5")
    )
    if _is_reasoning_model:
        existing = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
        merged = {"effort": "low", "exclude": False, **existing}
        body["reasoning"] = merged
        logger.debug(
            "[LLM_CLIENT] Reasoning override: reasoning.effort=%s (model=%s, tier=%s)",
            merged.get("effort"), effective_model, tier,
        )

    # Nitro routing on OpenRouter — only inject if the user hasn't already
    # configured a `provider` block in env extra_body (env wins).
    if "openrouter.ai" in (base_url or "").lower() and not isinstance(body.get("provider"), dict):
        body["provider"] = {"sort": "throughput"}
        logger.debug(
            "[LLM_CLIENT] OpenRouter nitro override: provider.sort=throughput (model=%s, tier=%s)",
            effective_model, tier,
        )

    return body


def get_embedding_client() -> AsyncOpenAI:
    """
    Get (or lazily create) the embedding client.
    Reads EMBEDDING_BASE_URL and EMBEDDING_API_KEY from .env.
    """
    global _embedding_client

    if _embedding_client is None:
        base_url = os.getenv("EMBEDDING_BASE_URL", "").strip()
        api_key  = os.getenv("EMBEDDING_API_KEY",  "").strip()

        if not base_url:
            raise ValueError("EMBEDDING_BASE_URL is not set in .env")

        _embedding_client = AsyncOpenAI(api_key=api_key or "no-key", base_url=base_url, max_retries=5)
        logger.info("[LLM_CLIENT] Embedding client â†’ %s", base_url)

    return _embedding_client


def get_embedding_model() -> str:
    """Return the configured embedding model name."""
    return os.getenv("EMBEDDING_MODEL", "baai/bge-m3")


def get_embedding_dimension() -> int:
    """Return the configured embedding dimension."""
    return int(os.getenv("EMBEDDING_DIMENSION", "768"))


def get_vision_client() -> OpenAI:
    """
    Get (or lazily create) the vision/OCR client.
    Reads VISION_BASE_URL and VISION_API_KEY from .env.
    """
    global _vision_client

    if _vision_client is None:
        base_url = os.getenv("VISION_BASE_URL", "").strip()
        api_key  = os.getenv("VISION_API_KEY",  "").strip()

        if not base_url:
            raise ValueError("VISION_BASE_URL is not set in .env")

        _vision_client = _make_client(base_url, api_key)
        logger.info("[LLM_CLIENT] Vision client â†’ %s", base_url)

    return _vision_client


def get_vision_model() -> str:
    """Return the configured vision/OCR model name."""
    return os.getenv("VISION_MODEL", "qwen/qwen3-vl-32b-instruct")


def get_vision_extra_body() -> dict:
    """
    Return provider-specific ``extra_body`` for the vision client.

    Reads ``VISION_EXTRA_BODY`` JSON if set. Additionally, when the vision
    client points at OpenRouter, inject ``provider.sort = "throughput"``
    (the "nitro" routing knob — picks the fastest available upstream
    provider) unless the user has set their own ``provider`` block. Vision
    calls are the dominant per-slide latency on the critique loop; nitro
    routing typically cuts wall time by ~30-50%.
    """
    body: dict = {}
    raw = os.getenv("VISION_EXTRA_BODY", "").strip()
    if raw:
        try:
            import json as _json
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                body = parsed
            else:
                logger.warning("[LLM_CLIENT] VISION_EXTRA_BODY is not a JSON object; ignoring")
        except Exception as e:  # noqa: BLE001
            logger.warning("[LLM_CLIENT] Failed to parse VISION_EXTRA_BODY: %s", e)

    base_url = os.getenv("VISION_BASE_URL", "").strip().lower()
    if "openrouter.ai" in base_url and not isinstance(body.get("provider"), dict):
        body["provider"] = {"sort": "throughput"}
        logger.debug("[LLM_CLIENT] Vision OpenRouter nitro override: provider.sort=throughput")

    return body


def get_search_client() -> OpenAI:
    """
    Get (or lazily create) the internet-search client.
    Reads SEARCH_BASE_URL and SEARCH_API_KEY from .env.
    """
    global _search_client

    if _search_client is None:
        base_url = os.getenv("SEARCH_BASE_URL", "").strip()
        api_key  = os.getenv("SEARCH_API_KEY",  "").strip()

        if not base_url:
            raise ValueError(
                "SEARCH_BASE_URL is not set in .env. "
                "Example: SEARCH_BASE_URL=https://api.x.ai/v1"
            )

        _search_client = _make_client(base_url, api_key)
        logger.info("[LLM_CLIENT] Search client â†’ %s", base_url)

    return _search_client


def get_search_model() -> str:
    """Return the configured internet-search model name from SEARCH_MODEL env var."""
    model = os.getenv("SEARCH_MODEL", "").strip()
    if not model:
        raise ValueError("SEARCH_MODEL is not set in .env")
    return model


def reset_clients() -> None:
    """Reset all cached singletons. Useful after config changes or in tests."""
    global _embedding_client, _vision_client, _search_client
    _sync_clients.clear()
    _async_clients.clear()
    _embedding_client = None
    _vision_client = None
    _search_client = None
    logger.info("[LLM_CLIENT] All clients reset.")

