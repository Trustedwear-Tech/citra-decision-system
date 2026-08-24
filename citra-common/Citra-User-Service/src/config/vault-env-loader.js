// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const dotenv = require('dotenv');
const dotenvExpand = require('dotenv-expand');
const axios = require('axios');
const path = require('path');

// 1. Load .env first (used in dev only)
// Look for .env file in project root
const envPath = path.resolve(process.cwd(), '.env');
const env = dotenv.config({ path: envPath });
dotenvExpand.expand(env);

// Log environment loading status
if (env.error) {
  console.log('📄 No .env file found - will rely on system environment variables or Vault');
} else {
  console.log('📄 Loaded .env file from:', envPath);
}

const VAULT_ADDR = process.env.VAULT_ADDR || "";
const VAULT_TOKEN = process.env.VAULT_TOKEN || "";
const VAULT_ROLE_ID = process.env.VAULT_ROLE_ID || "";
const VAULT_SECRET_ID = process.env.VAULT_SECRET_ID || "";
const VAULT_SECRET_PATH = process.env.VAULT_SECRET_PATH || "env/Citra-User-Service";
const VAULT_TIMEOUT = parseFloat(process.env.VAULT_TIMEOUT || "10") * 1000; // Convert to milliseconds
const NODE_ENV = process.env.NODE_ENV || "production";

// Helper function to get environment variable with default
function _env(name, defaultValue = null) {
  const value = process.env[name];
  return (value !== undefined && value !== "") ? value : defaultValue;
}

// Helper function to split KV2 path
function _split_kv2_path(secretPath) {
  const p = secretPath.trim().replace(/^\/+|\/+$/g, '');
  if (!p.includes('/')) {
    throw new Error(`VAULT_SECRET_PATH must be '<mount>/<name>', e.g. 'env/Citra-User-Service'`);
  }
  return p.split('/', 2);
}

// AppRole login function
async function _approle_login(vaultAddr, roleId, secretId, timeout) {
  const url = `${vaultAddr.replace(/\/$/, '')}/v1/auth/approle/login`;
  try {
    const response = await axios.post(url, {
      role_id: roleId,
      secret_id: secretId
    }, {
      timeout: timeout,
      headers: { 'Content-Type': 'application/json' }
    });

    const token = response.data?.auth?.client_token;
    if (!token) {
      throw new Error('AppRole login succeeded but no client_token in response');
    }
    return token;
  } catch (error) {
    if (error.response) {
      throw new Error(`AppRole login failed: HTTP ${error.response.status} ${error.response.statusText}`);
    }
    throw error;
  }
}

// KV2 read function
async function _kv2_read(vaultAddr, token, mount, name, timeout) {
  const url = `${vaultAddr.replace(/\/$/, '')}/v1/${mount}/data/${name}`;
  try {
    const response = await axios.get(url, {
      headers: {
        'X-Vault-Token': token,
        'Content-Type': 'application/json'
      },
      timeout: timeout,
      validateStatus: function (status) {
        return status < 500; // Accept any status to handle manually
      }
    });

    if (response.status === 200) {
      const payload = response.data;
      return (payload.data && payload.data.data) || {};
    } else if (response.status === 403 || response.status === 404) {
      // 403 => likely write-only policy; 404 => not created yet
      console.log(`⚠️  Vault KV read not permitted or not found (HTTP ${response.status}). If this is a write-only account, reads are intentionally blocked.`);
      return {};
    } else {
      throw new Error(`KV read failed: HTTP ${response.status} ${response.statusText}`);
    }
  } catch (error) {
    throw error;
  }
}

