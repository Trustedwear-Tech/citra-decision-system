// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * DeptSourceService.js — admin client for the MCP data-plane audit feed.
 *
 * The dept_sources registry CRUD was removed (2026-07-10): sources are now
 * defined in the MCP's SOURCES_FILE + the discovery registry, not managed from
 * the UI. What remains is the read-only Operational Data Flow audit
 * (dept_query_audit), served by citra-workflow at /api/dept-sources/query-audit
 * (route prefix kept for stability).
 */
import authService from './authService';
import { WORKFLOW_API_BASE } from '../config/config';

class DeptSourceService {
  constructor() {
    this.baseURL = `${WORKFLOW_API_BASE}/api/dept-sources`;
    this.defaultTimeout = 30000;
  }

  _headers() {
    return { 'Content-Type': 'application/json' };
  }

  async _fetch(url, options = {}, timeout = this.defaultTimeout) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      return await authService.authenticatedFetch(url, {
        ...options,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  async _json(resp, fallbackError) {
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || fallbackError);
    }
    return resp.json();
  }

  // ── MCP data-plane audit (dept_query_audit) ─────────────────────────
  // Who queried / wrote which source via the dept MCPs. `deptId` filters on
  // the OWNING dept of the data (source_dept_id), not the user's dept.

  async listQueryAudit({ orgId, deptId, sourceId, op, deniedOnly, limit } = {}) {
    const params = new URLSearchParams();
    if (orgId)  params.set('org_id', orgId);
    if (deptId) params.set('dept_id', deptId);
    if (sourceId) params.set('source_id', sourceId);
    if (op && op !== 'all') params.set('op', op);
    if (deniedOnly) params.set('denied_only', 'true');
    if (limit) params.set('limit', String(limit));
    const qs = params.toString();
    const resp = await this._fetch(`${this.baseURL}/query-audit${qs ? `?${qs}` : ''}`, {
      method: 'GET', headers: this._headers(),
    });
    return this._json(resp, 'Failed to load query audit');
  }
}

export default new DeptSourceService();
