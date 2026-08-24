# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra-mcp-service configuration.

Env-driven. The service is intentionally stateless and standalone — it
holds its own Serper credentials and reaches the downstream providers
directly. No dependency on action-chat-service or smart-app-service at
runtime; both of them simply inject CITRA_MCP_URL into their sandbox
spawns, pointing here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MCPConfig:
    # ---- service identity --------------------------------------------
    listen_host: str = os.getenv("CITRA_MCP_LISTEN_HOST", "0.0.0.0")
    listen_port: int = int(os.getenv("CITRA_MCP_LISTEN_PORT", "9090"))
    environment: str = (os.getenv("ENVIRONMENT") or "dev").lower()

    # ---- auth --------------------------------------------------------
    # Shared JWT secret. Both action-chat-service and smart-app-service
    # mint scoped tokens with this secret; the MCP service validates
    # any token signed with it and accepts the scopes listed below.
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_issuer: str = os.getenv("JWT_ISSUER", "Citra-AI")
    # Comma-separated allowlist of accepted ``scope`` claims.
    #
    # ``smart-app-builder`` (minted by smart-app-service) is the ONLY scope
    # anything mints today — the builder pod is the sole MCP caller.
    #
    # ``action-sandbox`` is retained but DEAD: it was minted by
    # action-chat-service, which has been removed from this repo. It stays in
    # the default only so the eight tools still gated to it keep their
    # gate meaningful rather than silently becoming open-to-all. See the
    # "DEAD TOOL SURFACE" block in tools/registry.py; when those tools are
    # deleted, drop this scope with them.
    accepted_scopes: tuple[str, ...] = tuple(
        s.strip() for s in (
            os.getenv(
                "CITRA_MCP_ACCEPTED_SCOPES",
                "action-sandbox,smart-app-builder",
            ) or ""
        ).split(",")
        if s.strip()
    )

    # ---- web search (Serper) -----------------------------------------
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    serper_api_url: str = os.getenv(
        "SERPER_API_URL", "https://google.serper.dev/search"
    )

    # ---- web fetch ---------------------------------------------------
    # Defaults match what action-chat-service uses for its own /web-fetch
    # so behaviour is consistent across the two access paths during the
    # migration.
    web_fetch_max_bytes_default: int = int(
        os.getenv("CITRA_MCP_WEB_FETCH_MAX_BYTES", str(2 * 1024 * 1024))
    )
    web_fetch_max_bytes_hard: int = int(
        os.getenv("CITRA_MCP_WEB_FETCH_MAX_BYTES_HARD", str(16 * 1024 * 1024))
    )

    # ---- downstream Citra services -----------------------------------
    # All optional; a tool that requires its downstream returns a 500-class
    # tool error at call time rather than crashing the service at boot.
    reranker_service_url: str = os.getenv("RERANKER_SERVICE_URL", "")
    duckdb_service_url: str = os.getenv("DUCKDB_SERVICE_URL", "")
    discovery_service_url: str = os.getenv("DISCOVERY_SERVICE_URL", "")
    # playwright-render-service — headless Chromium screenshots (PNG). Used by
    # the citra_visual_review tool to capture a rendered app page before the
    # vision model critiques it. Config only (env in dev, Vault in prod) — no
    # browser ever runs inside this service or the agent sandbox.
    render_service_url: str = os.getenv("RENDER_SERVICE_URL", "")
    # Render-URL rewrite for citra_visual_review: the public app host
    # (apps.citra-ai.com) resolves to Cloudflare — from the render container
    # that's IPv6-only + proxied, and the screenshot fails as "DNS/unreachable"
    # even though the app is fine. When BOTH values are set, a review URL whose
    # host equals APP_PUBLIC_HOST is rewritten to APP_RUNTIME_INTERNAL_URL
    # (scheme+host only; path/query/token preserved) so Chromium loads the
    # runtime over the shared docker network. Unset ⇒ URLs pass through as-is.
    app_public_host: str = os.getenv("APP_PUBLIC_HOST", "")
    app_runtime_internal_url: str = os.getenv("APP_RUNTIME_INTERNAL_URL", "")
    # smart-app-service base URL — used by the citra_spec_validate tool to run
    # the authoritative AppSpec/AgentSpec validation (JSON Schema + Pydantic
    # cross-references) where the spec models actually live. The builder pod
    # cannot import those models, so this MCP tool proxies the validation to
    # smart-app-service's POST /builder/validate, forwarding the caller's token.
    # Optional at boot; citra_spec_validate fails loud at call time if unset.
    smart_app_service_url: str = os.getenv("SMART_APP_SERVICE_URL", "")
    # Citra-Service base URL — RAG short-circuit: a `kind=semantic` source is
    # answered by the Citra-Service platform reader (POST /semantic/search, Milvus
    # direct), NEVER the dept-MCP (which serves no RAG). citra_discovery_query
    # routes semantic sources here; structured sources still go to the dept-MCP
    # query_endpoint. Optional at boot; the semantic branch fails loud if unset.
    citra_service_url: str = os.getenv("CITRA_SERVICE_URL", "")
    # Shared dept-MCP service API key. A dept-MCP gates its data plane on
    # TWO things: Authorization: Bearer <service key> (service-to-service
    # auth) AND X-User-JWT (the end-user identity for visibility). The
    # citra_discovery_query tool must therefore present the service key as
    # Authorization and forward the caller's token as X-User-JWT — not the
    # other way round. Same key Citra-Service / smart-app-service use.
    mcp_service_api_key: str = os.getenv("MCP_SERVICE_API_KEY", "")

    # ---- user-data backend ------------------------------------------
    # User-scoped tools (vault / files / ocr / llm / sql_duckdb) forward
    # to ONE backend that owns the user-data layer. Today that's
    # action-chat-service's /actionchat/internal/* routes; tomorrow it
    # could be a dedicated citra-user-data-service. Either way, scoping
    # happens by user_id extracted from the JWT — the scope claim is
    # auth-only ("is this a legitimate sandbox?"), not a routing key.
    #
    # Any sandbox (action-chat, smart-app, future) calls the same MCP
    # service and reaches the same user data. A file the user uploaded
    # in action-chat is visible from smart-app and vice versa.
    # smart-app-service's internal API root, e.g.
    # ``http://smart-app-service:9100/smart-app/internal``. Used by
    # citra_visual_review for the vision-critique step. Authenticated with the
    # caller-relayed internal bearer (X-Smart-App-Internal), never with a
    # credential this service holds.
    smart_app_internal_url: str = os.getenv("SMART_APP_INTERNAL_URL", "").strip()

    # DEPRECATED — only the dead tool surface reads this (see the DEAD TOOL
    # SURFACE block in tools/registry.py). It pointed at action-chat-service,
    # which no longer exists; delete it with those tools.
    user_data_backend_url: str = (
        os.getenv("CITRA_USER_DATA_BACKEND_URL", "")
        or os.getenv("ACTIONCHAT_INTERNAL_URL", "")
        or os.getenv("ACTIONCHAT_SERVICE_INTERNAL_URL", "")
    )

    # ---- embeddings (any OpenAI-compatible /v1/embeddings endpoint) --
    embed_base_url: str = os.getenv("EMBED_BASE_URL", "")
    embed_api_key: str = os.getenv("EMBED_API_KEY", "")
    embed_model: str = os.getenv("EMBED_MODEL", "")

    # ---- image generation (Runware-compatible) -----------------------
    # Same env var names as Citra-Service / action-chat-service so a
    # single .env line works everywhere.
    image_gen_provider: str = (os.getenv("IMAGE_GEN_PROVIDER") or "runware").strip().lower()
    image_gen_api_key: str = os.getenv("IMAGE_GEN_API_KEY", "")
    image_gen_model: str = os.getenv("IMAGE_GEN_MODEL", "runware:400@1")
    image_gen_base_url: str = os.getenv("IMAGE_GEN_BASE_URL", "")
    image_gen_timeout_seconds: int = int(os.getenv("IMAGE_GEN_TIMEOUT", "60"))


_config: MCPConfig | None = None


def get_config() -> MCPConfig:
    global _config
    if _config is None:
        _config = MCPConfig()
    return _config
