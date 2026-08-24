// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Dept — single-tenant dept catalog.
 *
 * Each row is an org-scoped department the super_admin can assign to users.
 * Seeded from `config/depts.seed.json` on service startup (idempotent upsert).
 * Until SAML/IdP wiring lands, this is the authoritative list of valid
 * dept_ids that the Manage Users UI uses for its multiselect.
 *
 * `parent_id` lets a deployment model hierarchy (e.g., plant_ops →
 * plant_ops_north) but most deployments will run flat.
 */

const mongoose = require('mongoose');

const DeptSchema = new mongoose.Schema(
  {
    id:        { type: String, required: true, index: true },
    name:      { type: String, required: true },
    parent_id: { type: String, default: null },
    org_id:    { type: String, required: true, index: true },
  },
  {
    collection: 'depts',
    timestamps: true,
  }
);

DeptSchema.index({ org_id: 1, id: 1 }, { unique: true });

module.exports = mongoose.model('Dept', DeptSchema);
