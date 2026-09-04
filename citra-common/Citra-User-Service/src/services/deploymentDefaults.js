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
 * A deployment has a home org, set via process.env.ORG_ID, and anyone who
 * signs in through the UI belongs to it. Demo tenants have their own orgs
 * and depts seeded via demo-data scripts; those users are NEVER created
 * through this path because they cannot log in directly
 * (impersonation-only).
 *
 * Anyone who logs in via the UI therefore gets:
 *   org_id      = ORG_ID env
 *   dept_ids    = [DEFAULT_DEPT_ID env]  — only when that is set
 *   entity_type = company
 *
 * DEFAULT_DEPT_ID USED TO BE THE HARDCODED STRING 'citra-software', which is
 * the name of a department in OUR org. Every self-hosted install therefore
 * filed its own users into a department it had never created and did not own:
 * on a fresh clone the super-admin came out as org_admin of one org while
 * being a member of a department belonging to another. Dept-scoped reads then
 * match nothing, and the operator has no way to see why.
 *
 * It is now read from the environment, and when it is unset NO department is
 * assigned. A user with no department is a state the platform already handles;
 * a user in a department that does not exist is not. Set DEFAULT_DEPT_ID in
 * .env to restore the old behaviour for a deployment that really does have
 * one home department.
 *
 * The functions below are idempotent: they only set a field when it's
 * currently empty. Demo personas seeded with explicit org_id/dept_ids
 * are left untouched.
 */

const DEFAULT_ENTITY_TYPE = 'company';

// null when unset — see the note above on why we assign nothing rather
// than inventing a department the deployment does not have.
function defaultDeptId() {
  return (process.env.DEFAULT_DEPT_ID || '').trim() || null;
}

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
  const deptId = defaultDeptId();
  const hasDept = Array.isArray(existing?.dept_ids) && existing.dept_ids.length > 0;
  if (deptId && (isNew || !hasDept)) {
    userData.dept_ids = [deptId];
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
  const deptId = defaultDeptId();
  if (deptId && (!Array.isArray(user.dept_ids) || user.dept_ids.length === 0)) {
    user.dept_ids = [deptId];
    changed = true;
  }
  if (!user.entity_type || user.entity_type === 'general') {
    user.entity_type = DEFAULT_ENTITY_TYPE;
    changed = true;
  }
  return changed;
}

module.exports = {
  defaultDeptId,
  DEFAULT_ENTITY_TYPE,
  deploymentOrgId,
  applyToUserData,
  applyToUserDoc,
};
