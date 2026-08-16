// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Org — top-level tenant container.
 *
 * Each row is an organization that owns users, depts, and resources.
 * Seeded from `config/orgs.seed.json` on service startup (idempotent upsert).
 *
 * Org is the parent container for Dept (depts.org_id → orgs.id) and for
 * every user/resource carrying an `org_id`. Unlike Dept, the seed is NOT
 * single-tenant scoped — orgs ARE the tenants.
 *
 * Deletion of an Org row is refused via the admin API when any user still
 * references it; the seed never prunes orgs (orgs may be created via the
 * admin API and intentionally not present in the seed file).
 */

const mongoose = require('mongoose');

const OrgSchema = new mongoose.Schema(
  {
    id:      { type: String, required: true, unique: true, index: true },
    name:    { type: String, required: true },
    domain:  { type: String, default: null },
    is_demo: { type: Boolean, default: false },
  },
  {
    collection: 'orgs',
    timestamps: true,
  }
);

module.exports = mongoose.model('Org', OrgSchema);
