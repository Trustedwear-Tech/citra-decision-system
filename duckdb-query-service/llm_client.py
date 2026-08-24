# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
llm_client.py

Unified LLM client factory for DuckDB Query Service.

All models are called as external hosted endpoints using the OpenAI SDK
(AsyncOpenAI).  Any endpoint that speaks the OpenAI chat-completions format
works — commercial API, self-hosted vLLM / LocalAI, or anything else.

API key is OPTIONAL.  Keyless self-hosted endpoints work fine with
LLM_API_KEY left blank.

  LLM_BASE_URL   endpoint base URL          e.g. http://10.0.0.5:8000/v1
  LLM_API_KEY    API key (optional)          e.g. sk-...
  LLM_MODEL      model name                  e.g. llama-3-70b

  Named-provider shortcut:
  LLM_PROVIDER   openai | deepseek | llm | groq | together |
                 ollama | mistral
"""

import logging
import os
from typing import Optional, Tuple

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider Registry — OpenAI-compatible REST API base URLs.
# Used as shortcuts when LLM_PROVIDER is set. LLM_MODEL must always be
# configured via .env — no default model names are assumed.
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: dict = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "LLM_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "LLM_API_KEY",
    },
    "llm": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "LLM_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "LLM_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "LLM_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "LLM_API_KEY",
    },
}

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_async_client: Optional[AsyncOpenAI] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """
    Build an AsyncOpenAI client.
    api_key is optional — keyless self-hosted endpoints pass a placeholder
    so the SDK constructor doesn't raise.
    """
    return AsyncOpenAI(api_key=api_key or "no-key", base_url=base_url)


def _resolve_llm() -> Tuple[str, str, str]:
    """
    Resolve (base_url, api_key, model) from environment variables.

    Priority:
      1. LLM_BASE_URL + LLM_API_KEY  → direct override
      2. LLM_PROVIDER                → registry lookup
    """
    manual_base_url = os.getenv("LLM_BASE_URL", "").strip()
    manual_api_key = os.getenv("LLM_API_KEY", "").strip()
    manual_model = os.getenv("LLM_MODEL", "").strip()

    # Option 1: Full manual override
    if manual_base_url:
        return manual_base_url, manual_api_key, manual_model or "unknown-model"

    # Option 2: Named provider
    provider_name = os.getenv("LLM_PROVIDER", "").lower().strip()
    if provider_name:
        provider = PROVIDER_REGISTRY.get(provider_name)
        if not provider:
            raise ValueError(
                f"Unknown LLM_PROVIDER '{provider_name}'. "
                f"Options: {list(PROVIDER_REGISTRY.keys())}"
            )
        api_key = manual_api_key or os.getenv(provider["api_key_env"], "").strip()
        base_url = manual_base_url or provider["base_url"]
        model = manual_model or "unknown-model"
        logger.info("[LLM_CLIENT] Provider=%s model=%s", provider_name, model)
        return base_url, api_key, model

    raise ValueError(
        "No LLM provider configured. "
        "Set LLM_BASE_URL + LLM_MODEL (LLM_API_KEY optional for local endpoints), "
        "or set LLM_PROVIDER=<name> + LLM_MODEL."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_default_model() -> str:
    """Return the configured default chat model name."""
    _, _, model = _resolve_llm()
    return model


def get_llm_client() -> AsyncOpenAI:
    """
    Get (or lazily create) the async LLM client singleton.

    Returns:
        openai.AsyncOpenAI pointed at the active provider.
    """
    global _async_client
    if _async_client is None:
        base_url, api_key, _ = _resolve_llm()
        _async_client = _make_client(base_url, api_key)
    return _async_client


def reset_client() -> None:
    """Reset cached singleton. Useful after config changes or in tests."""
    global _async_client
    _async_client = None
    logger.info("[LLM_CLIENT] Client reset.")
