// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const envConfig = require('../config/environment');
const ServiceAccount = require('../models/ServiceAccount');

class TokenService {
  /**
   * Generate JWT token for any auth provider.
   *
   * Embeds enterprise identity (org_id, dept_ids, roles) for fast downstream
   * authorisation without per-query DB lookups.
   *
   * **Also embeds the user's service-account membership** so downstream
   * services know which SAs this user can act on resources for:
   *
   *   service_account_admin_of:  ["svc:plant-ops-ingestion@acme-cement.citra.ai", ...]
   *   service_account_member_of: ["svc:claims-bot@acme-insurance.citra.ai", ...]
   *
   * @param {Object} user - Mongoose user document
   * @param {Object} [opts]
   * @param {number} [opts.ttlSeconds] - override default TTL (used for
   *   short-lived impersonation tokens; default = envConfig.jwtExpiresIn)
   * @param {Object} [opts.extraClaims] - extra claims merged into the JWT
   *   body. Used by impersonation to embed `act` (acting admin email) +
   *   `impersonation_id` (audit-row uuid). Reserved claims (user_id, email,
   *   org_id, roles, etc.) cannot be overridden — they always come from
   *   the user document.
   * @param {string} [opts.jti] - explicit JWT id. When omitted a random uuid
   *   is generated. Callers that may need to REVOKE the token later (e.g.
   *   impersonation) pass their own jti so they can store it and later add it
   *   to the `revoked_tokens` blocklist.
   * @returns {Promise<string>} signed JWT
   */
  async generateToken(user, opts = {}) {
    if (!envConfig.jwtSecret) {
      throw new Error('JWT secret is not configured');
    }

    // Query SA membership so the JWT carries the full picture.
    // Cached at JWT mint time; user gets a fresh token on next login.
    let saAdminOf = [];
    let saMemberOf = [];
    try {
      const sas = await ServiceAccount.findByUserMembership(user.email);
      for (const sa of sas) {
        if (sa.isAdmin(user.email)) {
          saAdminOf.push(sa.service_account_id);
        } else if (sa.isMember(user.email)) {
          saMemberOf.push(sa.service_account_id);
        }
      }
    } catch (err) {
      // Don't fail login if SA query fails — log and continue with empty lists.
      console.warn('[tokenService] SA membership lookup failed:', err.message);
    }

    const corePayload = {
      user_id: user.email,
      email: user.email,
      googleId: user.googleId || null,
      authProvider: user.authProvider || 'google',
      // Enterprise identity
      org_id: user.org_id || null,
      dept_ids: Array.isArray(user.dept_ids) ? user.dept_ids : [],
      district_ids: user.district_ids || [],
      entity_type: user.entity_type || 'general',
      roles: user.roles || ['user'],
      // ── Service-account membership ──
      is_service_account: false,
      personal_sa_id: user.personal_sa_id || null,
      work_sa_id: user.work_sa_id || null,
      service_account_admin_of: saAdminOf,
      service_account_member_of: saMemberOf,
      // ── Workflow surface config ──
      // The IT department slug for this deployment. Frontend reads it
      // to decide whether a dept_admin's dept_ids grant workflow access.
      // Defaults to "it"; deployments override via WORKFLOW_IT_DEPT_ID
      // on user-service. Mirrors the same env var on citra-workflow.
      it_dept_id: (process.env.WORKFLOW_IT_DEPT_ID || 'it').toLowerCase(),
    };

    // Merge extra claims (impersonation, audit markers). Core claims win —
    // a caller cannot impersonate by claiming a different org_id/role.
    const payload = { ...(opts.extraClaims || {}), ...corePayload };

    const signOpts = {
      expiresIn: opts.ttlSeconds || envConfig.jwtExpiresIn || '7d',
      issuer: 'Citra-AI',
      subject: user.email,
      // jti enables targeted revocation via the `revoked_tokens` blocklist.
      // Callers needing to revoke later (impersonation) supply their own.
      jwtid: opts.jti || crypto.randomUUID(),
    };

    return jwt.sign(payload, envConfig.jwtSecret, signOpts);
  }

  /**
   * Mint a short-lived JWT for a service account (machine identity).
   *
   * Used by:
   *   - citra-workflow scheduler when firing a SA-owned scheduled workflow
   *   - The admin API endpoint `/api/admin/service-accounts/{id}/token`
   *     when a managed_by admin manually requests one
   *
   * Claims include `is_service_account=true` and `sa_minted_at` so
   * downstream auth middleware can refuse tokens minted before the SA's
   * last_rotated_at.
   *
   * @param {Object} sa - ServiceAccount document
   * @param {Object} [opts]
   * @param {number} [opts.ttl_seconds=3600] - token lifetime
   * @returns {string} signed JWT
   */
  generateServiceAccountToken(sa, { ttl_seconds = 3600 } = {}) {
    if (!envConfig.jwtSecret) {
      throw new Error('JWT secret is not configured');
    }
    if (!sa.canMintToken()) {
      throw new Error('Service account is disabled or inactive');
    }

    const now = Math.floor(Date.now() / 1000);
    return jwt.sign(
      {
        sub: sa.service_account_id,
        user_id: sa.service_account_id,
        email: sa.service_account_id,
        is_service_account: true,
        org_id: sa.org_id,
        dept_ids: sa.dept_ids || [],
        entity_type: 'company',
        roles: sa.roles || ['service:workflow_runner'],
        // jti for the revocation blocklist (revoked_tokens).
        jti: crypto.randomUUID(),
        // Audit marker — Citra-Service compares this against
        // sa.last_rotated_at to invalidate post-rotation tokens.
        sa_minted_at: now,
        // Admins of this SA — surfaced so downstream services don't
        // need a back-channel lookup to know who could have minted this.
        sa_admin_emails: sa.admins,
        // Membership lists not embedded — keep token small. Anyone
        // checking visibility against a SA-owned resource looks up the
        // SA doc once per request and caches.
        iat: now,
        exp: now + Math.max(60, Math.min(ttl_seconds, 86400)),
        iss: 'Citra-AI',
        aud: 'service-account',
      },
      envConfig.jwtSecret,
      { algorithm: 'HS256' },
    );
  }

  /**
   * Verify + decode JWT.
   *
   * Service-account tokens additionally check `sa_minted_at >=
   * service_account.last_rotated_at` — but that check requires a DB
   * lookup, so it's done at the caller level (auth middleware) not here.
   *
   * @param {string} token
   * @returns {Object} decoded payload
   */
  verifyToken(token) {
    if (!envConfig.jwtSecret) {
      throw new Error('JWT secret is not configured');
    }
    try {
      return jwt.verify(token, envConfig.jwtSecret);
    } catch (error) {
      throw new Error(`Invalid JWT token: ${error.message}`);
    }
  }
}

module.exports = new TokenService();
