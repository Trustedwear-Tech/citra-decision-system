// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * OIDC authentication — per-customer enterprise SSO (Wave 3, slice A).
 *
 * The bridge that lets a customer's own IdP (Okta / Azure AD / Auth0 / Keycloak /
 * Google Workspace, anything OIDC) log users into a single-tenant Citra
 * deployment. It is ADDITIVE: it adds one new front door that lands in the SAME
 * `createOrUpdateUser` + Personal/Work-SA path Google login uses, then mints the
 * SAME enterprise Citra JWT (org_id/dept_ids/roles/SA). Nothing in the Google or
 * local paths changes; this activates only when AUTH_PROVIDERS includes 'oidc'.
 *
 * Flow (mirrors googleAuthService.authenticateWithGoogle): the SPA runs the OIDC
 * dance with the customer IdP (PKCE) and POSTs us the resulting ID token; we
 * verify its signature against the issuer's JWKS and check iss + aud, then
 * provision the user and mint the Citra JWT.
 *
 * Department assignment has two modes (see ./oidcGroupMap):
 *   - a group map is configured → dept_ids/roles are derived from the ID
 *     token's group claim on EVERY login; the customer's directory is the
 *     source of truth, so grants and revocations happen in their IdP.
 *   - no group map → JIT-provision with ZERO access (roles ['user'], no dept)
 *     and an admin grants dept+role afterwards via userAdminRoutes.
 */
const envConfig = require('../config/environment');
const CitraAIUser = require('../models/CitraAIUser');
const tokenService = require('./tokenService');
const { ensurePersonalSA } = require('./personalSAService');
const { ensureWorkSA } = require('./workSAService');
const { applyToUserData } = require('./deploymentDefaults');
const googleAuthService = require('./googleAuthService'); // reuse createOrUpdateUser
const oidcGroupMap = require('./oidcGroupMap');

class OidcAuthService {
  constructor() {
    this._jwks = null;          // memoized remote JWKS (createRemoteJWKSet)
    this._jwksUri = null;
  }

  _jose() {
    // jose is ESM; Node 24 supports require() of it. Lazy so the module loads
    // even in deployments where OIDC is disabled.
    return require('jose');
  }

  /**
   * Resolve the issuer's JWKS endpoint: explicit OIDC_JWKS_URI, else discovered
   * from /.well-known/openid-configuration. Fails loud if neither resolves.
   */
  async _resolveJwksUri() {
    if (envConfig.oidcJwksUri) return envConfig.oidcJwksUri;
    const issuer = envConfig.oidcIssuer;
    if (!issuer) throw new Error('OIDC_ISSUER is not configured');
    const discoveryUrl = `${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`;
    const resp = await fetch(discoveryUrl);
    if (!resp.ok) {
      throw new Error(`OIDC discovery failed (${resp.status}) at ${discoveryUrl}`);
    }
    const doc = await resp.json();
    if (!doc.jwks_uri) throw new Error('OIDC discovery document has no jwks_uri');
    return doc.jwks_uri;
  }

  /** Memoized remote JWKS keyset (rotates keys itself; cache is per-process). */
  async _getJwks() {
    if (this._jwks) return this._jwks;
    const { createRemoteJWKSet } = this._jose();
    const jwksUri = await this._resolveJwksUri();
    this._jwksUri = jwksUri;
    this._jwks = createRemoteJWKSet(new URL(jwksUri));
    return this._jwks;
  }

  /**
   * Verify an OIDC ID token: signature (issuer JWKS) + iss + aud + exp.
   * Returns the verified claims. Throws on any failure (never trusts an
   * unverified token).
   */
  async verifyIdToken(idToken) {
    if (!envConfig.oidcIssuer || !envConfig.oidcClientId) {
      throw new Error('OIDC is not configured (OIDC_ISSUER / OIDC_CLIENT_ID)');
    }
    const { jwtVerify } = this._jose();
    const jwks = await this._getJwks();
    const { payload } = await jwtVerify(idToken, jwks, {
      issuer: envConfig.oidcIssuer,
      audience: envConfig.oidcClientId,
    });
    if (!payload.email) {
      throw new Error('OIDC ID token has no email claim (request the "email" scope)');
    }
    return payload;
  }

