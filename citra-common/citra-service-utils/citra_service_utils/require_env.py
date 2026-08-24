# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Fail-loud environment-variable accessors.

Platform rule #1: never silently fall back. A missing or empty *required*
environment variable (a DB name, a connection URI, an internal-service URL,
a secret) must crash the process at startup with a clear message — not
default to ``localhost`` / a dev database / an empty secret and silently run
against the wrong thing. Silent infra defaults are what cause cross-environment
bleed (prod reading the dev Mongo) and are nearly impossible to spot at runtime.

Usage::

    from citra_service_utils import require_env, require_env_int

    MONGODB_DATABASE = require_env("MONGODB_DATABASE")
    REDIS_PORT = require_env_int("REDIS_PORT")

Only use ``os.getenv(name, default)`` directly for values that are *genuinely*
optional and where the default is correct in every environment (e.g. a tunable
timeout). Anything that selects which database/service/tenant you talk to, or
any secret, is required — use these helpers.
"""
from __future__ import annotations

import os
from typing import Optional


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is unset or empty."""


def require_env(name: str, *, allow_empty: bool = False) -> str:
    """Return ``os.environ[name]`` or raise :class:`MissingConfigError`.

    An unset variable always raises. An empty-string value also raises unless
    ``allow_empty=True`` (rarely correct — prefer leaving it required).
    """
    val = os.environ.get(name)
    if val is None:
        raise MissingConfigError(
            f"Required environment variable {name!r} is not set. "
            f"Set it explicitly (no silent default) — see the service .env / Vault bag."
        )
    if not allow_empty and val.strip() == "":
        raise MissingConfigError(
            f"Required environment variable {name!r} is set but empty. "
            f"Provide a real value (no silent default)."
        )
    return val


def require_env_int(name: str) -> int:
    """Return a required env var parsed as ``int`` or raise loudly.

    Raises :class:`MissingConfigError` if unset/empty, and a clear
    :class:`MissingConfigError` (not a bare ``ValueError``) if non-numeric.
    """
    raw = require_env(name)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise MissingConfigError(
            f"Environment variable {name!r}={raw!r} is not a valid integer."
        ) from exc


def require_env_url(name: str) -> str:
    """Return a required URL env var, rejecting dev hosts in production.

    Beyond presence, when ``ENVIRONMENT`` is ``prod``/``production`` this rejects
    bare ``localhost``/``127.0.0.1``/Docker-Desktop ``host.docker.internal`` hosts
    so a dev URL can't silently ship to prod (the cross-environment-bleed bug).
    In dev/test these hosts are legitimate and allowed.
    """
    val = require_env(name)
    env = os.environ.get("ENVIRONMENT", "dev").strip().lower()
    if env in ("prod", "production"):
        lowered = val.lower()
        for bad in ("localhost", "127.0.0.1", "host.docker.internal"):
            if bad in lowered:
                raise MissingConfigError(
                    f"Environment variable {name!r}={val!r} points at {bad!r} "
                    f"while ENVIRONMENT={env}. This is a dev value leaking into "
                    f"production — set the real host."
                )
    return val
