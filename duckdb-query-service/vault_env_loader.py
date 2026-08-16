# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

import os
import logging
import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if (v is not None and v != "") else default

def _split_kv2_path(secret_path: str) -> tuple[str, str]:
    p = secret_path.strip().strip("/")
    if "/" not in p:
        raise ValueError("VAULT_SECRET_PATH must be '<mount>/<name>', e.g. 'env/citra-ai'")
    return p.split("/", 1)

def _vault_session(timeout: float):
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    s.timeout = timeout
    return s

def _approle_login(vault_addr: str, role_id: str, secret_id: str, timeout: float) -> str:
    url = f"{vault_addr.rstrip('/')}/v1/auth/approle/login"
    resp = requests.post(url, json={"role_id": role_id, "secret_id": secret_id}, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"AppRole login failed: HTTP {resp.status_code} {resp.text}")
    token = (resp.json().get("auth") or {}).get("client_token")
    if not token:
        raise RuntimeError("AppRole login succeeded but no client_token in response")
    return token

def _is_production_mount(mount: str) -> bool:
    return mount.lower().strip() in ("prod", "production")

def _kv2_read(vault_addr: str, token: str, mount: str, name: str, timeout: float) -> dict:
    url = f"{vault_addr.rstrip('/')}/v1/{mount}/data/{name}"
    resp = requests.get(url, headers={"X-Vault-Token": token}, timeout=timeout)
    if resp.status_code == 200:
        payload = resp.json()
        return ((payload.get("data") or {}).get("data")) or {}
    if resp.status_code in (403, 404):
        if _is_production_mount(mount):
            raise RuntimeError(
                f"FATAL: Vault KV read failed in PRODUCTION (HTTP {resp.status_code}). "
                f"Cannot start without secrets. Check Vault token permissions for '{mount}/{name}'."
            )
        logger.warning("Vault KV read not permitted or not found (HTTP %s).", resp.status_code)
        return {}
    raise RuntimeError(f"KV read failed: HTTP {resp.status_code} {resp.text}")

def load_environment_variables() -> dict:
    load_dotenv()
    logger.info("Loaded local .env file")

    vault_addr = _env("VAULT_ADDR")
    vault_token = _env("VAULT_TOKEN")
    role_id = _env("VAULT_ROLE_ID")
    secret_id = _env("VAULT_SECRET_ID")
    secret_path = _env("VAULT_SECRET_PATH", "env/citra-ai")
    timeout = float(_env("VAULT_TIMEOUT", "10"))

    if not vault_addr:
        logger.info("VAULT_ADDR not set — using .env only")
        return {}

    if not vault_token:
        if role_id and secret_id:
            logger.info("Logging into Vault with AppRole")
            vault_token = _approle_login(vault_addr, role_id, secret_id, timeout)
            logger.info("AppRole login successful")
        else:
            logger.info("Vault configured without token or AppRole — skipping")
            return {}

    try:
        health = requests.get(f"{vault_addr.rstrip('/')}/v1/sys/health", timeout=timeout)
        if health.status_code not in (200, 429):
            logger.warning("Vault health not OK (HTTP %s): %s", health.status_code, health.text)
    except requests.RequestException as e:
        logger.warning("Could not check Vault health: %s", e)

    try:
        mount, name = _split_kv2_path(secret_path)
        logger.info("Fetching KV v2 secret at %s (mount=%s, name=%s)", secret_path, mount, name)
        secrets = _kv2_read(vault_addr, vault_token, mount, name, timeout)

        if secrets:
            for k, v in secrets.items():
                os.environ[k] = str(v)
            logger.info("Loaded %d Vault secrets from %s", len(secrets), secret_path)
        else:
            if _is_production_mount(mount):
                raise RuntimeError(
                    f"FATAL: No secrets loaded from Vault in PRODUCTION (mount={mount}). "
                    f"Service cannot start without critical config."
                )
            logger.info("No secrets loaded from Vault.")

        return {k: str(v) for k, v in secrets.items()}
    except requests.exceptions.RequestException as e:
        logger.error("Vault connection error: %s", e)
        raise
    except Exception as e:
        logger.error("Failed to load Vault secrets: %s", e)
        raise

if __name__ == "__main__":
    load_environment_variables()
