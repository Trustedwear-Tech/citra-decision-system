# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
llm/llm_defaults.py
===================
Fixed-temperature registry for an enterprise RAG product (banks, healthcare,
state government). Default posture is **factual**.

Callers pick a profile by name; they do NOT pass profession or run intent
classification. Layout/creative calls that genuinely need higher temperature
pass the profile explicitly at the call site.

Override globally via env var `LLM_TEMPERATURE_OVERRIDE` (debugging only).
"""

from __future__ import annotations

import os
from typing import Optional

# Profile → temperature.
PROFILE_TEMPERATURES = {
    "factual":          0.2,   # default — Q&A, summarization, RAG answers
    "structured":       0.3,   # JSON / Mermaid / mindmap / entity extraction
    "creative_layout":  0.5,   # presentation / printable layout-only calls
    "creative":         0.6,   # explicit creative writing intents only
}

DEFAULT_PROFILE = "factual"


def temperature_for(profile: Optional[str] = None) -> float:
    """
    Resolve a temperature. If `profile` is None or unknown, returns the
    enterprise default (`factual` = 0.2). Override globally via
    `LLM_TEMPERATURE_OVERRIDE` env var.
    """
    override = os.getenv("LLM_TEMPERATURE_OVERRIDE")
    if override is not None:
        try:
            return float(override)
        except ValueError:
            pass

    return PROFILE_TEMPERATURES.get(
        (profile or DEFAULT_PROFILE).strip().lower(),
        PROFILE_TEMPERATURES[DEFAULT_PROFILE],
    )
