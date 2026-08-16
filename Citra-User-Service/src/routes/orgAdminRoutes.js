// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Org-admin routes — CRUD over the `orgs` tenant catalog.
 *
 * Mounted at /api/admin/orgs.
 *
 * RBAC:
 *   POST   /                — super_admin only. Creates a new org.
 *   GET    /                — super_admin sees all; org_admin/dept_admin
 *                             sees only their own org row.
 *   GET    /:orgId          — super_admin any; org_admin/dept_admin only
 *                             if it matches their JWT org_id.
 *   PATCH  /:orgId          — super_admin only. Update name/domain/is_demo.
 *                             id is immutable (it's the tenant identifier
 *                             every downstream service keys on).
 *   DELETE /:orgId          — super_admin only. Refuses if any user
 *                             still references this org_id.
 *
 * Why super_admin-only for writes:
 *   org_admin owns users + resources within their org but should not be
 *   able to mint a new org or delete the one they're in. Org lifecycle is
 *   a platform-level concern (billing, isolation guarantees, SAML/IdP
 *   wiring) so it stays with super_admin.
 */

const express = require('express');
const Org = require('../models/Org');
const CitraAIUser = require('../models/CitraAIUser');
const { authenticateToken } = require('../middleware/authMiddleware');

const router = express.Router();
router.use(authenticateToken);

const requireSuperAdmin = (req, res, next) => {
  const roles = (req.user && req.user.roles) || [];
  if (!roles.includes('super_admin')) {
    return res.status(403).json({
      success: false,
      error: 'super_admin required',
    });
  }
  next();
};

const ORG_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

function _sanitizeOrg(doc) {
  if (!doc) return null;
  const { _id, __v, ...rest } = doc;
  return rest;
}

router.post('/', requireSuperAdmin, async (req, res) => {
  const { id, name, domain, is_demo } = req.body || {};

  if (!id || !ORG_ID_PATTERN.test(id)) {
    return res.status(400).json({
      success: false,
      error: 'id is required and must be lowercase alphanumeric with hyphens (3-64 chars)',
    });
  }
  if (!name || typeof name !== 'string' || !name.trim()) {
    return res.status(400).json({ success: false, error: 'name is required' });
  }

  try {
    const existing = await Org.findOne({ id }).lean();
    if (existing) {
      return res.status(409).json({
        success: false,
        error: 'org already exists',
        org: _sanitizeOrg(existing),
      });
    }

    const created = await Org.create({
      id,
      name: name.trim(),
      domain: domain || null,
      is_demo: !!is_demo,
    });

    return res.status(201).json({
      success: true,
      org: _sanitizeOrg(created.toObject()),
    });
  } catch (err) {
    console.error('[orgAdmin] create failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/', async (req, res) => {
  try {
    const roles = (req.user && req.user.roles) || [];
    const isSuperAdmin = roles.includes('super_admin');

    let filter = {};
    if (!isSuperAdmin) {
      const callerOrgId = req.user && req.user.org_id;
      if (!callerOrgId) {
        return res.status(200).json({ success: true, orgs: [] });
      }
      filter = { id: callerOrgId };
    }

    const docs = await Org.find(filter).sort({ name: 1 }).lean();
    return res.status(200).json({
      success: true,
      orgs: docs.map(_sanitizeOrg),
    });
  } catch (err) {
    console.error('[orgAdmin] list failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/:orgId', async (req, res) => {
  const orgId = (req.params.orgId || '').trim();
  if (!orgId) {
    return res.status(400).json({ success: false, error: 'orgId required' });
  }

  try {
    const roles = (req.user && req.user.roles) || [];
    const isSuperAdmin = roles.includes('super_admin');
    if (!isSuperAdmin && req.user.org_id !== orgId) {
      return res.status(403).json({ success: false, error: 'org access denied' });
    }

    const doc = await Org.findOne({ id: orgId }).lean();
    if (!doc) {
      return res.status(404).json({ success: false, error: 'org not found' });
    }
    return res.status(200).json({ success: true, org: _sanitizeOrg(doc) });
  } catch (err) {
    console.error('[orgAdmin] get failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

router.patch('/:orgId', requireSuperAdmin, async (req, res) => {
  const orgId = (req.params.orgId || '').trim();
  if (!orgId) {
    return res.status(400).json({ success: false, error: 'orgId required' });
  }

  const { name, domain, is_demo } = req.body || {};
  const $set = {};
  if (typeof name === 'string' && name.trim()) $set.name = name.trim();
  if (domain === null || typeof domain === 'string') $set.domain = domain || null;
  if (typeof is_demo === 'boolean') $set.is_demo = is_demo;

  if (Object.keys($set).length === 0) {
    return res.status(400).json({
      success: false,
      error: 'nothing to update — provide name, domain, or is_demo',
    });
  }

  try {
    const updated = await Org.findOneAndUpdate(
      { id: orgId },
      { $set },
      { new: true, lean: true }
    );
    if (!updated) {
      return res.status(404).json({ success: false, error: 'org not found' });
    }
    return res.status(200).json({ success: true, org: _sanitizeOrg(updated) });
  } catch (err) {
    console.error('[orgAdmin] update failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

router.delete('/:orgId', requireSuperAdmin, async (req, res) => {
  const orgId = (req.params.orgId || '').trim();
  if (!orgId) {
    return res.status(400).json({ success: false, error: 'orgId required' });
  }

  try {
    const userCount = await CitraAIUser.countDocuments({ org_id: orgId });
    if (userCount > 0) {
      return res.status(409).json({
        success: false,
        error: 'org has users — reassign or delete users first',
        user_count: userCount,
      });
    }

    const result = await Org.deleteOne({ id: orgId });
    if (result.deletedCount === 0) {
      return res.status(404).json({ success: false, error: 'org not found' });
    }
    return res.status(200).json({ success: true, deleted: orgId });
  } catch (err) {
    console.error('[orgAdmin] delete failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

module.exports = router;