// Helper function to validate required environment variables
function validateRequiredEnvVars() {
  const required = [
    // Add required environment variables here if needed
  ];

  const optional = [
    'MONGODB_CONNECTION_STRING'  // Only needed for Citra AI functionality
  ];

  const missing = required.filter(key => !process.env[key]);

  if (missing.length > 0) {
    console.error('❌ Missing required environment variables:', missing.join(', '));
    if (NODE_ENV === 'production') {
      process.exit(1);
    } else {
      console.log('⚠️  Development mode: continuing without required variables (may cause runtime errors)');
    }
  } else {
    console.log('✅ All required environment variables are present');
  }

  // Log optional variables status
  const missingOptional = optional.filter(key => !process.env[key]);
  if (missingOptional.length > 0) {
    console.log('ℹ️  Optional environment variables not set:', missingOptional.join(', '));
  }
}

async function loadVaultSecrets() {
  console.log(`🔧 Environment: ${NODE_ENV}`);

  if (!VAULT_ADDR) {
    console.log("🔓 VAULT_ADDR not set — using .env only");
    validateRequiredEnvVars();
    return {};
  }

  let vaultToken = VAULT_TOKEN;

  // If no token, try AppRole
  if (!vaultToken) {
    if (VAULT_ROLE_ID && VAULT_SECRET_ID) {
      console.log("🔐 Logging into Vault with AppRole");
      try {
        vaultToken = await _approle_login(VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID, VAULT_TIMEOUT);
        console.log("� AppRole login successful (received client token)");
      } catch (error) {
        console.error("❌ AppRole login failed:", error.message);
        if (NODE_ENV === 'production') {
          throw error;
        } else {
          console.log("� Development mode: continuing with .env file only");
          validateRequiredEnvVars();
          return {};
        }
      }
    } else {
      console.log("🔧 Vault configured without VAULT_TOKEN or VAULT_ROLE_ID/VAULT_SECRET_ID — skipping");
      validateRequiredEnvVars();
      return {};
    }
  }

  // Health check (best-effort)
  try {
    const healthResponse = await axios.get(`${VAULT_ADDR.replace(/\/$/, '')}/v1/sys/health`, {
      timeout: VAULT_TIMEOUT,
      validateStatus: function (status) {
        return status === 200 || status === 429; // 200 active, 429 standby
      }
    });
    if (healthResponse.status !== 200 && healthResponse.status !== 429) {
      console.log(`⚠️  Vault health not OK (HTTP ${healthResponse.status}): ${healthResponse.statusText}`);
    }
  } catch (error) {
    console.log(`⚠️  Could not check Vault health: ${error.message}`);
  }

  // Resolve KV v2 mount/name and read
  try {
    const [mount, name] = _split_kv2_path(VAULT_SECRET_PATH);
    console.log(`📦 Fetching KV v2 secret at ${VAULT_SECRET_PATH} (mount=${mount}, name=${name})`);
    const secrets = await _kv2_read(VAULT_ADDR, vaultToken, mount, name, VAULT_TIMEOUT);

    const loadedSecrets = {};
    if (Object.keys(secrets).length > 0) {
      for (const [key, val] of Object.entries(secrets)) {
        // Don't override existing environment variables in development
        if (NODE_ENV === 'development' && process.env[key]) {
          console.log(`⚠️  Skipping ${key} - already set in environment`);
          continue;
        }

        process.env[key] = String(val);
        loadedSecrets[key] = String(val);
      }
      console.log(`✅ Loaded ${Object.keys(loadedSecrets).length} Vault secrets from ${VAULT_SECRET_PATH}`);
    } else {
      console.log("ℹ️  No secrets loaded from Vault (read not permitted or secret missing).");
    }

    validateRequiredEnvVars();
    return loadedSecrets;

  } catch (error) {
    console.error("❌ Failed to load Vault secrets:", error.message);

    if (NODE_ENV === 'production') {
      throw error;
    } else {
      console.log("🔧 Development mode: continuing with .env file only");
      validateRequiredEnvVars();
      return {};
    }
  }
}

// Export the loader function and validation helper
module.exports = {
  loadVaultSecrets,
  validateRequiredEnvVars
};

// If this script is run directly, load the environment variables
if (require.main === module) {
  loadVaultSecrets().catch(error => {
    console.error('Failed to load environment variables:', error.message);
    process.exit(1);
  });
}
