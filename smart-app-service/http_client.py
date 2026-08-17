# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Process-shared pooled httpx.AsyncClient for all upstream HTTP (dept-MCP,
discovery). Reuses connections (keep-alive) so we don't pay a fresh TCP+TLS
handshake on every panel load, dashboard metric, MCP read, or grounding pull.

Per-request timeouts are still passed at the call site (client.post(...,
timeout=...)); only the connection pool is shared. Created lazily on first
use (always inside the running event loop).
"""
from __future__ import annotations

import threading
from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None
_lock = threading.Lock()


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            _client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=50,
                    max_connections=200,
                    keepalive_expiry=30.0,
                ),
                timeout=httpx.Timeout(60.0),
            )
    return _client


async def aclose_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
