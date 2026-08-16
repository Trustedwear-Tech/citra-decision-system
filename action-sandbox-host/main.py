# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Action Sandbox Host — FastAPI scheduler.

Runs on each sandbox-host VM. Owns the local Docker daemon and spawns
per-user citra-agent-sandbox-base containers (and builder pods that layer
on top of them) on behalf of the stateless
Citra-Service fleet.

Wire contract: see models.py. Every protected endpoint requires the
shared `X-Sandbox-Host-Secret` header (matches SANDBOX_HOST_SECRET env).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# Load local .env first (dev / local-only override path).
_HERE = Path(__file__).resolve().parent
for _envfile in (".env.local", ".env"):
    _path = _HERE / _envfile
    if _path.is_file():
        load_dotenv(_path, override=False)

# Vault bootstrap MUST run BEFORE `from config import ...` because config.py
# reads os.getenv at dataclass-definition time. Uses an inline urllib-only
# loader (this service's build context can't reach citra-service-utils).
if os.getenv("VAULT_ADDR"):
    from vault_bootstrap import load_from_vault
    load_from_vault()

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
import uvicorn

from config import get_config
from models import (
    CapacityResponse,
    OkResponse,
    SessionStatusResponse,
    SpawnRequest,
    SpawnResponse,
)
from scheduler import get_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("sandbox-host")

app = FastAPI(title="Citra Action Sandbox Host", version="0.1.0")


def _auth(x_sandbox_host_secret: str | None) -> None:
    """Constant-time check of the shared-secret header."""
    expected = get_config().shared_secret
    if not x_sandbox_host_secret or not secrets.compare_digest(
        x_sandbox_host_secret, expected
    ):
        raise HTTPException(status_code=401, detail="bad or missing X-Sandbox-Host-Secret")


@app.get("/health")
async def health() -> JSONResponse:
    # Unauthenticated liveness for LB / readiness probes.
    return JSONResponse({"status": "ok", "version": app.version})


@app.get("/capacity", response_model=CapacityResponse)
async def capacity(
    x_sandbox_host_secret: str | None = Header(default=None),
) -> CapacityResponse:
    _auth(x_sandbox_host_secret)
    return await get_scheduler().capacity()


@app.post("/spawn", response_model=SpawnResponse)
async def spawn(
    req: SpawnRequest,
    x_sandbox_host_secret: str | None = Header(default=None),
) -> SpawnResponse:
    _auth(x_sandbox_host_secret)
    try:
        return await get_scheduler().spawn(req)
    except RuntimeError as e:
        # Surface capacity / docker errors as 503 so the pool scheduler
        # in Citra-Service can retry another host.
        logger.exception(
            "spawn failed session=%s user=%s: %s",
            req.session_id, req.user_id, e,
        )
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/session/{session_id}", response_model=SessionStatusResponse)
async def session_status(
    session_id: str,
    x_sandbox_host_secret: str | None = Header(default=None),
) -> SessionStatusResponse:
    """Liveness of one session's container.

    smart-app-service calls this before reusing a builder pod: ``alive``
    is True only when a container exists and is in Docker's ``running``
    state. Never 404s — a missing session is a normal ``alive=false``.
    """
    _auth(x_sandbox_host_secret)
    try:
        return await get_scheduler().session_status(session_id)
    except RuntimeError as e:
        # Liveness could not be determined (Docker error). Surface as 503
        # so the caller fails loud rather than treating it as "pod dead"
        # and spawning a duplicate.
        logger.warning("session_status indeterminate for %s: %s", session_id, e)
        raise HTTPException(status_code=503, detail=str(e))


@app.delete("/session/{session_id}", response_model=OkResponse)
async def stop_session(
    session_id: str,
    x_sandbox_host_secret: str | None = Header(default=None),
) -> OkResponse:
    _auth(x_sandbox_host_secret)
    stopped = await get_scheduler().stop(session_id)
    return OkResponse(
        ok=stopped,
        detail=None if stopped else "session not found on this host",
    )


@app.post("/session/{session_id}/upload")
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    subdir: str = Form(default="uploads"),
    x_sandbox_host_secret: str | None = Header(default=None),
) -> JSONResponse:
    _auth(x_sandbox_host_secret)
    try:
        data = await file.read()
        result = await get_scheduler().upload(
            session_id,
            filename=file.filename or "upload.bin",
            data=data,
            subdir=subdir,
        )
        return JSONResponse(result)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _reaper_loop() -> None:
    """Periodically sweep leaked sandbox containers. Runs for the life of
    the process; one failed sweep is logged and the loop continues."""
    cfg = get_config()
    interval = cfg.reaper_interval_seconds
    logger.info("orphan reaper started (interval=%ds, builder_max_age=%ds)",
                interval, cfg.builder_max_age_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            n = await get_scheduler().reap_orphans()
            if n:
                logger.info("reaper sweep removed %d orphan/corpse container(s)", n)
        except Exception as e:  # noqa: BLE001 — never let the sweeper die
            logger.warning("reaper sweep failed (will retry next interval): %s", e)


# Strong reference to the reaper task. asyncio only keeps a weak reference
# to tasks, so without this the long-running sweeper could be garbage
# collected mid-flight.
_reaper_task: asyncio.Task | None = None


@app.on_event("startup")
async def _start_reaper() -> None:
    global _reaper_task
    if get_config().reaper_interval_seconds > 0:
        _reaper_task = asyncio.create_task(_reaper_loop())


def main() -> None:
    cfg = get_config()
    logger.info(
        "starting sandbox-host public_host=%s listen=%s:%d max=%d",
        cfg.public_host, cfg.listen_host, cfg.listen_port, cfg.max_concurrent,
    )
    uvicorn.run(app, host=cfg.listen_host, port=cfg.listen_port, log_level="info")


if __name__ == "__main__":
    main()
