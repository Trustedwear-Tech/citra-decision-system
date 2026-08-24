# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Runtime configuration for the Citra Action Sandbox.

Everything here is read from env vars so the image stays immutable and the
same build works across SaaS / on-prem / air-gapped deployments.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxConfig:
    # --- Identity ---
    user_id: str
    session_id: str
    org_id: str | None
    # The JWT that Citra-Service minted specifically for this sandbox.
    # Scope: {discovery:read, mcp:call, milvus:user-collection}. Short TTL.
    scoped_token: str

    # --- Upstream services ---
    discovery_service_url: str | None
    milvus_proxy_url: str | None      # Citra-Service endpoint that proxies Milvus
    artifact_url: str | None          # Citra-Service endpoint for artifact upload
    proxy_base_url: str | None        # CITRA_AGENT_PROXY_BASE_URL — used by the
                                      # adapter to call action-chat-service
                                      # (scratch.workspace, files.delete) for
                                      # post-upload bookkeeping (slug versioning).
    email_url: str | None             # Citra-Service endpoint for sending email
    llm_base_url: str | None          # OpenAI-compatible endpoint (OpenRouter by default)
    llm_api_key: str | None
    llm_model: str

    # --- Paths ---
    workspace_dir: str

    # --- Control plane ---
    bind_host: str
    bind_port: int

    # --- Auth for the control plane itself ---
    # Citra-Service shares this secret with the sandbox at launch time so only
    # Citra-Service can submit tasks into it.
    control_plane_secret: str

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        required = [
            "CITRA_AGENT_USER_ID", "CITRA_AGENT_SESSION_ID", "CITRA_AGENT_SCOPED_TOKEN",
            "CITRA_AGENT_CONTROL_SECRET",
            "CITRA_AGENT_LLM_BASE_URL", "CITRA_AGENT_LLM_API_KEY", "CITRA_AGENT_MODEL",
        ]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"Missing required env: {', '.join(missing)}")

        return cls(
            user_id=os.environ["CITRA_AGENT_USER_ID"],
            session_id=os.environ["CITRA_AGENT_SESSION_ID"],
            org_id=os.getenv("CITRA_AGENT_ORG_ID"),
            scoped_token=os.environ["CITRA_AGENT_SCOPED_TOKEN"],
            discovery_service_url=os.getenv("DISCOVERY_SERVICE_URL"),
            milvus_proxy_url=os.getenv("CITRA_AGENT_MILVUS_PROXY_URL"),
            email_url=os.getenv("CITRA_AGENT_EMAIL_URL"),
            artifact_url=os.getenv("CITRA_AGENT_ARTIFACT_URL"),
            proxy_base_url=os.getenv("CITRA_AGENT_PROXY_BASE_URL"),
            llm_base_url=os.getenv("CITRA_AGENT_LLM_BASE_URL"),
            llm_api_key=os.getenv("CITRA_AGENT_LLM_API_KEY"),
            llm_model=os.getenv("CITRA_AGENT_MODEL", ""),
            workspace_dir=os.getenv("CITRA_AGENT_WORKSPACE_DIR", "/workspace"),
            bind_host=os.getenv("CITRA_SANDBOX_HOST", "0.0.0.0"),
            bind_port=int(os.getenv("CITRA_AGENT_SANDBOX_PORT", "8090")),
            control_plane_secret=os.environ["CITRA_AGENT_CONTROL_SECRET"],
        )
