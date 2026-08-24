// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * observability.js — Sentry-SDK init for Citra-User-Service.
 *
 * Activates only when SENTRY_DSN is set (no-op in dev).
 * Emits every unhandled exception + unhandled rejection to GlitchTip/Sentry
 * and attaches a per-request trace_id that is echoed in the X-Trace-Id
 * response header for correlation with Loki logs.
 */

const crypto = require('crypto');

let Sentry = null;
let _initialised = false;

function initSentry() {
  if (_initialised) return Sentry;
  _initialised = true;

  const dsn = (process.env.SENTRY_DSN || '').trim();
  if (!dsn) {
    console.log('[sentry] SENTRY_DSN not set — skipping init for user-service');
    return null;
  }

  try {
    Sentry = require('@sentry/node');
    Sentry.init({
      dsn,
      environment: process.env.ENVIRONMENT || 'prod',
      release: process.env.GIT_SHA || process.env.SERVICE_VERSION || 'unknown',
      serverName: process.env.HOSTNAME || 'user-service',
      tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE || '0.05'),
      sendDefaultPii: false,
      maxBreadcrumbs: 50,
      initialScope: {
        tags: {
          service: 'user-service',
          ...(process.env.ORG_ID  ? { org:  process.env.ORG_ID  } : {}),
          ...(process.env.DEPT_ID ? { dept: process.env.DEPT_ID } : {}),
        },
      },
    });
    console.log('[sentry] initialised for user-service');
    return Sentry;
  } catch (e) {
    console.warn('[sentry] init failed:', e.message);
    return null;
  }
}

/**
 * Express middleware — assigns a trace_id per request, tags the Sentry
 * scope, and sets the X-Trace-Id response header.
 */
function traceIdMiddleware(req, res, next) {
  const incoming = req.headers['x-trace-id'];
  const traceId = incoming || crypto.randomUUID().replace(/-/g, '');
  req.traceId = traceId;
  res.setHeader('X-Trace-Id', traceId);
  if (Sentry) {
    Sentry.getCurrentScope().setTag('trace_id', traceId);
  }
  next();
}

/**
 * Express error handler — capture unhandled errors to Sentry, then fall
 * through to the default Express error response.
 */
function sentryErrorHandler(err, req, res, next) {
  if (Sentry) {
    Sentry.withScope((scope) => {
      if (req.traceId) scope.setTag('trace_id', req.traceId);
      Sentry.captureException(err);
    });
  }
  next(err);
}

module.exports = { initSentry, traceIdMiddleware, sentryErrorHandler };
