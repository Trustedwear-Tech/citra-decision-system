// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Deployment defaults — what enterprise-context fields every user gets
 * when their slot was previously empty.
 *
 * Citra is sold as a platform: the deployment's home org is `citra-ai`
 * (set via process.env.ORG_ID) and there is exactly ONE dept for all
 * Citra employees — `citra-software`. Demo tenants (acme-cement, etc.)
 * have their own orgs + their own depts, seeded via demo-data scripts;
 * those users are NEVER created via this auth-defaults path because
 * they can't log in directly (impersonation-only).
 *
 * Anyone who logs in via the UI is therefore a Citra user and gets:
 *   org_id      = ORG_ID env (citra-ai)
 *   dept_ids    = [citra-software]
 *   entity_type = company
 *
 * The functions below are idempotent: they only set a field when it's
 * currently empty. Demo personas seeded with explicit org_id/dept_ids
 * are left untouched.
 */

const DEFAULT_DEPT_ID = 'citra-software';
const DEFAULT_ENTITY_TYPE = 'company';

function deploymentOrgId() {
  return (process.env.ORG_ID || '').trim() || null;
}

/**
 * Mutate a userData object (about to be passed into createOrUpdate)
 * to fill in deployment defaults that are currently missing.
 *
 * @param {Object} userData    — staged user fields for create/update
 * @param {Object|null} existing — current DB doc (null on signup)
 * @param {boolean} isNew      — true when about to insert
 */
function applyToUserData(userData, existing, isNew) {
  const orgId = deploymentOrgId();
  if (!orgId) return userData;

  if (isNew || !existing || !existing.org_id) {
    userData.org_id = orgId;
  }
  const hasDept = Array.isArray(existing?.dept_ids) && existing.dept_ids.length > 0;
  if (isNew || !hasDept) {
    userData.dept_ids = [DEFAULT_DEPT_ID];
  }
  if (isNew || !existing?.entity_type || existing.entity_type === 'general') {
    userData.entity_type = DEFAULT_ENTITY_TYPE;
  }
  return userData;
}

/**
 * Same idea but applied to a Mongoose user doc that's already loaded.
 * Used on the login + /me self-heal paths where we hold the doc, not
 * staged userData. Returns true when any field was changed so the
 * caller can save() conditionally.
 */
function applyToUserDoc(user) {
  const orgId = deploymentOrgId();
  if (!orgId || !user) return false;
  let changed = false;

  if (!user.org_id) {
    user.org_id = orgId;
    changed = true;
  }
  if (!Array.isArray(user.dept_ids) || user.dept_ids.length === 0) {
    user.dept_ids = [DEFAULT_DEPT_ID];
    changed = true;
  }
  if (!user.entity_type || user.entity_type === 'general') {
    user.entity_type = DEFAULT_ENTITY_TYPE;
    changed = true;
  }
  return changed;
}

module.exports = {
  DEFAULT_DEPT_ID,
  DEFAULT_ENTITY_TYPE,
  deploymentOrgId,
  applyToUserData,
  applyToUserDoc,
};
