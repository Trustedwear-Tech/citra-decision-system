# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Inline Vault AppRole bootstrap for action-sandbox-host.

Mirrors citra_service_utils.vault_bootstrap but is self-contained — this
service's Docker build context is its own folder (not the monorepo root),
so it can't COPY the shared citra-service-utils package. urllib-only,
no third-party deps.

Usage in main.py (BEFORE `from config import ...`):

    from vault_bootstrap import load_from_vault
    if os.getenv("VAULT_ADDR"):
        load_from_vault()

Fails fast with SystemExit(1) on any Vault error so a missing secret
doesn't silently degrade the running service. Logs key NAMES only.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def load_from_vault(*, overwrite: bool = False) -> int:
    addr = (os.environ.get("VAULT_ADDR") or "").strip()
    role_id = (os.environ.get("VAULT_ROLE_ID") or "").strip()
    secret_id = (os.environ.get("VAULT_SECRET_ID") or "").strip()
    secret_path = (os.environ.get("VAULT_SECRET_PATH") or "").strip()
    for name, val in (("VAULT_ADDR", addr), ("VAULT_ROLE_ID", role_id),
                      ("VAULT_SECRET_ID", secret_id),
                      ("VAULT_SECRET_PATH", secret_path)):
        if not val:
            raise SystemExit(f"[vault_bootstrap] required env {name!r} empty")
    if "/" not in secret_path:
        raise SystemExit(f"[vault_bootstrap] VAULT_SECRET_PATH must be '<mount>/<path>', got {secret_path!r}")
    mount, _, sub = secret_path.partition("/")

    login_body = json.dumps({"role_id": role_id, "secret_id": secret_id}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"{addr.rstrip('/')}/v1/auth/approle/login",
                data=login_body,
                headers={"Content-Type": "application/json"},
                method="POST"), timeout=5.0) as r:
            token = ((json.load(r).get("auth") or {}).get("client_token"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        raise SystemExit(f"[vault_bootstrap] AppRole login failed: {e}")
    if not token:
        raise SystemExit("[vault_bootstrap] AppRole login returned no client_token")

    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"{addr.rstrip('/')}/v1/{mount}/data/{sub}",
                headers={"X-Vault-Token": token}), timeout=5.0) as r:
            bag = (json.load(r).get("data") or {}).get("data") or {}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        raise SystemExit(f"[vault_bootstrap] read {secret_path} failed: {e}")
    if not bag:
        raise SystemExit(f"[vault_bootstrap] {secret_path} empty")

    loaded = 0
    for k, v in bag.items():
        if not overwrite and k in os.environ:
            continue
        os.environ[k] = "" if v is None else str(v)
        loaded += 1
    logger.info("[vault_bootstrap] populated %d env vars from %s", loaded, secret_path)
    return loaded
