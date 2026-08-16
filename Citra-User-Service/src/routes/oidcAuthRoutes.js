// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * OIDC auth routes (Wave 3, slice A) — mounted at /api/auth/oidc ONLY when
 * AUTH_PROVIDERS includes 'oidc' (see server.js). Per-customer enterprise SSO.
 */
const express = require('express');
const router = express.Router();
const envConfig = require('../config/environment');
const oidcAuthService = require('../services/oidcAuthService');

/**
 * @route  POST /api/auth/oidc
 * @desc   Exchange a customer-IdP OIDC ID token for a Citra JWT (JIT-provisions
 *         the user with zero access on first login).
 * @access Public (the ID token IS the credential; verified against issuer JWKS)
 * Body:   { idToken | id_token, termsAcceptedAt? }
 */
router.post('/', async (req, res) => {
  const body = req.body || {};
  const idToken = body.idToken || body.id_token;
  if (!idToken || typeof idToken !== 'string') {
    return res.status(400).json({ success: false, error: 'idToken is required' });
  }
  try {
    const result = await oidcAuthService.authenticateWithOidc(
      idToken, { termsAcceptedAt: body.termsAcceptedAt });
    return res.json({ success: true, ...result });
  } catch (err) {
    // Verification / provisioning failures are a 401 — a bad or unverifiable
    // token is an auth failure, not a server error.
    console.error('[OIDC] authentication failed:', err.message || err);
    return res.status(401).json({
      success: false,
      error: `OIDC authentication failed: ${err.message || err}`,
    });
  }
});

/**
 * @route  GET /api/auth/oidc/health
 * @desc   Report whether OIDC is configured for this deployment (no secrets).
 */
router.get('/health', (req, res) => {
  res.json({
    success: true,
    provider: 'oidc',
    issuer: envConfig.oidcIssuer || null,
    configured: Boolean(envConfig.oidcIssuer && envConfig.oidcClientId),
  });
});

module.exports = router;
