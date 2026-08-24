// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * DiscoveryService.js — read-only view of registered MCP tools
 *
 * Uses the Citra-Service proxy at /api/dept-sources/_discovery/tools so the
 * UI doesn't talk to discovery-service directly (avoids extra CORS + lets
 * the proxy apply role checks).
 */
import authService from './authService';
import { WORKFLOW_API_BASE } from '../config/config';
import { buildApiError } from './apiError';

class DiscoveryService {
  constructor() {
    // /api/dept-sources moved into citra-workflow as part of the Phase J3 split.
    this.baseURL = `${WORKFLOW_API_BASE}/api/dept-sources/_discovery`;
    this.defaultTimeout = 15000;
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

  async listTools({ activeOnly = true } = {}) {
    const resp = await this._fetch(
      `${this.baseURL}/tools?active_only=${activeOnly ? 'true' : 'false'}`,
      { method: 'GET', headers: { 'Content-Type': 'application/json' } },
    );
    // Surface the server's reason — the proxy forwards discovery's 403 detail
    // (e.g. "…role required"). A generic message would throw that away and put
    // the operator back to guessing.
    if (!resp.ok) throw await buildApiError(resp, `Failed to list MCP tools (${resp.status})`);
    return resp.json();
  }
}

export default new DiscoveryService();
