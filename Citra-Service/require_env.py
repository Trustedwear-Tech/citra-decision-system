# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Fail-loud environment-variable accessors (local copy).

Platform rule #1: never silently fall back. A missing or empty *required*
environment variable (a DB name, a connection URI, an internal-service URL,
a secret) must crash loudly with a clear message — not default to ``localhost``
/ a dev database / an empty secret and silently run against the wrong thing.
Silent infra defaults are what cause cross-environment bleed (prod reading the
dev Mongo) and are nearly impossible to spot at runtime.

This mirrors citra_service_utils.require_env; Citra-Service keeps a local copy
because it does not depend on that package.
"""
from __future__ import annotations

import os


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is unset or empty."""


def require_env(name: str) -> str:
    """Return ``os.environ[name]`` or raise loudly if unset/empty."""
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        raise MissingConfigError(
            f"Required environment variable {name!r} is not set "
            f"(or is empty). Set it explicitly — no silent default."
        )
    return val


def require_env_int(name: str) -> int:
    """Return a required env var parsed as ``int`` or raise loudly."""
    raw = require_env(name)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise MissingConfigError(
            f"Environment variable {name!r}={raw!r} is not a valid integer."
        ) from exc


def require_env_url(name: str) -> str:
    """Return a required URL env var, rejecting dev hosts in production.

    When ``ENVIRONMENT`` is ``prod``/``production`` this rejects bare
    ``localhost`` / ``127.0.0.1`` / Docker-Desktop ``host.docker.internal``
    hosts so a dev URL can't silently ship to prod. In dev/test those hosts
    are legitimate and allowed.
    """
    val = require_env(name)
    env = os.environ.get("ENVIRONMENT", "dev").strip().lower()
    if env in ("prod", "production"):
        lowered = val.lower()
        for bad in ("localhost", "127.0.0.1", "host.docker.internal"):
            if bad in lowered:
                raise MissingConfigError(
                    f"Environment variable {name!r}={val!r} points at {bad!r} "
                    f"while ENVIRONMENT={env} — a dev value leaking into "
                    f"production. Set the real host."
                )
    return val
