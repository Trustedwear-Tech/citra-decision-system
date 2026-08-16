// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const express = require('express');
const cors = require('cors');
const { rateLimit, ipKeyGenerator } = require('express-rate-limit');
const { loadVaultSecrets } = require('./src/config/vault-env-loader');
const { connectToMongoDB } = require('./src/config/mongodb');
const { initSentry, traceIdMiddleware, sentryErrorHandler } = require('./src/config/observability');

// Error tracker (GlitchTip / Sentry) — must init before Express app so
// instrumentation patches http before any route is hit.
initSentry();

// Load environment variables before starting the app
async function startServer() {
  try {
    // Load vault secrets first
    await loadVaultSecrets();
    
    // Connect to MongoDB if connection string is provided
    if (process.env.MONGODB_CONNECTION_STRING) {
      try {
        await connectToMongoDB();

        // Initialize pricing cache after MongoDB connection
        const usageTrackingService = require('./src/services/usageTrackingService');
        await usageTrackingService.initializePricingCache();
        console.log('✅ Pricing cache initialized successfully');

        // Multi-tenant org bootstrap: upsert orgs from the deployment's
        // seed file. Orgs are the parent containers for depts/users and must
        // exist before deptSeed runs. Safe to run every boot — idempotent.
        try {
          const { seedOrgs } = require('./src/services/orgSeedService');
          await seedOrgs();
        } catch (orgErr) {
          console.warn('[orgSeed] failed (continuing):', orgErr.message);
        }

        // Single-tenant dept bootstrap: upsert depts from the deployment's
        // seed file. Safe to run every boot — idempotent.
        try {
          const { seedDepts } = require('./src/services/deptSeedService');
          await seedDepts();
        } catch (deptErr) {
          console.warn('[deptSeed] failed (continuing):', deptErr.message);
        }
      } catch (mongoError) {
        console.warn('MongoDB connection or pricing initialization failed, but server will continue:', mongoError.message);
      }

      // Single-customer boot guard: ORG_ID must resolve to a real `orgs` row.
      // A typo'd ORG_ID otherwise silently stamps every UI-login user into a
      // phantom org. Deliberately OUTSIDE the swallow-and-continue mongo try
      // above so a mismatch propagates to the outer catch → process.exit(1)
      // (RULE: fail loud, fail early — a wrong tenant identity is not a
      // "continue anyway" condition).
      const { validateDeploymentOrg } = require('./src/services/validateDeploymentOrg');
      await validateDeploymentOrg();

      // OIDC group→dept map: parse it and assert every dept it can grant
      // actually exists in this org's catalogue. Same fail-loud posture as the
      // org guard above, and for the same reason — a map pointing at a dept
      // that was never seeded is invisible at login time (the officer just
      // gets "app not found"), so it has to fail in front of whoever deploys.
      // Deliberately AFTER seedDepts so the catalogue is populated.
      const oidcGroupMap = require('./src/services/oidcGroupMap');
      const groupMapStatus = await oidcGroupMap.validateDeptsExist(
        (process.env.ORG_ID || '').trim()
      );
      if (groupMapStatus.enabled) {
        console.log(
          `[oidcGroupMap] ✅ loaded — ${groupMapStatus.checked} dept(s) referenced, all present`
        );
      }
    } else {
      console.log('MongoDB connection string not provided, skipping MongoDB connection');
    }
    
    const app = express();
    const PORT = process.env.PORT || 3000;

    // Trust proxy (needed for rate-limit behind reverse proxy / Docker)
    app.set('trust proxy', 1);

    // Per-request trace_id (correlates Sentry issues ↔ Loki logs)
    app.use(traceIdMiddleware);

    // ── Rate Limiting ──

    // 1. Global per-IP limit — generous ceiling for DDoS protection
    const generalLimiter = rateLimit({
      windowMs: 15 * 60 * 1000, // 15 minutes
      max: 300,                  // 300 requests per 15 min per IP (generous)
      standardHeaders: true,
      legacyHeaders: false,
      message: { error: 'Too many requests from this IP, please try again later.' }
    });

    // 2. Auth limiter — per-user so different users behind the same IP
    //    (office, VPN, localhost) are tracked independently.
    //    Key priority: email (access-token flow) → device fingerprint → IP fallback.
    const authLimiter = rateLimit({
      windowMs: 15 * 60 * 1000,
      max: 20,                   // 20 auth attempts per 15 min per user
      standardHeaders: true,
      legacyHeaders: false,
      validate: { keyGeneratorIpFallback: false }, // Custom key; suppress IPv6 warning
      keyGenerator: (req) => {
        const email = req.body?.googleUserInfo?.email   // access-token flow
                   || req.body?.email                     // direct email field
                   || null;
        if (email) return `auth:${email}`;

        // ID-token flow: no email in body yet; use device fingerprint if available
        const fp = req.body?.deviceFingerprint || null;
        if (fp) return `auth:fp:${fp}`;

        // Final fallback: normalised IP via the official helper
        return `auth:ip:${ipKeyGenerator(req)}`;
      },
      message: { error: 'Too many authentication attempts for this account, please try again later.' }
    });

    // Apply global per-IP rate limit to all routes
    app.use(generalLimiter);

    // Middleware — CORS
    const allowedOrigins = process.env.CORS_ALLOWED_ORIGINS
      ? process.env.CORS_ALLOWED_ORIGINS.split(',').map(o => o.trim())
      : [
          'http://localhost:8081',
          'http://localhost:8082',
          'http://localhost:8083',
          'http://localhost:3000',
          'http://localhost:19006',
        ];

    if (process.env.NODE_ENV === 'development') {
      app.use(cors({
        origin: allowedOrigins,
        credentials: true,
        methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
        allowedHeaders: [
          'Content-Type', 
          'Authorization', 
          'X-Requested-With',
          'X-Device-ID',
          'x-device-id'
        ]
      }));
      console.log('🌐 CORS enabled for development');
    } else {
      // Production: allow only configured origins (or the defaults + production domain)
      const prodOrigins = process.env.CORS_ALLOWED_ORIGINS
        ? allowedOrigins
        : ['https://citra-ai.com'];
      app.use(cors({
        origin: prodOrigins,
        credentials: true,
        methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
        allowedHeaders: [
          'Content-Type', 
          'Authorization', 
          'X-Requested-With',
          'X-Device-ID',
          'x-device-id'
        ]
      }));
      console.log('🔒 CORS enabled for production with allowed origins:', prodOrigins.join(', '));
    }

    // ── Webhook routes MUST be registered BEFORE express.json() ──
    // Razorpay webhook needs the raw body bytes for HMAC signature verification.
    // express.json() would parse the body and re-serialise it, breaking the signature.
    app.use('/api', require('./src/routes/webhookRoutes'));

    app.use(express.json({ limit: '1mb' }));
    app.use(express.urlencoded({ extended: true, limit: '1mb' }));

    // Health check route — exposes the deployment's ORG_ID so a fleet check
    // can diff it against data-discovery / dept-MCPs and catch org drift.
    app.get('/health', (req, res) => {
      res.status(200).json({
        status: 'OK',
        message: 'Server is running',
        org_id: (process.env.ORG_ID || '').trim() || null,
      });
    });

    // Import and use route modules
    // REMOVED: Missing route files (were Azure Functions, not converted to Express routes yet)
    // app.use('/api', require('./src/routes/deviceRoutes'));
    // app.use('/api', require('./src/routes/userRoutes'));
    // app.use('/api', require('./src/routes/dataRoutes'));
    // app.use('/api', require('./src/routes/authRoutes'));
    // app.use('/api', require('./src/routes/nomineeRoutes'));
    // app.use('/api', require('./src/routes/sosRoutes'));
    
    // Active route modules (Express routes)
    app.use('/api/subscriptions', require('./src/routes/subscriptionRoutes')); // Credit-based billing routes (renamed from subscription)
    // NOTE: emailLimiter is applied inside emailRoutes.js to specific endpoints,
    // NOT here at mount level, to avoid rate-limiting all /api/* routes.
    app.use('/api', require('./src/routes/emailRoutes'));

    // Service-account admin routes — named groups with explicit membership
    // that own Citra resources (workflows, smart apps, connections).
    // Auth: Bearer JWT; RBAC handled per-route.
    app.use('/api/admin/service-accounts', require('./src/routes/serviceAccountRoutes'));

    // User-admin routes — deactivate/reactivate users + trigger the
    // resource-inheritance fan-out (USER_DEACTIVATED → Citra-Worker).
    app.use('/api/admin/users', require('./src/routes/userAdminRoutes'));

    // Org-admin routes — CRUD over the tenant catalog. super_admin owns
    // create/update/delete; org_admin can only view their own org.
    app.use('/api/admin/orgs', require('./src/routes/orgAdminRoutes'));

    // Impersonation audit routes — surface over `impersonation_audit`.
    // Mints happen at /api/admin/users/:userId/impersonation-token; this
    // mount owns list/get/revoke of audit rows. super_admin-only.
    app.use('/api/admin/impersonations', require('./src/routes/impersonationAuditRoutes'));

    // Dept catalog. Read at /api/depts (any authed user, scoped to their
    // org). super_admin write at /api/admin/depts — used by demo-data's
    // seed_tenant.py to provision tenant-scoped depts without touching
    // user-service config or Mongo directly.
    const deptRoutes = require('./src/routes/deptRoutes');
    app.use('/api/depts', deptRoutes);
    app.use('/api/admin/depts', deptRoutes.adminRouter);

    // Self-service Personal SA endpoint — idempotent get-or-create.
    // UI / builder pods call POST /api/auth/me/personal-sa before
    // publishing a workflow / smart-app under the user's identity.
    app.use('/api/auth/me', require('./src/routes/selfServiceAccountRoutes'));
    // webhookRoutes already registered above (before express.json for raw body handling)

    // ── Auth provider configuration (config-driven) ──
    const envConfig = require('./src/config/environment');
    const enabledProviders = envConfig.authProviders; // e.g. ['google'], ['local'], or ['google','local']
    console.log(`🔐 Auth providers enabled: ${enabledProviders.join(', ')}`);

    // Public endpoint so UI can discover which auth methods are available
    app.get('/api/auth/providers', (req, res) => {
      res.json({ success: true, providers: enabledProviders });
    });

    // /me, /users, /logout, /send-welcome-email, /delete-account are
    // provider-agnostic (JWT + user lookup) and must work regardless of
    // which provider a session authenticated through -- mounted
    // unconditionally, not gated on AUTH_PROVIDERS. See accountRoutes.js.
    app.use('/api/auth', authLimiter, require('./src/routes/accountRoutes'));

    if (enabledProviders.includes('google')) {
      app.use('/api/auth', authLimiter, require('./src/routes/googleAuthRoutes')); // Google Authentication routes
      console.log('  ✅ Google OAuth routes mounted at /api/auth');
    }

    if (enabledProviders.includes('local')) {
      app.use('/api/auth/local', authLimiter, require('./src/routes/localAuthRoutes')); // Email/password auth routes
      console.log('  ✅ Local auth routes mounted at /api/auth/local');
    }

    if (enabledProviders.includes('oidc')) {
      app.use('/api/auth/oidc', authLimiter, require('./src/routes/oidcAuthRoutes')); // Per-customer enterprise SSO (OIDC)
      console.log('  ✅ OIDC routes mounted at /api/auth/oidc');
    }

    app.use('/api/coupons', require('./src/routes/couponRoutes')); // Coupon routes
    app.use('/api/users', require('./src/routes/couponRoutes')); // User access check routes

    // Error handling middleware
    app.use((err, req, res, next) => {
      // Client aborted the request (e.g. user closed tab during payment verification)
      // The Razorpay webhook will handle credit addition — this is harmless noise
      if (err.type === 'request.aborted' || err.code === 'ECONNABORTED') {
        console.warn(`⚠️ Client aborted request: ${req.method} ${req.url}`);
        return;
      }
      console.error('Error:', err);
      res.status(500).json({ 
        error: 'Internal Server Error',
        message: process.env.NODE_ENV === 'development' ? err.message : 'Something went wrong'
      });
    });

    // 404 handler
    app.use('*', (req, res) => {
      res.status(404).json({ error: 'Endpoint not found' });
    });

    // Sentry error handler — capture unhandled errors before Express
    // returns a 500. Must be registered after all routes.
    app.use(sentryErrorHandler);

    app.listen(PORT, () => {
      console.log(`🚀 Server is running on port ${PORT}`);
      console.log(`📍 Health check: http://localhost:${PORT}/health`);
      console.log(`🔐 Environment: ${process.env.NODE_ENV || 'development'}`);
    });

    // REMOVED: Background scheduler job (activatePendingSubscriptions) - intentionally removed
    // No longer using scheduled subscription activation

    return app;
  } catch (error) {
    console.error('Failed to start server:', error);
    process.exit(1);
  }
}

// Start the server only if this file is run directly (not required as a module)
if (require.main === module) {
  startServer().catch(error => {
    console.error('💥 Failed to start server:', error.message);
    console.error('Stack trace:', error.stack);
    process.exit(1);
  });
}

module.exports = { startServer };
