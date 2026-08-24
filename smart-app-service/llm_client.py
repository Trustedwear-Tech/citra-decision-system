# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Process-singleton AsyncOpenAI client pointed at LLM_LARGE_BASE_URL.

Mirrors the ``llm_client._make_client`` factory in Citra-Service so the
two services configure the same provider (OpenRouter for cloud demos,
the in-house ``inference-service`` for on-prem GPU) with identical
retry / timeout behaviour.

Used by:
- ``runtime.py`` for ``/apps/{slug}/run`` agent execution
- ``internal_routes.py`` for the ``/smart-app/internal/llm`` proxy that
  the builder pod calls (so the builder never holds raw OpenRouter
  credentials).
"""

from __future__ import annotations

import threading
from typing import Optional

from fastapi import HTTPException, status
from openai import AsyncOpenAI

from config import Settings


_singleton: Optional[AsyncOpenAI] = None
_lock = threading.Lock()

# Per-endpoint client cache keyed by (base_url, api_key) so each model tier
# (large/medium/small) can point at a different provider/key while reusing
# one pooled AsyncOpenAI (and its connection pool) per distinct endpoint.
_clients: dict = {}
_clients_lock = threading.Lock()


def get_llm_client_for(base_url: str, api_key: str) -> AsyncOpenAI:
    """Pooled AsyncOpenAI for a specific endpoint (tier-aware). Reused across
    calls so we don't open a fresh client (and connection pool) per request."""
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM base_url not configured for the requested tier",
        )
    key = (base_url, api_key or "no-key")
    client = _clients.get(key)
    if client is not None:
        return client
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            client = AsyncOpenAI(base_url=base_url, api_key=api_key or "no-key", max_retries=5)
            _clients[key] = client
    return client


def get_llm_client(settings: Settings) -> AsyncOpenAI:
    """Lazy-init AsyncOpenAI bound to the configured large-LLM endpoint.

    Raises 503 if ``LLM_LARGE_BASE_URL`` is unset — the service cannot
    serve any /run or /internal/llm request without it.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _lock:
        if _singleton is not None:
            return _singleton
        if not settings.llm_large_base_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LLM_LARGE_BASE_URL not configured",
            )
        _singleton = AsyncOpenAI(
            base_url=settings.llm_large_base_url,
            api_key=settings.llm_large_api_key or "no-key",
            max_retries=5,
        )
    return _singleton
