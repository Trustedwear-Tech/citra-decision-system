// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * sentry.js — Error tracker for Citra-UI (React Native / Expo Web).
 *
 * Activates only when EXPO_PUBLIC_SENTRY_DSN is set (no-op in dev/tests).
 * Captures:
 *   • Unhandled JS errors + promise rejections
 *   • React component errors via ErrorBoundary (see App.js wrap below)
 *   • Last N user actions (nav, button taps) as breadcrumbs
 *
 * Correlation:
 *   When the app makes API calls, we attach `X-Trace-Id` to each request
 *   (see attachTraceId in this module). The same id ends up in Sentry as a
 *   tag, in the backend Sentry event, and in Loki logs — so a single user
 *   report can be traced across the whole stack.
 */

import Constants from 'expo-constants';

let Sentry = null;
let _initialised = false;

export function initSentry() {
  if (_initialised) return Sentry;
  _initialised = true;

  const dsn =
    process.env.EXPO_PUBLIC_SENTRY_DSN ||
    Constants?.expoConfig?.extra?.sentryDsn ||
    '';

  if (!dsn) {
    if (__DEV__) {
      // eslint-disable-next-line no-console
      console.log('[sentry] EXPO_PUBLIC_SENTRY_DSN not set — skipping init');
    }
    return null;
  }

  try {
    // eslint-disable-next-line global-require
    Sentry = require('@sentry/react-native');
    Sentry.init({
      dsn,
      environment: process.env.EXPO_PUBLIC_ENVIRONMENT || (__DEV__ ? 'dev' : 'prod'),
      release: Constants?.expoConfig?.version || 'unknown',
      dist: String(Constants?.expoConfig?.runtimeVersion || '1'),
      tracesSampleRate: Number(process.env.EXPO_PUBLIC_SENTRY_TRACES_SAMPLE_RATE || '0.05'),
      // Replays / profiling off to keep the bundle small; enable later if needed.
      enableAutoSessionTracking: true,
      sendDefaultPii: false,
      attachStacktrace: true,
      maxBreadcrumbs: 50,
    });
    return Sentry;
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('[sentry] init failed:', e?.message);
    return null;
  }
}

/** Attach user identity once the user logs in. */
export function setSentryUser(user) {
  if (!Sentry || !user) return;
  try {
    Sentry.setUser({
      id: user.user_id || user.email,
      email: user.email,
      org_id: user.org_id,
      entity_type: user.entity_type,
    });
  } catch (_e) { /* ignore */ }
}

export function clearSentryUser() {
  if (!Sentry) return;
  try { Sentry.setUser(null); } catch (_e) { /* ignore */ }
}

/**
 * Generate a trace id + tag it on Sentry. Call this right before an API
 * request and forward the returned value as the `X-Trace-Id` header.
 */
export function newTraceId() {
  const id =
    (globalThis.crypto && globalThis.crypto.randomUUID && globalThis.crypto.randomUUID().replace(/-/g, '')) ||
    Math.random().toString(16).slice(2) + Date.now().toString(16);
  if (Sentry) {
    try { Sentry.setTag('trace_id', id); } catch (_e) { /* ignore */ }
  }
  return id;
}

/**
 * Higher-order component that wraps the root App with a Sentry error
 * boundary, so React render errors become captured events instead of
 * white screens.
 */
export function wrap(App) {
  if (Sentry && typeof Sentry.wrap === 'function') {
    try { return Sentry.wrap(App); } catch (_e) { /* ignore */ }
  }
  return App;
}

export { Sentry };
