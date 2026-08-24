# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Resolve action-chat-service proxy URLs for the citra_toolkit clients.

The sandbox is given ``CITRA_AGENT_PROXY_BASE_URL`` (e.g.
``http://actionchat-service:8090/actionchat/internal``) by the spawn
manager. Every internal endpoint resolves under that base.
"""
from __future__ import annotations

import os


def _proxy_base() -> str:
    base = (os.getenv("CITRA_AGENT_PROXY_BASE_URL") or "").rstrip("/")
    return base


def proxy_url(path: str, *, legacy_env: str | None = None) -> str:
    """Return the full URL for an internal endpoint.

    ``path`` is the trailing piece after ``/actionchat/internal/`` —
    e.g. ``"web-search"``, ``"embed"``.

    ``legacy_env`` (rare) is a fully-qualified URL env var that wins
    when present; kept for endpoints that need an out-of-band override.
    """
    if legacy_env:
        legacy = (os.getenv(legacy_env) or "").strip()
        if legacy:
            return legacy.rstrip("/")
    base = _proxy_base()
    if not base:
        raise RuntimeError(
            "CITRA_AGENT_PROXY_BASE_URL is not set inside the sandbox; "
            "the spawn manager must inject it"
        )
    return f"{base}/{path.lstrip('/')}"
