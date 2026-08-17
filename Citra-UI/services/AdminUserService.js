// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * AdminUserService — UI wrapper for /api/admin/users/* (Citra-User-Service).
 *
 * Used by the admin screens to:
 *   - list users in the admin's scope (dept / org)
 *   - run the preflight for "Delete user X"
 *   - submit the per-resource action picker
 *   - poll for completion
 *   - browse historical HandoffReports (Departures screen)
 *
 * Auth: piggybacks on authService.authenticatedFetchJson — every call
 * goes out with the user's JWT. Backend enforces dept/org/super scope.
 */

import { CONFIG } from '../config/config';
import authService from './authService';

const BASE = () => `${CONFIG.USER_SERVICE_URL}/api/admin/users`;
const CITRA_BASE = () => `${CONFIG.API_BASE_URL || CONFIG.WORKFLOW_SERVICE_URL || ''}`;


async function _json(url, options = {}) {
  return authService.authenticatedFetchJson(url, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
}


const AdminUserService = {
  /**
   * GET /api/admin/users?q=&dept_id=&limit=
   * Returns { users: [...] } scoped to the caller's admin role.
   */
  async listUsers({ q = '', deptId = '', limit = 50 } = {}) {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (deptId) params.set('dept_id', deptId);
    params.set('limit', String(limit));
    const url = `${BASE()}?${params.toString()}`;
    const res = await _json(url);
    return res.users || [];
  },

  /**
   * GET /api/admin/users/impersonation-candidates
   *
   * Super_admin-only. Returns demo personas grouped by demo tenant org
   * (Acme Cement, etc.) so the picker can render them per company.
   * Shape: { orgs: [{ id, name, domain, users: [...] }] }
   */
  async listImpersonationCandidates() {
    const url = `${BASE()}/impersonation-candidates`;
    const res = await _json(url);
    return res.orgs || [];
  },

  /**
   * POST /api/admin/users/:userId/preflight-delete
   *
   * Returns { request_id, target_email, resources: [...], service_accounts_user_admins }
   * Each resource row has: kind, resource_id, name, current_sa, sole_admin,
   * action (suggested default), action_target.
   */
  async preflightDelete(userId) {
    const url = `${BASE()}/${encodeURIComponent(userId)}/preflight-delete`;
    return _json(url, { method: 'POST', body: JSON.stringify({}) });
  },

  /**
   * POST /api/admin/users/:userId/delete
   *
   * Body:
   *   {
   *     request_id,
   *     resource_actions: [{kind, resource_id, action, action_target}],
   *     personal_content_policies: { documents: 'keep'|'transfer_to_admin'|'delete', ... },
   *   }
   * Returns { request_id, job_id, pending_resources, message }.
   */
  async submitDelete(userId, { requestId, resourceActions, personalContentPolicies }) {
    const url = `${BASE()}/${encodeURIComponent(userId)}/delete`;
    return _json(url, {
      method: 'POST',
      body: JSON.stringify({
        request_id: requestId,
        resource_actions: resourceActions,
        personal_content_policies: personalContentPolicies || {},
      }),
    });
  },

  /**
   * GET /api/admin/users/:userId/deletion-status?request_id=<>
   *
   * Returns the DeletionRequest doc for status polling.
   */
  async getDeletionStatus(userId, requestId) {
    const url = `${BASE()}/${encodeURIComponent(userId)}/deletion-status?request_id=${encodeURIComponent(requestId)}`;
    return _json(url);
  },

  /**
   * PATCH /api/admin/users/:userId
   * Update a user's roles, dept_ids, org_id, name, or isActive.
   * Backend enforces:
   *   - dept_admin can only assign their own depts
   *   - org_id change requires super_admin
   *   - role rank cannot exceed the caller's rank
   */
  async updateUser(userId, patch) {
    const url = `${BASE()}/${encodeURIComponent(userId)}`;
    const res = await _json(url, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
    return res.user || res;
  },

  /**
   * POST /api/admin/users/:userId/ensure-sas
   *
   * Idempotently provision the user's Personal + Work SAs and stamp
   * their ids on the user doc. Used by the "Fix Service Accounts"
   * button surfaced when a user reports the HomePanel banner that
   * their SAs are missing.
   *
   * Returns { user: {personal_sa_id, work_sa_id, ...}, provisioned: {personal, work} }.
   * 400 if the user has no org_id (admin must assign one first via PATCH).
   */
  async ensureServiceAccounts(userId) {
    const url = `${BASE()}/${encodeURIComponent(userId)}/ensure-sas`;
    return _json(url, { method: 'POST', body: JSON.stringify({}) });
  },

  /**
   * GET /api/depts — list depts for the caller's org (used by the
   * Edit-User modal multiselect).
   */
  async listDepts(orgId) {
    // super_admin may pass an orgId to read ANOTHER org's dept catalog (the
    // automation console drills into a specific org). Others: own org only.
    const q = orgId ? `?org_id=${encodeURIComponent(orgId)}` : '';
    const url = `${CONFIG.USER_SERVICE_URL}/api/depts${q}`;
    const res = await _json(url);
    return res.depts || [];
  },

  /**
   * GET /api/admin/orgs — orgs the caller can see. super_admin → all orgs;
   * org_admin/dept_admin → their own org only. Used by the halt console so a
   * super_admin can freeze one organization (e.g. acme-power) on a hub box.
   */
  async listOrgs() {
    const url = `${CONFIG.USER_SERVICE_URL}/api/admin/orgs`;
    const res = await _json(url);
    return res.orgs || [];
  },

  /**
   * GET /api/admin/service-accounts — list SAs the caller can manage,
   * used as the "transfer target" dropdown in the picker.
   */
  async listManageableServiceAccounts() {
    const url = `${CONFIG.USER_SERVICE_URL}/api/admin/service-accounts`;
    const res = await _json(url);
    return res.service_accounts || res.serviceAccounts || res.data || res;
  },

  /**
   * POST /api/admin/users/:userId/impersonation-token
   *
   * Mints a 1-hour HS256 session JWT for `userId`. The token carries the
   * target user's identity (org_id, dept_ids, roles) plus two audit
   * claims that are forwarded on the HS256 session token to
   * downstream services + MCPs:
   *   act              — the admin's email (RFC 8693 actor)
   *   impersonation_id — uuid linking to the impersonation_audit row
   *
   * super_admin only — backend returns 403 for org_admin / dept_admin.
   *
   * Returns { token, impersonation_id, expires_at, target_user }.
   */
  async mintImpersonationToken(userEmail, reason = '') {
    const url = `${BASE()}/${encodeURIComponent(userEmail)}/impersonation-token`;
    return _json(url, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  },

  /**
   * POST /api/admin/impersonations/:impersonationId/revoke
   *
   * Marks the audit row revoked_at. The minted JWT itself can't be
   * invalidated mid-flight (no per-request blocklist), but this records
   * intent + lets the UI surface "this session was cancelled" in audit
   * tooling.
   */
  async revokeImpersonation(impersonationId) {
    const url = `${CONFIG.USER_SERVICE_URL}/api/admin/impersonations/${encodeURIComponent(impersonationId)}/revoke`;
    return _json(url, { method: 'POST', body: JSON.stringify({}) });
  },

  /**
   * GET /api/handoff-reports?org_id=&since=
   *
   * Departures screen reads from citra-workflow (or wherever the
   * Workflows DB sits). HandoffReports collection currently has no
   * dedicated endpoint; this is a stub that hits a future surface
   * (citra-service /api/handoff-reports). If the endpoint is missing
   * the screen will degrade gracefully.
   */
  async listHandoffReports({ orgId = '', since = '' } = {}) {
    const params = new URLSearchParams();
    if (orgId) params.set('org_id', orgId);
    if (since) params.set('since', since);
    const url = `${CITRA_BASE()}/api/handoff-reports?${params.toString()}`;
    try {
      const res = await _json(url);
      return res.reports || res.handoff_reports || [];
    } catch {
      return [];
    }
  },
};

export default AdminUserService;
