# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Discovery Service — Configuration
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings

# Load .env into os.environ BEFORE the Settings fields evaluate their
# os.getenv() defaults below. pydantic-settings v2 ignores the legacy
# ``class Config: env_file``, so this is the real .env-reading step for
# local dev. override=False → Vault / compose-set env wins. No-op when no
# .env exists.
try:  # pragma: no cover - trivial bootstrap
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _require_env(name: str) -> str:
    """Return env var ``name`` or fail loud (RULE #1).

    Connection strings and secrets get NO fallback default — a missing
    value surfaces at startup instead of silently running against a wrong
    host or a guessable secret.
    """
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. Set it in "
            f".env (local dev) or Vault (prod); refusing to start on a "
            f"fallback default."
        )
    return val


class Settings(BaseSettings):
    # MongoDB — required, no creds-bearing localhost fallback.
    mongo_uri: str = _require_env("MONGO_URI")
    mongo_db: str = os.getenv("MONGO_DB", "citra_discovery")
    tools_collection: str = os.getenv("TOOLS_COLLECTION", "discovery_tools")

    # Auth — required secrets, no guessable ``change-me`` defaults.
    jwt_secret: str = _require_env("JWT_SECRET")
    admin_api_key: str = _require_env("ADMIN_API_KEY")

    # Tool lifecycle
    # Tools whose heartbeat is older than this (seconds) are marked inactive
    heartbeat_timeout_seconds: int = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "120"))

    # Discovery cache TTL (mirrors enterprise_search.py default of 300s)
    discovery_cache_ttl_seconds: int = int(os.getenv("DISCOVERY_CACHE_TTL_SECONDS", "300"))

    # Health-proxy timeout
    health_proxy_timeout_seconds: float = float(os.getenv("HEALTH_PROXY_TIMEOUT_SECONDS", "5"))

    # Tool-description embedder (OpenAI-compatible /v1/embeddings).
    # Used by /tools/search to cosine-rank registered tools against the
    # caller's query. Optional — leaving EMBED_BASE_URL empty disables
    # semantic ranking and /tools/search falls back to substring scoring.
    embed_base_url: str = os.getenv("EMBED_BASE_URL", "")
    embed_api_key: str = os.getenv("EMBED_API_KEY", "")
    embed_model: str = os.getenv("EMBED_MODEL", "")
    # Default top_k used when /tools/search?top_k is not provided.
    search_default_top_k: int = int(os.getenv("DISCOVERY_SEARCH_DEFAULT_TOP_K", "10"))

    # App
    port: int = int(os.getenv("PORT", "9000"))
    environment: str = os.getenv("ENVIRONMENT", "dev")

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