  /**
   * Authenticate via a customer IdP ID token → JIT-provision + mint Citra JWT.
   * Mirrors googleAuthService.authenticateWithGoogle so both providers converge
   * on the same user store, SA creation, and enterprise token.
   */
  async authenticateWithOidc(idToken, reqMeta = {}) {
    const claims = await this.verifyIdToken(idToken);
    const email = String(claims.email).toLowerCase();

    const userData = {
      email,
      name: claims.name || claims.preferred_username || claims.given_name || '',
      oidcSubject: claims.sub,
      oidcIssuer: envConfig.oidcIssuer,
      authProvider: 'oidc',
      // Trust the IdP's email_verified when present; enterprise IdPs vouch for
      // their own directory, so absence defaults to verified.
      emailVerified: claims.email_verified !== false,
      lastLogin: new Date(),
      isActive: true,
    };

    if (reqMeta.termsAcceptedAt) {
      userData.gdpr_consent = {
        terms_accepted_at: new Date(reqMeta.termsAcceptedAt),
        privacy_accepted_at: new Date(reqMeta.termsAcceptedAt),
        consent_version: '1.0',
      };
    }

    let existingUser = await CitraAIUser.findOne({ email });
    const isNewUser = !existingUser;

    // SECURITY: refuse to let an OIDC login ASSUME a local-only account. The
    // break-glass super_admin is a LOCAL user, IdP-independent BY DESIGN — if its
    // email also exists in the customer IdP, anyone who can obtain an OIDC token
    // for that email would otherwise be minted a JWT carrying that account's
    // (possibly super_admin) roles, taking over the break-glass admin through the
    // very IdP it is meant to be isolated from. A shared email must go through
    // explicit local login, not SSO assumption.
    if (existingUser && existingUser.authProvider === 'local') {
      const e = new Error(
        'This email is a local (break-glass) account; sign in with email + password, not SSO.');
      e.statusCode = 409;
      e.code = 'LOCAL_ACCOUNT_NO_SSO';
      throw e;
    }

    // Deployment defaults stamp org_id + entity_type.
    applyToUserData(userData, existingUser, isNewUser);

    // ── Departments: from the IdP's groups when a map is configured ──────────
    // Two modes, and which one is active is a deployment decision:
    //
    // (a) GROUP MAP CONFIGURED — the customer's directory is the source of
    //     truth. dept_ids (and any mapped roles) are recomputed on EVERY login,
    //     so a transfer between departments follows the officer on their next
    //     sign-in and an offboarding at the IdP revokes access here. This is
    //     what makes embedding viable: officers arrive already entitled instead
    //     of needing a manual grant each.
    //
    // (b) NO GROUP MAP — the original zero-access JIT. applyToUserData defaults
    //     a NEW user's dept_ids to DEFAULT_DEPT_ID, which is wrong for customer
    //     SSO: a first-time user must land with NO department so an admin grants
    //     it explicitly. Force it empty.
    const groupGrant = oidcGroupMap.resolveFromClaims(claims);
    if (groupGrant) {
      userData.dept_ids = groupGrant.dept_ids;
      // Roles are a UNION with what the user already holds, minus nothing: the
      // map can add (e.g. decision-app-builder) but must never silently strip a
      // role an admin granted in Citra, and it can never grant super_admin
      // (rejected at map-load). Directory groups describe the officer's job;
      // they are not a complete statement of their platform privileges.
      const existingRoles = Array.isArray(existingUser?.roles) ? existingUser.roles : [];
      userData.roles = Array.from(new Set([...existingRoles, ...groupGrant.roles]));
      if (groupGrant.dept_ids.length === 0) {
        // Not an error — this is how revocation presents. Logged at warn
        // because it is also the #1 cause of "the app says not found".
        console.warn(
          `[OIDC] ${email} matched NO mapped group — resolving to zero access. ` +
          `claim="${groupGrant.claim}" groups_in_token=${groupGrant.groups_in_token} ` +
          `unmapped=${JSON.stringify(groupGrant.ignored.slice(0, 10))}`
        );
      } else {
        console.log(
          `[OIDC] ${email} groups→depts: matched=${JSON.stringify(groupGrant.matched)} ` +
          `dept_ids=${JSON.stringify(groupGrant.dept_ids)} ` +
          `roles=${JSON.stringify(userData.roles)}`
        );
      }
    } else if (isNewUser) {
      userData.dept_ids = [];
    }

    const user = await googleAuthService.createOrUpdateUser(userData, existingUser);

    if (isNewUser) {
      try {
        const usageTrackingService = require('./usageTrackingService');
        await usageTrackingService.initializeNewUser(user.email, user.email, 0);
      } catch (err) {
        console.warn('[OIDC] usage init failed (continuing):', err.message || err);
      }
    }

    // Idempotently ensure both Service Accounts (same as the Google path).
    try { await ensurePersonalSA(user); }
    catch (err) { console.warn('[OIDC] ensurePersonalSA failed (continuing):', err.message || err); }
    try { await ensureWorkSA(user); }
    catch (err) { console.warn('[OIDC] ensureWorkSA failed (continuing):', err.message || err); }

    const token = await tokenService.generateToken(user);
    return {
      user: user.toJSON(),
      token,
      tokenType: 'Bearer',
      expiresIn: envConfig.jwtExpiresIn || '7d',
      isNewUser,
      accessStatus: { hasAccess: true, user_type: user.user_type || 'paid' },
    };
  }
}

module.exports = new OidcAuthService();
