# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Fake customer-notification webhook receiver.

The wf_post_decision workflow's "notify customer" branch posts here.
We just store everything received in memory; tests assert on count
and payload shape via ``GET /admin/messages``.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("fake-notify")


app = FastAPI(title="fake-customer-notify", version="0.1.0")
_MESSAGES: List[Dict[str, Any]] = []


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "messages_received": len(_MESSAGES)}


@app.post("/notify")
async def notify(request: Request) -> Dict[str, Any]:
    body: Any
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = (await request.body()).decode("utf-8", errors="replace")
    _MESSAGES.append({
        "received_at": time.time(),
        "headers": dict(request.headers),
        "body": body,
    })
    logger.info("[fake-notify] received message #%d", len(_MESSAGES))
    return {"status": "queued", "id": f"msg-{len(_MESSAGES):06d}"}


@app.get("/admin/messages")
def get_messages() -> Dict[str, Any]:
    return {"count": len(_MESSAGES), "items": _MESSAGES}


@app.post("/admin/reset")
def reset_messages() -> Dict[str, Any]:
    _MESSAGES.clear()
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8700"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
