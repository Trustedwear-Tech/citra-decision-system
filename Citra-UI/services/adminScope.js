// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
