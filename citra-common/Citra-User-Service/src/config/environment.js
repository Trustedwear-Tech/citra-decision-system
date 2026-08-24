// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

// Environment configuration module
// This module should be imported at the top of any file that needs environment variables

/**
 * Environment Configuration
 * 
 * This module centralizes environment variable access and provides
 * type-safe access to configuration values.
 */

class EnvironmentConfig {



  // AWS SES Configuration
  get awsAccessKeyId() { return process.env.AWS_ACCESS_KEY_ID; }
  get awsSecretAccessKey() { return process.env.AWS_SECRET_ACCESS_KEY; }
  get awsRegion() { return process.env.AWS_REGION || 'ap-south-1'; }
  get defaultFromEmail() { return process.env.EMAIL_DEFAULT_SENDER || 'info@citra-ai.com'; }

  // Email Provider Configuration — 'ses' or 'smtp' (explicit, no fallback)
  get emailProvider() { return process.env.EMAIL_PROVIDER; } // 'ses' or 'smtp'

  // SMTP Configuration (used when EMAIL_PROVIDER=smtp)
  get smtpHost() { return process.env.SMTP_HOST; }
  get smtpPort() { return parseInt(process.env.SMTP_PORT || '587', 10); }
  get smtpSecure() { return process.env.SMTP_SECURE === 'true'; }
  get smtpUser() { return process.env.SMTP_USER; }
  get smtpPass() { return process.env.SMTP_PASS; }

  // Auth Providers Configuration — comma-separated: 'google', 'local'
  get authProviders() {
    const raw = process.env.AUTH_PROVIDERS || 'google';
    return raw.split(',').map(p => p.trim().toLowerCase()).filter(Boolean);
  }

  isAuthProviderEnabled(provider) {
    return this.authProviders.includes(provider.toLowerCase());
  }

  // App URL for email links (verification, password reset)
  get appUrl() { return process.env.APP_URL || 'http://localhost:8081'; }

  // Razorpay configuration removed — license model

  // Admin Configuration
  get adminEmails() {
    const raw = process.env.ADMIN_EMAILS || '';
    return raw.split(',').map(e => e.trim().toLowerCase()).filter(Boolean);
  }

  isAdmin(email) {
    if (!email) return false;
    return this.adminEmails.includes(email.toLowerCase());
  }


  // Redis Configuration
  get redisHost() { return process.env.REDIS_HOST || 'localhost'; }
  get redisPort() { return process.env.REDIS_PORT || '6379'; }
  get redisPassword() { return process.env.REDIS_PASSWORD; }

  // Server Configuration
  get port() { return process.env.PORT || '3000'; }
  get nodeEnv() { return process.env.NODE_ENV || 'development'; }

  // Vault Configuration
  get vaultAddr() { return process.env.VAULT_ADDR; }
  get vaultToken() { return process.env.VAULT_TOKEN; }
  get vaultSecretPath() { return process.env.VAULT_SECRET_PATH; }

  // Google Authentication Configuration
  get googleClientSecret() { return process.env.GOOGLE_CLIENT_SECRET; }
  get googleClientIds() {
    return [
      process.env.GOOGLE_CLIENT_ID_WEB
    ].filter(Boolean); // Remove any undefined values
  }

  // OIDC (per-customer enterprise SSO) — active only when AUTH_PROVIDERS
  // includes 'oidc'. ONE IdP per single-tenant deployment: the customer's IdP
  // issues the ID token; we verify its signature against the issuer's JWKS and
  // require audience == our client_id. The customer's client_id/issuer live
  // ONLY in that deployment's config (private to the company).
  get oidcIssuer() { return process.env.OIDC_ISSUER; }              // e.g. https://login.customer.com/  (or /realms/acme)
  get oidcClientId() { return process.env.OIDC_CLIENT_ID; }         // expected `aud` of the ID token
  // Optional explicit JWKS endpoint; when unset it is discovered from the
  // issuer's /.well-known/openid-configuration.
  get oidcJwksUri() { return process.env.OIDC_JWKS_URI; }

  // MongoDB Configuration
  get mongodbConnectionString() { return process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI || null; }
  get mongodbDatabase() { return process.env.MONGODB_DATABASE || 'citra-ai'; }

  // Backwards-compatible alias used in some files
  get mongodbConnString() { return this.mongodbConnectionString; }

  // JWT Configuration
  get jwtSecret() { return process.env.JWT_SECRET; }
  get jwtExpiresIn() { return process.env.JWT_EXPIRES_IN || '7d'; }

  // Citra-Service (Python core) base URL — used for cross-service calls
  // (account deletion, admin content summary/apply). Required: NO localhost
  // fallback (RULE #1). A missing value must fail loud rather than silently
  // hit a dev host (or, worse, the wrong port). Set CITRA_SERVICE_URL in .env
  // (local dev) or Vault (prod). MAIN_SERVICE_URL kept as a legacy alias.
  get citraServiceUrl() {
    const url = process.env.CITRA_SERVICE_URL || process.env.MAIN_SERVICE_URL;
    if (!url) {
      throw new Error(
        'CITRA_SERVICE_URL is not set — refusing to fall back to localhost. ' +
        'Set it in .env (local dev) or Vault (prod).'
      );
    }
    return url;
  }

  // Welcome Bonus Configuration
  get welcomeBonusAmount() { return parseFloat(process.env.WELCOME_BONUS_AMOUNT) || 100; }

  /**
   * Validate that all required environment variables are present
   * @param {Array<string>} requiredVars - Array of required environment variable names
   * @throws {Error} If any required variables are missing
   */
  validateRequired(requiredVars = []) {
    const missing = requiredVars.filter(varName => !process.env[varName]);
    
    if (missing.length > 0) {
      throw new Error(`Missing required environment variables: ${missing.join(', ')}`);
    }
  }


  /**
   * Get AWS SES configuration object
   */
  getAwsSesConfig() {
    return {
      accessKeyId: this.awsAccessKeyId,
      secretAccessKey: this.awsSecretAccessKey,
      region: this.awsRegion
    };
  }

  /**
   * Get Redis configuration object
   */
  getRedisConfig() {
    return {
      host: this.redisHost,
      port: parseInt(this.redisPort, 10),
      password: this.redisPassword
    };
  }

  /**
   * Check if we're in development mode
   */
  isDevelopment() {
    return this.nodeEnv === 'development';
  }

  /**
   * Check if we're in production mode
   */
  isProduction() {
    return this.nodeEnv === 'production';
  }
}

// Create and export a singleton instance
const envConfig = new EnvironmentConfig();

module.exports = envConfig;
