# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Vault bootstrap — authenticate via AppRole and populate os.environ before
config is loaded.

Why
---
Per the platform's Vault-only directive: no secrets in .env files baked into
images, no secret fallback chains. Each service must read its config bag from
Vault on startup using its own AppRole identity.

Usage
-----
The very first line of `main.py` (before any pydantic Settings / os.getenv
calls) must be::

    from citra_service_utils.vault_bootstrap import load_from_vault
    load_from_vault()  # fails fast if Vault is unreachable

Required env vars (set in the service's docker-compose.yml `environment:`):
- ``VAULT_ADDR``            e.g. http://vault:8200 (on citra-ai-net) or
                            http://172.31.39.51:8200 (box private IP)
- ``VAULT_ROLE_ID``         AppRole role_id (long-lived, non-secret)
- ``VAULT_SECRET_ID``       AppRole secret_id (sensitive; rotate via Vault)
- ``VAULT_SECRET_PATH``     e.g. prod/citra-service

The KV mount is parsed from VAULT_SECRET_PATH (first segment); KV v2 layout
is assumed (path becomes ``<mount>/data/<rest>``).

Behaviour
---------
- Fails fast (``SystemExit(1)``) if any required env var is missing.
- Fails fast on Vault HTTP errors (network / auth / not-found).
- Logs each key being populated at INFO (key names only, NO VALUES).
- If a key is already set in os.environ, it is NOT overwritten — useful for
  container-local debug overrides (e.g. ``ENVIRONMENT=test`` set at run time).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class VaultBootstrapError(SystemExit):
    """Vault bootstrap failed - service should exit non-zero so the operator notices."""


def _required_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise VaultBootstrapError(
            f"[vault_bootstrap] required env var {name!r} is empty or unset. "
            f"Service cannot read its config from Vault. Set it in docker-compose.yml."
        )
    return val


def _approle_login(addr: str, role_id: str, secret_id: str, timeout: float = 5.0) -> str:
    """Exchange role_id + secret_id for a short-lived client token."""
    body = json.dumps({"role_id": role_id, "secret_id": secret_id}).encode()
    req = urllib.request.Request(
        f"{addr.rstrip('/')}/v1/auth/approle/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:200]
        raise VaultBootstrapError(
            f"[vault_bootstrap] AppRole login failed ({e.code}): {msg}"
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise VaultBootstrapError(
            f"[vault_bootstrap] cannot reach Vault at {addr}: {e}"
        )
    token = (data.get("auth") or {}).get("client_token")
    if not token:
        raise VaultBootstrapError(
            f"[vault_bootstrap] AppRole login returned no client_token: {data!r}"
        )
    return token


def _kv_v2_read(addr: str, token: str, secret_path: str, timeout: float = 5.0) -> dict:
    """Read a KV v2 secret. secret_path like 'prod/citra-service' -> 'prod/data/citra-service'."""
    if "/" not in secret_path:
        raise VaultBootstrapError(
            f"[vault_bootstrap] VAULT_SECRET_PATH must be '<mount>/<path>', got {secret_path!r}"
        )
    mount, _, sub = secret_path.partition("/")
    api_path = f"{addr.rstrip('/')}/v1/{mount}/data/{sub}"
    req = urllib.request.Request(api_path, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:200]
        raise VaultBootstrapError(
            f"[vault_bootstrap] read of {secret_path} failed ({e.code}): {msg}"
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise VaultBootstrapError(
            f"[vault_bootstrap] cannot read {secret_path} from Vault: {e}"
        )
    return (data.get("data") or {}).get("data") or {}


def load_from_vault(*, overwrite: bool = False) -> int:
    """
    Bootstrap entry point. Authenticates via AppRole, reads VAULT_SECRET_PATH,
    populates os.environ. Returns number of keys loaded. Raises SystemExit on
    any failure.

    Set ``overwrite=True`` to let Vault values clobber existing env vars
    (default: existing env wins, useful for local-dev overrides).
    """
    addr = _required_env("VAULT_ADDR")
    role_id = _required_env("VAULT_ROLE_ID")
    secret_id = _required_env("VAULT_SECRET_ID")
    secret_path = _required_env("VAULT_SECRET_PATH")

    logger.info("[vault_bootstrap] authenticating to %s as AppRole role_id=%s…",
                addr, role_id[:8] + "...")
    token = _approle_login(addr, role_id, secret_id)
    logger.info("[vault_bootstrap] auth OK; reading %s", secret_path)

    bag = _kv_v2_read(addr, token, secret_path)
    if not bag:
        raise VaultBootstrapError(
            f"[vault_bootstrap] {secret_path} is empty - nothing to load. "
            f"Service would crash on missing config. Populate the Vault path first."
        )

    loaded = 0
    skipped = 0
    for k, v in bag.items():
        if not overwrite and k in os.environ:
            skipped += 1
            continue
        os.environ[k] = "" if v is None else str(v)
        loaded += 1
    logger.info("[vault_bootstrap] populated %d env vars from %s (skipped %d already-set)",
                loaded, secret_path, skipped)
    return loaded


__all__ = ["load_from_vault", "VaultBootstrapError"]
