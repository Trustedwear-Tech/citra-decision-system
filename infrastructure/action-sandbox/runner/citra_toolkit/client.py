# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Shared HTTP plumbing for the toolkit.

We deliberately keep state minimal: each call site builds a short-lived
``httpx.Client`` (or ``AsyncClient``) so the agent can spawn many sub-tasks
without sharing a connection pool that might leak across them.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import httpx


def scoped_token() -> str:
    """JWT minted by Citra-Service for THIS sandbox.

    Audience = ``citra-action-sandbox``. Same HS256 secret as Citra-Service,
    so discovery-service / Citra-Service internal endpoints accept it.
    """
    tok = os.getenv("CITRA_AGENT_SCOPED_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "CITRA_AGENT_SCOPED_TOKEN is not set inside the sandbox; "
            "the adapter should always inject it"
        )
    return tok


def _bearer_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {scoped_token()}"}
    if extra:
        h.update(extra)
    return h


# Public alias — used by toolkit modules that build their own httpx clients
# (e.g. for streaming) but still want the standard scoped-bearer headers.
bearer_headers = _bearer_headers


@contextmanager
def http_client(timeout: float = 30.0) -> Iterator[httpx.Client]:
    """Sync httpx.Client preconfigured with the scoped bearer token."""
    with httpx.Client(timeout=timeout, headers=_bearer_headers()) as c:
        yield c
