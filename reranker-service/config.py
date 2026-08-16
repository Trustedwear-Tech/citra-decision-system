# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Configuration for the Reranker Service.
"""
import os

# Local dev (`python main.py`) reads reranker-service/.env; cloud injects env
# via Vault so an absent .env is fine — load_dotenv is a no-op then.
from dotenv import load_dotenv
load_dotenv()

# Provider: "local" = in-process cross-encoder (default), "remote" = external
# rerank API (Cohere-style shape: query + documents + top_n → results with
# index + relevance_score; served identically by Fireworks and OpenRouter).
# No cross-provider fallback — if the configured provider fails, /rerank fails
# loud (RULE #1).
RERANKER_PROVIDER = os.getenv("RERANKER_PROVIDER", "local").strip().lower()

# --- local provider ---
# Model selected automatically based on device (GPU → heavy model, CPU → lightweight)
RERANKER_MODEL_GPU = os.getenv("RERANKER_MODEL_GPU", "BAAI/bge-reranker-v2-m3")
RERANKER_MODEL_CPU = os.getenv("RERANKER_MODEL_CPU", "cross-encoder/ms-marco-MiniLM-L-6-v2")
# Explicit override — if set, always use this model regardless of device
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "")

# --- remote provider ---
# Context limits apply PER query+document pair (cross-encoder) and providers
# truncate oversized pairs themselves — chunks are sent whole, no client-side
# truncation. Qwen3-Reranker-8B: 32K per pair.
RERANK_API_URL = os.getenv("RERANK_API_URL", "")
RERANK_API_KEY = os.getenv("RERANK_API_KEY", "")
RERANK_API_MODEL = os.getenv("RERANK_API_MODEL", "")
RERANK_API_TIMEOUT = float(os.getenv("RERANK_API_TIMEOUT", "30"))

if RERANKER_PROVIDER not in ("local", "remote"):
    raise RuntimeError(
        f"RERANKER_PROVIDER must be 'local' or 'remote', got {RERANKER_PROVIDER!r}"
    )
if RERANKER_PROVIDER == "remote":
    missing = [
        name
        for name, val in (
            ("RERANK_API_URL", RERANK_API_URL),
            ("RERANK_API_KEY", RERANK_API_KEY),
            ("RERANK_API_MODEL", RERANK_API_MODEL),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"RERANKER_PROVIDER=remote requires {', '.join(missing)} to be set"
        )

PORT = int(os.getenv("PORT", "7302"))
HOST = os.getenv("HOST", "0.0.0.0")
