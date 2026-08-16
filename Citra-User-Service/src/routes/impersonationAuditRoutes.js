// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Impersonation audit routes — surface over the `impersonation_audit`
 * collection. Mints happen in userAdminRoutes.js (POST
 * /api/admin/users/:userId/impersonation-token); this router owns the
 * post-mint audit-side endpoints.
 *
 * Mounted at /api/admin/impersonations.
 *
 * RBAC: super_admin only on every route. Impersonation audit is a
 * compliance surface — leaking it to org_admins would let them see
 * super_admin activity targeting their users.
 *
 * Endpoints:
 *   POST /:impersonation_id/revoke   — flag the audit row revoked_at AND add
 *                                       the token's jti to the revoked_tokens
 *                                       blocklist (real mid-flight kill-switch).
 *                                       Returns 409 if already revoked.
 *   GET  /                           — list recent impersonations
 *                                       (paginated, newest first).
 *   GET  /:impersonation_id          — fetch one audit row.
 */

const express = require('express');
const ImpersonationAudit = require('../models/ImpersonationAudit');
const { authenticateToken } = require('../middleware/authMiddleware');
const revocationService = require('../services/revocationService');

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

router.use(requireSuperAdmin);

router.get('/', async (req, res) => {
  try {
    const limit = Math.min(parseInt(req.query.limit, 10) || 50, 200);
    const docs = await ImpersonationAudit.find({})
      .sort({ issued_at: -1 })
      .limit(limit)
      .lean();
    return res.status(200).json({
      success: true,
      impersonations: docs.map(({ _id, __v, ...rest }) => rest),
    });
  } catch (err) {
    console.error('[impersonationAudit] list failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

router.get('/:impersonation_id', async (req, res) => {
  const id = (req.params.impersonation_id || '').trim();
  if (!id) {
    return res.status(400).json({ success: false, error: 'impersonation_id required' });
  }
  try {
    const doc = await ImpersonationAudit.findOne({ impersonation_id: id }).lean();
    if (!doc) {
      return res.status(404).json({ success: false, error: 'impersonation not found' });
    }
    const { _id, __v, ...rest } = doc;
    return res.status(200).json({ success: true, impersonation: rest });
  } catch (err) {
    console.error('[impersonationAudit] get failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

router.post('/:impersonation_id/revoke', async (req, res) => {
  const id = (req.params.impersonation_id || '').trim();
  if (!id) {
    return res.status(400).json({ success: false, error: 'impersonation_id required' });
  }
  try {
    const updated = await ImpersonationAudit.findOneAndUpdate(
      { impersonation_id: id, revoked_at: null },
      { $set: { revoked_at: new Date() } },
      { new: true, lean: true }
    );
    if (!updated) {
      const existing = await ImpersonationAudit.findOne({ impersonation_id: id }).lean();
      if (!existing) {
        return res.status(404).json({ success: false, error: 'impersonation not found' });
      }
      return res.status(409).json({
        success: false,
        error: 'already revoked',
        revoked_at: existing.revoked_at,
      });
    }

    // Real kill-switch: blocklist the token's jti so every auth path rejects
    // it immediately. Older audit rows minted before jti tracking won't have
    // one — surface that rather than silently pretending the session was cut.
    let tokenRevoked = false;
    if (updated.jti) {
      const expSeconds = updated.expires_at
        ? Math.floor(new Date(updated.expires_at).getTime() / 1000)
        : undefined;
      await revocationService.revoke(updated.jti, expSeconds, {
        reason: 'impersonation_revoke',
        revokedBy: (req.user && (req.user.email || req.user.user_id)) || null,
      });
      tokenRevoked = true;
    } else {
      console.warn(
        `[impersonationAudit] revoke: audit row ${id} has no jti (minted before ` +
          `jti tracking) — audit flagged but the live token cannot be blocklisted.`
      );
    }

    return res.status(200).json({
      success: true,
      impersonation_id: id,
      revoked_at: updated.revoked_at,
      token_revoked: tokenRevoked,
    });
  } catch (err) {
    console.error('[impersonationAudit] revoke failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

module.exports = router;
