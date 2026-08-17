// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * adminScope — role-derived admin scope for the current user.
 */
import { authService } from './authService';


/**
 * Highest admin-scope the current user has, or null. Used by list
 * surfaces to decide whether to show an admin/audit tab.
 *   'platform' — super_admin
 *   'org'      — org_admin
 *   'dept'     — dept_admin
 */
export function highestAdminScopeForCurrentUser() {
  const u = authService.getCurrentUser?.() || {};
  const roles = Array.isArray(u.roles) ? u.roles : (u.roles ? [u.roles] : []);
  if (roles.includes('super_admin')) return 'platform';
  if (roles.includes('org_admin')) return 'org';
  if (roles.includes('dept_admin')) return 'dept';
  return null;
}
