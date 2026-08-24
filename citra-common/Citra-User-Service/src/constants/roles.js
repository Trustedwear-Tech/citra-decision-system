// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

'use strict';
/**
 * Shared identity vocabulary (Node) — the fixed role-name constants plus the
 * small fixed token-sets (owner_type, entity_type).
 *
 * Import these instead of hard-coding string literals ('super_admin', …) so a
 * typo is a reference to `undefined` (caught fast) rather than a silently-wrong
 * string, and so there is ONE authoritative list of role names. Adding a new
 * role is a one-line change here — it flows into the CitraAIUser schema enum
 * (which uses ALL_ROLES) and everywhere the constants are referenced.
 *
 * This is ONLY the vocabulary. Authorization logic (which roles may do what)
 * stays inline in each route, composing its own set from these constants.
 *
 * Keep ALL_ROLES aligned with the Python side
 * (citra-auth/citra_auth/constants.py Roles.ALL) — both are just this 5-name list.
 */
const ROLES = Object.freeze({
  USER: 'user',
  IT_WORKFLOW: 'IT-workflow',
  DEPT_ADMIN: 'dept_admin',
  ORG_ADMIN: 'org_admin',
  SUPER_ADMIN: 'super_admin',
  // A non-admin who may BUILD & publish Decision Apps/APIs (the builder
  // persona). Consumers only see apps/dashboards published to them; the
  // builder card + build API are gated on this role (or any admin role).
  // Mirror of citra-auth/citra_auth/constants.py Roles.DECISION_APP_BUILDER.
  DECISION_APP_BUILDER: 'decision-app-builder',
});

/** Every valid platform role — used as the CitraAIUser.roles Mongoose enum. */
const ALL_ROLES = Object.freeze(Object.values(ROLES));

const OWNER_TYPE = Object.freeze({
  USER: 'user',
  SERVICE_ACCOUNT: 'service_account',
  DEPT: 'dept',
  ORG: 'org',
});

const ENTITY_TYPE = Object.freeze({
  COMPANY: 'company',
  STATE: 'state',
  DISTRICT: 'district',
  AGENCY: 'agency',
  GENERAL: 'general',
});
const ALL_ENTITY_TYPES = Object.freeze(Object.values(ENTITY_TYPE));

module.exports = { ROLES, ALL_ROLES, OWNER_TYPE, ENTITY_TYPE, ALL_ENTITY_TYPES };
