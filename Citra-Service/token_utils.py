# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
token_utils.py

Shared token counting and input truncation utilities for Citra AI.

Constants (all configurable via env vars):
  MAX_INPUT_TOKENS          — maximum tokens allowed in a single LLM input (default: 120000)
  MAX_OUTPUT_TOKENS_DEFAULT — max output tokens for diagram / mindmap / reader / non-chat paths (default: 50000)
  MAX_OUTPUT_TOKENS_CHAT    — max output tokens for main chat streaming (/query/stream) (default: 60000)
  MAX_OUTPUT_TOKENS_CEILING — hard upper bound for per-request overrides on chat (default: 120000)
  MAX_OUTPUT_TOKENS_KG      — max output tokens for knowledge graph build & query (default: 120000)

Used by:
  - llm_oss.py  (llm_call, llm_call_streaming)
  - api/diagram.py, api/entity_extraction.py, api/reader.py
  - graph/query_service.py
  - query.py
"""

import os
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — all overridable via environment variables
# ---------------------------------------------------------------------------

MAX_INPUT_TOKENS: int = int(os.getenv("MAX_INPUT_TOKENS", 190_000))
MAX_OUTPUT_TOKENS_DEFAULT: int = int(os.getenv("MAX_OUTPUT_TOKENS_DEFAULT", 50_000))
MAX_OUTPUT_TOKENS_CHAT: int = int(os.getenv("MAX_OUTPUT_TOKENS_CHAT", 60_000))
MAX_OUTPUT_TOKENS_CEILING: int = int(os.getenv("MAX_OUTPUT_TOKENS_CEILING", 120_000))
MAX_OUTPUT_TOKENS_KG: int = int(os.getenv("MAX_OUTPUT_TOKENS_KG", 120_000))

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(text: str) -> int:
    """
    Estimate the number of tokens in *text*.

    Uses tiktoken (cl100k_base) when available — a reasonable approximation
    for all model families (GPT, DeepSeek, Mistral, LLaMA, Qwen …).
    Falls back to ``len(text) // 4`` if tiktoken is not installed.
    """
    if not text:
        return 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def truncate_to_token_limit(text: str, max_tokens: int, label: str = "") -> str:
    """
    Truncate *text* so that it does not exceed *max_tokens* tokens.

    If truncation occurs a notice is appended to the result and a WARNING is
    logged so operators can tune their prompt budgets.

    Args:
        text:       The input string to possibly truncate.
        max_tokens: Hard token ceiling.
        label:      Optional tag shown in log messages (e.g. "user_prompt").

    Returns:
        The original string if it fits, otherwise a truncated version with
        a ``[content truncated …]`` notice appended.
    """
    if not text or max_tokens <= 0:
        return text

    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        token_ids = enc.encode(text)
        if len(token_ids) <= max_tokens:
            return text
        # Reserve a small buffer for the truncation notice
        notice = f"\n\n[content truncated to {max_tokens:,} token limit]"
        notice_tokens = len(enc.encode(notice))
        truncated = enc.decode(token_ids[: max_tokens - notice_tokens])
        tag = f" [{label}]" if label else ""
        logger.warning(
            f"⚠️ [TOKEN_UTILS]{tag} Input truncated from {len(token_ids):,} → "
            f"{max_tokens:,} tokens."
        )
        return truncated + notice

    except Exception:
        # tiktoken unavailable — fall back to character-based approximation
        char_limit = max_tokens * 4
        if len(text) <= char_limit:
            return text
        notice = f"\n\n[content truncated to ~{max_tokens:,} token limit]"
        tag = f" [{label}]" if label else ""
        logger.warning(
            f"⚠️ [TOKEN_UTILS]{tag} Input truncated (char fallback) from "
            f"{len(text):,} → {char_limit:,} chars."
        )
        return text[:char_limit - len(notice)] + notice
