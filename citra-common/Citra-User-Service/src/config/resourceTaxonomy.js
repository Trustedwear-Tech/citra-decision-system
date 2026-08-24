// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Resource taxonomy — single source of truth across the platform.
 *
 * MANAGED resources are admin-visible: dept_admin / org_admin can list,
 * transfer, and delete them. On user deletion the admin chooses keep /
 * transfer-to-SA / transfer-to-dept / delete via the per-resource picker.
 *
 * PERSONAL resources are admin-invisible. On user deletion they are
 * hard-cascade-deleted (no picker, just a count + confirmation). Sharing
 * is per-resource (team_share + public_share) via the centralised
 * authorization_service.
 *
 * KEEP THESE THREE LISTS IN LOCKSTEP:
 *   - Citra-User-Service/src/config/resourceTaxonomy.js  (this file, JS)
 *   - Citra-Service/config/resource_taxonomy.py          (Python)
 *   - Citra-UI/services/resourceTaxonomy.js              (frontend mirror)
 */

const MANAGED_RESOURCES = Object.freeze([
  'workflow',
  'smart_app',
]);

const PERSONAL_RESOURCES = Object.freeze([
  'presentation',
  'report',      // backed by composer_reports collection
  'printable',
  'diagram',     // mindmaps share this collection
  'note',        // backed by Notes collection (capital N)
  'page',        // page_blocks + page_databases cascade with this
  'vault',       // backed by folders collection
  'project',
]);

const ALL_RESOURCES = Object.freeze([
  ...MANAGED_RESOURCES,
  ...PERSONAL_RESOURCES,
]);

function isManaged(resourceType) {
  return MANAGED_RESOURCES.includes(resourceType);
}

function isPersonal(resourceType) {
  return PERSONAL_RESOURCES.includes(resourceType);
}

module.exports = {
  MANAGED_RESOURCES,
  PERSONAL_RESOURCES,
  ALL_RESOURCES,
  isManaged,
  isPersonal,
};
