// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * ScreeningHealthService — admin client for the fraud Screening Health panel.
 *
 * Backend (smart-app-service, docs/fraud-screening-admin-panel-plan.md):
 *   GET /org/screening-stats?period=week|month|all      (org rollup — the card)
 *   GET /apps/{slug}/screening-stats?period=...          (per-app drill-down)
 *
 * Both are read-only aggregations over officer-judged screenings; the server
 * gates access (org/dept/super admin for the rollup; app-edit rights for the
 * drill-down) — the UI only decides what to render.
 */

import { CONFIG } from '../config/config';
import authService from './authService';

function _base() {
  return (CONFIG.urls?.smartAppService || CONFIG.SMART_APP_SERVICE_URL || '').replace(/\/$/, '');
}

async function _get(url) {
  const resp = await authService.authenticatedFetch(url, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!resp.ok) {
    let detail = '';
    try { detail = (await resp.json())?.detail?.message || ''; } catch { /* text body */ }
    const err = new Error(detail || `Request failed (${resp.status})`);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

export async function getOrgScreeningStats(period = 'month') {
  return _get(`${_base()}/org/screening-stats?period=${encodeURIComponent(period)}`);
}

export async function getAppScreeningStats(slug, period = 'month') {
  return _get(`${_base()}/apps/${encodeURIComponent(slug)}/screening-stats?period=${encodeURIComponent(period)}`);
}

export default { getOrgScreeningStats, getAppScreeningStats };
