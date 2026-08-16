# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Configuration for DuckDB Query Service
"""
import logging
import os

logger = logging.getLogger(__name__)


class Config:
    # Server settings
    PORT = int(os.getenv("PORT", "7301"))
    HOST = os.getenv("HOST", "0.0.0.0")

    # DuckDB TablePool settings
    TABLE_TTL = int(os.getenv("DUCKDB_TABLE_TTL", "600"))  # seconds idle before eviction
    MAX_TABLES = int(os.getenv("DUCKDB_MAX_TABLES", "500"))  # max loaded tables across all users
    CLEANUP_INTERVAL = int(os.getenv("DUCKDB_CLEANUP_INTERVAL", "60"))  # seconds between cleanup sweeps

    # MongoDB — no dev fallback; must be set via Vault or .env
    MONGODB_URI = os.getenv("MONGODB_CONN_STRING", "")
    MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "")

    # Redis — no dev fallback; must be set via Vault or .env
    REDIS_HOST = os.getenv("REDIS_HOST", "")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
    REDIS_USERNAME = os.getenv("REDIS_USERNAME")
    REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"
    BULK_FETCH_CACHE_TTL = int(os.getenv("SAAS_BULK_FETCH_CACHE_TTL", "300"))

    # LLM provider — see llm_client.py for full resolution logic
    # Set LLM_PROVIDER to switch providers (openai, deepseek, groq, ollama, etc.)
    # Set LLM_MODEL to override the default model for the selected provider
    # Set LLM_BASE_URL + LLM_API_KEY for a fully custom endpoint
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
    LLM_MODEL = os.getenv("LLM_MODEL", "")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")

    # Chart-specific row limits
    CHART_ROW_LIMITS = {
        "pie": 10,
        "doughnut": 10,
        "bar": 50,
        "line": 100,
        "scatter": 100,
        "radar": 12,
        "table": 100,
    }


config = Config()
