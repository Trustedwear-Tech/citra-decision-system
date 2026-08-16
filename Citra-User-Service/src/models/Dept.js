// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

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
