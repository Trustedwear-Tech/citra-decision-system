// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
