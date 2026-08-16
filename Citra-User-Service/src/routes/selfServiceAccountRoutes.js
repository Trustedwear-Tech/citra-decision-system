// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Personal Service Account endpoints — mounted at /api/auth/me.
 *
 * Under SA-only ownership, every workflow / smart-app must belong to an
 * SA. When a user creates a resource without picking a shared SA, the UI
 * (or a builder pod, server-side) calls POST /api/auth/me/personal-sa to
 * fetch — and create-on-demand — the user's "Personal SA". This SA has
 * the user as its sole admin; resources owned by it default to a
 * `lifecycle_stage="personal"` and survive ownership transfers if the
 * user later adds collaborators or transfers to a team SA.
 *
 * The Personal SA id is deterministic so it can be derived without a
 * round-trip in services like smart-app-service / citra-workflow:
 *     svc:personal-<userid-slug>@<org-id>.citra.ai
 *
 * Auth: any authenticated user (creates their own; will not create one
 * for someone else).
 */

const express = require('express');
const ServiceAccount = require('../models/ServiceAccount');
const CitraAIUser = require('../models/CitraAIUser');
const { authenticateToken } = require('../middleware/authMiddleware');

const router = express.Router();
router.use(authenticateToken);


function _slugify(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'anonymous';
}


function _personalSAIdFor(user) {
  const userPart = _slugify(user.email || user.user_id || '');
  const orgPart = _slugify(user.org_id || 'default');
  return `svc:personal-${userPart}@${orgPart}.citra.ai`;
}


/**
 * GET/POST /api/auth/me/personal-sa
 *
 * Idempotent: returns the caller's Personal SA, auto-creating it if
 * absent. POST and GET behave identically — POST is the canonical verb
 * since this endpoint may write on first call.
 */
async function getOrCreatePersonalSA(req, res) {
  try {
    const u = req.user || {};
    const userEmail = u.email || u.user_id;
    if (!userEmail) {
      return res.status(401).json({ success: false, error: 'auth required' });
    }
    if (!u.org_id) {
      return res.status(400).json({
        success: false,
        error: 'user has no org_id — cannot mint a Personal SA',
      });
    }

    const saId = _personalSAIdFor(u);
    let sa = await ServiceAccount.findOne({ service_account_id: saId });

    if (!sa) {
      sa = await ServiceAccount.create({
        service_account_id: saId,
        display_name: `${u.email || 'user'}'s personal workspace`,
        description:
          'Auto-created Personal Service Account. Owns workflows + smart-apps you create without picking a shared SA. Single-admin (you).',
        org_id: u.org_id,
        dept_ids: Array.isArray(u.dept_ids) ? u.dept_ids : [],
        admins: [userEmail],
        members: [],
        roles: [],
        is_active: true,
        created_by_user_id: userEmail,
        // Personal SAs are flagged so admin tooling can detect them
        // (e.g. delete-user picker treats sole-admin Personal SAs specially).
        is_personal: true,
      });
    }

    // Stamp the user doc with personal_sa_id so the admin-delete flow can
    // find it cheaply. Idempotent.
    await CitraAIUser.updateOne(
      { email: userEmail, personal_sa_id: { $ne: saId } },
      { $set: { personal_sa_id: saId, updatedAt: new Date() } }
    );

    return res.status(200).json({
      success: true,
      service_account_id: sa.service_account_id,
      display_name: sa.display_name,
      org_id: sa.org_id,
      admins: sa.admins,
      members: sa.members,
      is_personal: true,
      created: !sa.createdAt || (Date.now() - new Date(sa.createdAt).getTime() < 5000),
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[personal-sa] failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
}


router.get('/personal-sa', getOrCreatePersonalSA);
router.post('/personal-sa', getOrCreatePersonalSA);


module.exports = router;
