# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Thin wrapper around the shared singleton MilvusClient.

Every RAG engine in this template used to construct a fresh
``MilvusClient(uri=..., token=..., timeout=30)`` on every search call —
that meant a brand-new gRPC channel per query, which doesn't scale and
breaks ungracefully under Gunicorn forks.

This module hides ``citra_service_utils.milvus_client.get_milvus_client``
behind a single ``get_client()`` so the engines don't need to know about
the settings dataclass or the lifecycle. Call ``close_client()`` from
the FastAPI shutdown hook.
"""
from __future__ import annotations

from typing import Any

from citra_service_utils.milvus_client import (
    MilvusSettings,
    close_milvus_client,
    get_milvus_client,
)

from config import get_settings


def get_client() -> Any:
    """Return the process-wide singleton MilvusClient, built from current
    config. Safe to call from any coroutine."""
    cfg = get_settings()
    return get_milvus_client(MilvusSettings(
        uri=cfg.milvus_uri,
        token=cfg.milvus_token or None,
        timeout=30,
    ))


def close_client() -> None:
    """Tear down the singleton on app shutdown. Idempotent."""
    close_milvus_client()
