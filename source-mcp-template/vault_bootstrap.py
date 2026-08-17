# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Inline Vault AppRole bootstrap for dept-MCP deployments (source-mcp-template).

Mirrors data-discovery-service/vault_bootstrap.py — self-contained,
urllib-only, no third-party deps, because a dept MCP image must not grow a
Vault SDK just for boot-time config.

Deployment model — a dept MCP ships to CUSTOMERS and may run on ANY cloud as a
container. It must boot from whatever secret-delivery mechanism that estate uses,
so config is resolved by an env-first precedence (see ``bootstrap_secrets``):

  * CUSTOMER CLOUD (any) — the platform injects config as ENVIRONMENT VARIABLES
    (ECS/Fargate task ``secrets``, Cloud Run ``--set-secrets``, Container Apps
    ``secretRef``, a k8s Secret env). No Vault, no .env. Set
    ``SECRETS_PROVIDER=env`` to declare this and fail loud on any missing key.
  * DEMO-PROD (AWS, ours) — docker-compose sets VAULT_ADDR + VAULT_ROLE_ID +
    VAULT_SECRET_PATH (e.g. ``prod/mcp-demo-bsphcl``) and mounts a mode-600
    ``.vault_secret`` env_file carrying VAULT_SECRET_ID. The whole config bag
    (JWT_SECRET, MCP_API_KEY, SMART_APP_SERVICE_URL, Milvus/embedding/LLM keys,
    …) lives in Vault; nothing secret stays in an on-box .env.
  * LOCAL DEV — no VAULT_ADDR set → config comes from the mcp/.env file.

Usage in main.py (BEFORE any import that pulls ``config`` — Settings reads env
at class-definition time):

    from vault_bootstrap import bootstrap_secrets
    bootstrap_secrets()

Injected environment ALWAYS wins over the Vault bag (overwrite=False), so
identity/config already present in the process is never clobbered. Fails fast
with SystemExit(1) on any Vault error or a misconfiguration so a missing secret
never silently degrades a running MCP (RULE #1). Logs key NAMES only, never
values.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# The four env vars that TOGETHER configure Vault access. All-or-nothing: a
# partial set is a misconfiguration (bootstrap_secrets fails loud on it).
_VAULT_VARS = ("VAULT_ADDR", "VAULT_ROLE_ID", "VAULT_SECRET_ID", "VAULT_SECRET_PATH")


def _vault_configured() -> tuple[bool, list[str]]:
    """Return (all_present, missing_names) for the Vault access vars."""
    present = {v: bool((os.environ.get(v) or "").strip()) for v in _VAULT_VARS}
    missing = [v for v, ok in present.items() if not ok]
    return (len(missing) == 0), missing


def bootstrap_secrets() -> str:
    """Resolve boot-time config from the highest-priority available source and
    return the provider actually used ("env" | "vault").

    A dept MCP runs at the customer's end on any cloud, so environment injection
    is a first-class, self-sufficient path — Vault is only OUR demo-prod default.
    Precedence, highest first:

      1. Platform-injected environment variables — ALWAYS win, never overwritten.
      2. HashiCorp Vault — FALLBACK that fills only keys still missing, and only
         when fully configured (all of VAULT_ADDR/ROLE_ID/SECRET_ID/SECRET_PATH).
      3. .env — local dev only, loaded lazily by config.py (not here).

    Selected by ``SECRETS_PROVIDER`` (default ``auto``):
      * ``auto``  — env first; if Vault is fully configured, pull the bag to fill
                    gaps (injected values win). Partial Vault config → fail loud.
      * ``env``   — trust the environment ONLY; never contact Vault. A missing
                    required key fails loud later at config validation.
      * ``vault`` — require Vault; fail loud if it is not fully configured.
    """
    provider = (os.environ.get("SECRETS_PROVIDER") or "auto").strip().lower()
    if provider not in ("auto", "env", "vault"):
        raise SystemExit(
            f"[vault_bootstrap] SECRETS_PROVIDER must be 'auto', 'env' or "
            f"'vault', got {provider!r}")

    configured, missing = _vault_configured()

    if provider == "env":
        # Customer estate injects everything as env vars — never touch Vault,
        # even if some VAULT_* var happens to be set in the environment.
        logger.info("[vault_bootstrap] SECRETS_PROVIDER=env — using injected "
                    "environment only; Vault skipped.")
        return "env"

    if provider == "vault":
        if not configured:
            raise SystemExit(
                "[vault_bootstrap] SECRETS_PROVIDER=vault but Vault is not fully "
                f"configured — missing {missing}. Set all of {list(_VAULT_VARS)}.")
        load_from_vault()
        return "vault"

    # provider == "auto"
    if configured:
        load_from_vault()
        return "vault"
    if missing and len(missing) < len(_VAULT_VARS):
        # SOME but not all Vault vars set → almost-certainly a broken Vault
        # deployment, not an intentional env-only run. Fail loud (RULE #1)
        # rather than silently booting on a half-populated config.
        raise SystemExit(
            "[vault_bootstrap] Vault is partially configured — missing "
            f"{missing}. Set all of {list(_VAULT_VARS)}, or set "
            "SECRETS_PROVIDER=env to boot from injected environment only.")
    # No Vault vars at all → pure env-injection / local .env path.
    logger.info("[vault_bootstrap] no Vault configured — using injected "
                "environment (and .env if present).")
    return "env"


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
