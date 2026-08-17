// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Vault bootstrap wrapper for the Next.js standalone runtime.
 *
 * The standalone build's entrypoint is `node server.js`, which has no
 * fetch-Vault-at-boot step. This wrapper runs FIRST: it logs into Vault with
 * the service AppRole, overlays the KV v2 secret bag onto process.env, and
 * only then requires ./server.js to start Next.js. This mirrors the Python
 * services' env_loader.py and Citra-User-Service's vault-env-loader.js so
 * citra-app-runtime follows the same AppRole pattern (project-vault-approle-rollout).
 *
 * Config sources, in order of precedence:
 *   * Local dev  — values already in process.env (from .env via env_file /
 *     `next dev`). VAULT_ADDR is unset, so this wrapper is a no-op pass-through.
 *   * Prod/test  — VAULT_ADDR + VAULT_ROLE_ID (compose) + VAULT_SECRET_ID
 *     (.vault_secret env_file, mode 0600, not committed) + VAULT_SECRET_PATH.
 *     Secrets (JWT_SECRET, SMART_APP_SERVICE_URL, SENTRY_DSN,
 *     OTEL_EXPORTER_OTLP_ENDPOINT, ...) live in Vault, never in compose.
 *
 * Per RULE #1 (fail loud): when Vault IS configured but unreachable or the
 * AppRole login / read fails, we throw — the process exits non-zero rather
 * than silently booting with missing config.
 */

const VAULT_ADDR = process.env.VAULT_ADDR || "";
const VAULT_TOKEN = process.env.VAULT_TOKEN || "";
const VAULT_ROLE_ID = process.env.VAULT_ROLE_ID || "";
const VAULT_SECRET_ID = process.env.VAULT_SECRET_ID || "";
const VAULT_SECRET_PATH = process.env.VAULT_SECRET_PATH || "";
const VAULT_TIMEOUT_MS =
  parseFloat(process.env.VAULT_TIMEOUT || "10") * 1000;

function splitKv2Path(secretPath) {
  const p = secretPath.trim().replace(/^\/+|\/+$/g, "");
  if (!p.includes("/")) {
    throw new Error(
      `VAULT_SECRET_PATH must be '<mount>/<name>', e.g. 'prod/citra-app-runtime' (got '${secretPath}')`,
    );
  }
  const idx = p.indexOf("/");
  return [p.slice(0, idx), p.slice(idx + 1)];
}

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), VAULT_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function approleLogin(vaultAddr, roleId, secretId) {
  const url = `${vaultAddr.replace(/\/$/, "")}/v1/auth/approle/login`;
  const res = await fetchWithTimeout(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role_id: roleId, secret_id: secretId }),
  });
  if (!res.ok) {
    throw new Error(
      `AppRole login failed: HTTP ${res.status} ${res.statusText}`,
    );
  }
  const data = await res.json();
  const token = data?.auth?.client_token;
  if (!token) {
    throw new Error("AppRole login succeeded but no client_token in response");
  }
  return token;
}

async function kv2Read(vaultAddr, token, mount, name) {
  const url = `${vaultAddr.replace(/\/$/, "")}/v1/${mount}/data/${name}`;
  const res = await fetchWithTimeout(url, {
    headers: { "X-Vault-Token": token, "Content-Type": "application/json" },
  });
  if (res.status === 200) {
    const payload = await res.json();
    return (payload.data && payload.data.data) || {};
  }
  // 403 => write-only policy; 404 => secret not created yet. Both are
  // non-fatal "nothing to load", consistent with the other services' loaders.
  if (res.status === 403 || res.status === 404) {
    console.log(
      `⚠️  Vault KV read not permitted or not found (HTTP ${res.status}) at ${mount}/${name}`,
    );
    return {};
  }
  throw new Error(`KV read failed: HTTP ${res.status} ${res.statusText}`);
}

async function loadVaultSecrets() {
  if (!VAULT_ADDR) {
    console.log("🔓 VAULT_ADDR not set — using .env / process env only");
    return;
  }

  let vaultToken = VAULT_TOKEN;
  if (!vaultToken) {
    if (!(VAULT_ROLE_ID && VAULT_SECRET_ID)) {
      throw new Error(
        "VAULT_ADDR is set but neither VAULT_TOKEN nor VAULT_ROLE_ID+VAULT_SECRET_ID provided — cannot authenticate to Vault",
      );
    }
    console.log("🔐 Logging into Vault with AppRole");
    vaultToken = await approleLogin(VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID);
    console.log("🔑 AppRole login successful");
  }

  if (!VAULT_SECRET_PATH) {
    throw new Error(
      "VAULT_ADDR is set but VAULT_SECRET_PATH is empty — refusing to boot without a secret path",
    );
  }

  const [mount, name] = splitKv2Path(VAULT_SECRET_PATH);
  console.log(
    `📦 Fetching KV v2 secret at ${VAULT_SECRET_PATH} (mount=${mount}, name=${name})`,
  );
  const secrets = await kv2Read(VAULT_ADDR, vaultToken, mount, name);

  let loaded = 0;
  for (const [key, val] of Object.entries(secrets)) {
    // compose-supplied non-secrets already in env win over Vault only in
    // dev; in prod Vault is authoritative.
    if (process.env.NODE_ENV !== "production" && process.env[key]) continue;
    process.env[key] = String(val);
    loaded += 1;
  }
  console.log(`✅ Loaded ${loaded} Vault secrets from ${VAULT_SECRET_PATH}`);
}

loadVaultSecrets()
  .then(() => {
    // Hand off to the Next.js standalone server with env now populated.
    require("./server.js");
  })
  .catch((err) => {
    console.error("❌ Vault bootstrap failed:", err.message);
    process.exit(1);
  });
