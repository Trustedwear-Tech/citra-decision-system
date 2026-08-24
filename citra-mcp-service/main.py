# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""citra-mcp-service entrypoint.

Standalone FastAPI app that exposes Citra's tool surface (web search,
web fetch, and eventually vault / files / ocr / image / etc.) over the
MCP HTTP protocol. Both action-chat-service and smart-app-service inject
``CITRA_MCP_URL=http://<host>:9090/mcp`` into their sandbox spawns, so
the OpenClaw gateway inside every sandbox sees the same tool catalogue
without each consumer reimplementing the proxy logic.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env early so config defaults pick up overrides.
_HERE = Path(__file__).resolve().parent
for _env in (".env.local", ".env"):
    p = _HERE / _env
    if p.is_file():
        load_dotenv(p, override=False)

# Vault bootstrap MUST run BEFORE `from config import get_config` (that
# module reads os.getenv at import time). Uses inline urllib-only loader
# because this service's build context can't reach citra-service-utils.
if os.getenv("VAULT_ADDR"):
    from vault_bootstrap import load_from_vault
    load_from_vault()

import uvicorn
from fastapi import FastAPI

from api.mcp import router as mcp_router
from config import get_config

logging.basicConfig(
    level=os.getenv("CITRA_MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("citra-mcp-service")

# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set (loaded
# from Vault above in prod). Errors flow to GlitchTip → Monitoring-Service email.
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("citra-mcp-service")
except Exception as _exc:
    logger.warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]


app = FastAPI(
    title="Citra MCP Service",
    version="1.0.0",
    description=(
        "Citra tool surface exposed over the MCP HTTP protocol. "
        "Consumed by OpenClaw sandboxes spawned by action-chat-service "
        "and smart-app-service."
    ),
)

if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception as _exc:
        logger.warning(f"[sentry] trace_id middleware skipped: {_exc}")

app.include_router(mcp_router, tags=["MCP"])


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "citra-mcp-service", "mcp_endpoint": "/mcp", "health": "/health"}


def main() -> None:
    cfg = get_config()
    if not cfg.jwt_secret:
        logger.warning(
            "JWT_SECRET is not configured — /mcp will reject all calls. "
            "Set JWT_SECRET to the same value action-chat-service and "
            "smart-app-service use."
        )
    if not cfg.serper_api_key:
        logger.warning(
            "SERPER_API_KEY is not configured — citra_web_search calls "
            "will fail until it's set."
        )
    logger.info(
        "citra-mcp-service starting (env=%s) on %s:%s — accepted_scopes=%s",
        cfg.environment, cfg.listen_host, cfg.listen_port, list(cfg.accepted_scopes),
    )
    # Worker count env-tunable via WEB_CONCURRENCY (default 1). >1 forks multiple
    # event loops across cores; the in-process discovery LRU cache then becomes
    # per-worker (lower hit rate, not incorrect). uvicorn.run needs an import
    # string (not the app object) to fork workers.
    _workers = int(os.getenv("WEB_CONCURRENCY", "1") or "1")
    _log_level = os.getenv("CITRA_MCP_LOG_LEVEL", "info").lower()
    if _workers > 1:
        uvicorn.run(
            "main:app",
            host=cfg.listen_host,
            port=cfg.listen_port,
            workers=_workers,
            log_level=_log_level,
        )
    else:
        uvicorn.run(
            app,
            host=cfg.listen_host,
            port=cfg.listen_port,
            log_level=_log_level,
        )


if __name__ == "__main__":
    main()
