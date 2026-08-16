# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
data-discovery-service — Configuration
========================================
Reads environment variables / .env. No secrets in code.
"""

import os
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings

# Load .env into os.environ for local dev. pydantic-settings v2 ignores the
# legacy ``class Config: env_file``, so without this a bare local run never
# reads .env. override=False → Vault / compose-set env wins. No-op when no
# .env exists.
try:  # pragma: no cover - trivial bootstrap
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _require_env(name: str) -> str:
    """Return env var ``name`` or fail loud (RULE #1).

    Connection strings, inter-service URLs and secrets get NO fallback
    default — a missing value must surface at startup, not silently route
    to a wrong host or run on a guessable secret.
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
    # ── Identity ────────────────────────────────────────────────────────────
    service_name: str = "data-discovery-service"
    environment: str = "dev"
    port: int = 8095

    # ── Tenant (single-tenant deployment) ────────────────────────────────────
    # Every MCP deployment is single-tenant. ORG_ID is the org/tenant the
    # BACKGROUND crawler operates as (it mints an org_admin token for this org
    # and files the catalogue under it). Required when CRAWL_ENABLED=true.
    # User-triggered /crawl/run instead uses the caller's tenant from their JWT.
    org_id: str = ""

    # ── Mongo (catalogue store) ─────────────────────────────────────────────
    # Required — no localhost fallback; a missing store connection fails loud.
    mongo_uri: str = _require_env("MONGO_URI")
    mongo_db: str = "citra-ai"
    catalogue_collection: str = "data_catalogue"

    # ── Discovery service (where we look up registered MCP shims) ───────────
    discovery_url: str = _require_env("DISCOVERY_URL")

    # ── Auth ────────────────────────────────────────────────────────────────
    # Required secret — no guessable ``change-me`` default.
    jwt_secret: str = _require_env("JWT_SECRET")
    jwt_issuer: str = "Citra-AI"
    jwt_audience: str = "citra-data-discovery"

    # ── Crawler tuning ──────────────────────────────────────────────────────
    crawl_enabled: bool = False               # opt-in startup crawl (one-shot)
    # How long the crawl leader-lock is held. It exists so two replicas booting
    # together don't crawl the same tenant twice, so it need only outlive ONE
    # pass — the holder releases it as soon as the pass ends, and this is just
    # the backstop for a replica killed mid-crawl.
    #
    # It used to be derived from a `crawl_interval_seconds` (86400) left over
    # from the nightly loop removed on 2026-07-01, giving a ~24h lease. Since a
    # redeploy recreates the container (new HOSTNAME ⇒ new lock holder), that
    # silently turned "restart to re-crawl" — the documented remedy after a
    # schema change — into a no-op for a whole day. Keep this on the scale of a
    # crawl, not a day.
    crawl_lock_ttl_seconds: int = 900
    crawl_per_mcp_timeout_seconds: float = 60.0
    crawl_max_parallel_mcps: int = 8          # asyncio.gather concurrency cap
    sample_rows_for_profile: int = 100        # how many rows the classifier sees
    max_columns_profiled: int = 200           # cap per dataset for safety

    # ── LLM enrichment (semantic name + description for cryptic schemas) ───
    # Same env-var convention as Citra-Service's llm_client.py — the LLM is
    # any OpenAI-compatible endpoint (OpenRouter / vLLM / Azure …).
    # Switch providers by changing LLM_BASE_URL + LLM_API_KEY + LLM_MODEL only.
    llm_enrichment_enabled: bool = True
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek/deepseek-chat-v3.1"
    llm_request_timeout_seconds: float = 60.0
    llm_sample_rows_for_naming: int = 5       # rows sent to LLM (PII-redacted)
    llm_min_cryptic_score: float = 0.5        # only enrich names scoring above this
    # Kinds whose schemas are already business-readable (Salesforce
    # `Account`/`Opportunity`, SAP `A_BusinessPartner`, …). The crawler
    # skips the LLM rename for these regardless of name score. PII
    # classification + sample-row inspection still run.
    llm_enrichment_skip_kinds: List[str] = ["soql", "odata"]

    # ── Catalogue vector index (semantic dataset search for the builder) ────
    # At crawl we embed each dataset's signature into a DEDICATED Milvus
    # collection (separate from RAG document collections); the builder
    # ANN-searches it (recall) and reranks (precision) instead of fetch-all.
    # Disabled by default → fail-open to the plain Mongo /catalogue list.
    catalogue_vector_enabled: bool = False
    milvus_uri: str = ""
    milvus_token: str = ""
    catalogue_milvus_collection: str = "data_catalogue"
    # Embedding endpoint (OpenAI-compatible). dim MUST match the dim the
    # builder/query side embeds with; 768 mirrors the demo Milvus collections.
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 768
    embedding_timeout: float = 30.0

    # ── Inter-service calls ─────────────────────────────────────────────────
    service_api_key: str = ""                 # forwarded as Bearer to dept-mcps
    request_timeout_seconds: float = 30.0

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
