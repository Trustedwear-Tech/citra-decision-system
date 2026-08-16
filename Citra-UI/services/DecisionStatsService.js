/**
 * DecisionStatsService — admin client for the org-wide Success Rate card.
 *
 * Backend (smart-app-service, docs/adoption-metrics-precedent-citation-plan.md §1):
 *   GET /org/decision-stats?period=week|month|all
 *     → { totals: {accepted, accepted_with_changes, rejected, automated,
 *         pending, acceptance_rate}, apps: [...], memory_lift: {...} }
 *
 * Read-only aggregation over the decision ledger; the server gates access
 * (org/dept/super admin) — the UI only decides what to render.
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

export async function getOrgDecisionStats(period = 'month') {
  return _get(`${_base()}/org/decision-stats?period=${encodeURIComponent(period)}`);
}

// Per-app Learning Report (GET /apps/{slug}/loop-metrics — extended with
// time_to_decision, top_overridden_fields, memory_lift, correction_absorption).
export async function getAppLoopMetrics(slug, days = 90) {
  return _get(`${_base()}/apps/${encodeURIComponent(slug)}/loop-metrics?days=${days}`);
}

// Money impact — the canonical ROI spine (docs/money-saved-roi-plan.md V4).
// Sums of poller-stamped outcome.value amounts; the server echoes the
// definition versions + attribution rules so every headline is defensible.
export async function getOrgValueStats(period = 'month') {
  return _get(`${_base()}/org/value-stats?period=${encodeURIComponent(period)}`);
}

export async function getAppValueStats(slug, period = 'month') {
  return _get(`${_base()}/apps/${encodeURIComponent(slug)}/value-stats?period=${encodeURIComponent(period)}`);
}

export default { getOrgDecisionStats, getAppLoopMetrics, getOrgValueStats, getAppValueStats };
