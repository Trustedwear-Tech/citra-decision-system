# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Embedding service proxy — calls Citra-Service which proxies to the
configured embedding provider (OpenAI-compatible). No model, no torch
inside the sandbox.
"""
from __future__ import annotations

from typing import Sequence

from ._proxy import proxy_url
from .client import http_client


def encode(texts: Sequence[str], *, timeout: float = 60.0) -> list[list[float]]:
    """Embed a batch of texts. Returns a list of vectors (lists of floats).

    Empty / blank inputs are passed through to the proxy, which decides
    how to handle them (Citra-Service ``embed_texts_batch`` skips empties).
    """
    payload = {"texts": list(texts)}
    with http_client(timeout=timeout) as c:
        r = c.post(proxy_url("embed"), json=payload)
        r.raise_for_status()
        data = r.json() or {}
    return list(data.get("embeddings") or [])


def encode_one(text: str, *, timeout: float = 60.0) -> list[float]:
    """Convenience: embed a single string and return one vector."""
    out = encode([text], timeout=timeout)
    return out[0] if out else []
