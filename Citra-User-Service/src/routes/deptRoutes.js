// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * deptRoutes — read + write depts.
 *
 * GET /api/depts
 *   Any authenticated user. Returns depts in the caller's own org.
 *
 * POST /api/admin/depts
 *   super_admin. Upsert (or create) a dept under any org. Used by
 *   demo-data/scripts/seed_tenant.py to provision a new demo tenant's
 *   dept catalog via API — no hardcoded seed files, no Mongo writes.
 *
 * Body: { org_id, id, name, parent_id? }
 *   org_id    — target org (the dept catalog is per-org)
 *   id        — dept slug (immutable identifier; used in users.dept_ids)
 *   name      — display name
 *   parent_id — optional parent slug for hierarchy
 *
 * The deployment's home org (citra-ai) ships with citra-software in
 * config/depts.seed.json. Demo tenants seed their own depts via this
 * endpoint so user-service has zero tenant-specific config.
 */

const express = require('express');
const router = express.Router();
const Dept = require('../models/Dept');
const { authenticateToken } = require('../middleware/authMiddleware');

router.get('/', authenticateToken, async (req, res) => {
  try {
    // Caller's org from JWT. Fall back to the deployment ORG_ID if the JWT
    // somehow lacks it (legacy user pre-backfill).
    //
    // super_admin may pass ?org_id=<other org> to read ANOTHER org's dept
    // catalog (used by the automation console to drill into a specific org on a
    // multi-org hub box). Everyone else is locked to their own org.
    const roles = (req.user && req.user.roles) || [];
    const isSuperAdmin = roles.includes('super_admin');
    const requestedOrg = (req.query && String(req.query.org_id || '').trim()) || '';
    const orgId = (isSuperAdmin && requestedOrg)
      ? requestedOrg
      : ((req.user && req.user.org_id) || (process.env.ORG_ID || '').trim() || null);
    if (!orgId) {
      return res.status(200).json({ success: true, org_id: null, depts: [] });
    }

    const docs = await Dept.find({ org_id: orgId })
      .select('id name parent_id -_id')
      .sort({ name: 1 })
      .lean();

    return res.status(200).json({
      success: true,
      org_id: orgId,
      depts: docs,
    });
  } catch (err) {
    console.error('[deptRoutes] list failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// Sub-router for super-admin-only dept writes. Mounted at /api/admin/depts
// in app.js; kept in this file so dept logic stays together.
const adminRouter = express.Router();
adminRouter.use(authenticateToken);
adminRouter.use((req, res, next) => {
  const roles = (req.user && req.user.roles) || [];
  if (!roles.includes('super_admin')) {
    return res.status(403).json({ success: false, error: 'super_admin required' });
  }
  next();
});

const DEPT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$/;
const ORG_ID_PATTERN  = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

adminRouter.post('/', async (req, res) => {
  const { org_id, id, name, parent_id } = req.body || {};
  if (!ORG_ID_PATTERN.test(org_id || '')) {
    return res.status(400).json({ success: false, error: 'org_id is required and must be a valid slug' });
  }
  if (!DEPT_ID_PATTERN.test(id || '')) {
    return res.status(400).json({ success: false, error: 'id is required and must be a valid slug' });
  }
  if (!name || typeof name !== 'string' || !name.trim()) {
    return res.status(400).json({ success: false, error: 'name is required' });
  }

  try {
    const result = await Dept.findOneAndUpdate(
      { org_id, id },
      { $set: { org_id, id, name: name.trim(), parent_id: parent_id || null } },
      { upsert: true, new: true, lean: true },
    );
    return res.status(200).json({ success: true, dept: result });
  } catch (err) {
    console.error('[deptRoutes] upsert failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

module.exports = router;
module.exports.adminRouter = adminRouter;
