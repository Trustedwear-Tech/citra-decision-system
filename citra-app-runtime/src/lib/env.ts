// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Runtime env helpers.
 *
 * RULE #1 (fail loud): a required service URL / connection string / secret
 * gets NO fallback default. A missing value must surface at request time
 * rather than silently routing to a localhost dev host.
 *
 * These are evaluated lazily (inside request handlers), NOT at module scope,
 * so `next build` — which imports route modules to read their config — does
 * not crash when the variable is absent in the CI build environment. Env is
 * populated at boot by vault-bootstrap.js (prod) or .env (local dev).
 */

export function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Required environment variable ${name} is not set — refusing to fall ` +
        `back to a localhost default. Set it in .env (local dev) or Vault (prod).`,
    );
  }
  return value;
}

/** Base URL of smart-app-service. Required; throws if unset. */
export function smartAppServiceUrl(): string {
  return requireEnv("SMART_APP_SERVICE_URL");
}
