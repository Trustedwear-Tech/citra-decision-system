# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
llm/llm_tiers.py

Three-tier LLM routing for Citra AI.

Each tier may point at a different provider (different base_url + api_key + model).
Typical setup::

    # large  → your chosen reasoning model  (final answers, hard reasoning)
    LLM_LARGE_BASE_URL=https://openrouter.ai/api/v1
    LLM_LARGE_API_KEY=sk-or-...
    LLM_LARGE_MODEL=<provider/model-slug>     # e.g. deepseek/deepseek-chat

    # medium → GLM-4.7                   (planning, structured generation)
    LLM_MEDIUM_BASE_URL=https://openrouter.ai/api/v1
    LLM_MEDIUM_API_KEY=sk-or-...
    LLM_MEDIUM_MODEL=z-ai/glm-4.7

    # small  → GLM-4.7-Flash             (titles, transcript cleanup, intent)
    LLM_SMALL_BASE_URL=https://openrouter.ai/api/v1
    LLM_SMALL_API_KEY=sk-or-...
    LLM_SMALL_MODEL=z-ai/glm-4.7-flash

Backward compatibility: if a tier's variables are not set, the legacy
``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL`` / ``LLM_EXTRA_BODY`` env vars
are used instead. This means existing single-provider deployments continue to
work without changes — every tier resolves to the legacy config until tier-
specific env vars are added.
"""

from __future__ import annotations

import logging
import os
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

Tier = Literal["small", "medium", "large"]
VALID_TIERS: tuple[Tier, ...] = ("small", "medium", "large")
DEFAULT_TIER: Tier = "large"


class TierConfig(TypedDict):
    tier: Tier
    base_url: str
    api_key: str
    model: str
    extra_body_raw: str  # raw JSON string from env (parsed downstream)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def get_tier_config(tier: Tier) -> TierConfig:
    """
    Resolve env-driven config for the given tier with fallback to legacy
    ``LLM_*`` keys. Raises ``ValueError`` if neither tier-specific nor legacy
    base_url/model are set.
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier {tier!r}; expected one of {VALID_TIERS}")

    prefix = f"LLM_{tier.upper()}_"
    base_url = _env(prefix + "BASE_URL") or _env("LLM_BASE_URL")
    api_key = _env(prefix + "API_KEY") or _env("LLM_API_KEY")
    model = _env(prefix + "MODEL") or _env("LLM_MODEL")
    extra_body_raw = _env(prefix + "EXTRA_BODY") or _env("LLM_EXTRA_BODY")

    if not base_url:
        raise ValueError(
            f"Neither {prefix}BASE_URL nor LLM_BASE_URL is set in env "
            f"(needed for tier={tier!r})"
        )
    if not model:
        raise ValueError(
            f"Neither {prefix}MODEL nor LLM_MODEL is set in env "
            f"(needed for tier={tier!r})"
        )

    return TierConfig(
        tier=tier,
        base_url=base_url,
        api_key=api_key,
        model=model,
        extra_body_raw=extra_body_raw,
    )


def coerce_tier(value: object, default: Tier = DEFAULT_TIER) -> Tier:
    """
    Normalise an arbitrary value to a valid Tier. Falls back to ``default``
    (with a debug log) for unknown values so callers can pass through user
    input without try/except.
    """
    if isinstance(value, str):
        v = value.strip().lower()
        if v in VALID_TIERS:
            return v  # type: ignore[return-value]
    if value is not None:
        logger.debug("[LLM_TIERS] Unknown tier %r — defaulting to %s", value, default)
    return default
