// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Service-account management endpoints.
 *
 * Auth: All endpoints require Bearer JWT.
 *
 * Mental model: think of an SA like a GitHub Team. ANY authenticated
 * user can create one inside their own org and they auto-become the
 * first admin. After that, admins manage member lists, mint tokens,
 * rotate keys, etc.
 *
 * RBAC summary:
 *   POST   /api/admin/service-accounts            ANY authenticated user (creates in their own org)
 *   GET    /api/admin/service-accounts            authed (returns SAs you admin/member of, or all in your org if org_admin)
 *   GET    /api/admin/service-accounts/:id        admin OR member OR org_admin+
 *   PATCH  /api/admin/service-accounts/:id        SA admin OR org_admin+
 *   POST   /api/admin/service-accounts/:id/token  SA admin OR org_admin+ — mints SA JWT
 *   POST   /api/admin/service-accounts/:id/rotate SA admin OR org_admin+ — bumps last_rotated_at
 *   POST   /api/admin/service-accounts/:id/disable SA admin OR org_admin+
 *   POST   /api/admin/service-accounts/:id/enable  org_admin+
 *   DELETE /api/admin/service-accounts/:id        super_admin only
 *
 * Org policy: when CITRA_SA_QUOTA_PER_USER env var is set, each user
 * may admin no more than that many SAs in their own org. Default is
 * 25 (high enough to never get in the way for legitimate use).
 */

const express = require('express');
const ServiceAccount = require('../models/ServiceAccount');
const CitraAIUser = require('../models/CitraAIUser');
const tokenService = require('../services/tokenService');
const { authenticateToken } = require('../middleware/authMiddleware');

const router = express.Router();
router.use(authenticateToken);


// ── Helpers ─────────────────────────────────────────────────────

function hasAnyRole(user, ...roles) {
  const have = user.roles || [];
  return roles.some((r) => have.includes(r));
}

function canManageSA(user, sa) {
  // super_admin: always
  if (hasAnyRole(user, 'super_admin')) return true;
  // org_admin: must be in same org
  if (hasAnyRole(user, 'org_admin') && user.org_id === sa.org_id) return true;
  // SA admin: explicit
  if (sa.isAdmin(user.email)) return true;
  return false;
}

function canViewSA(user, sa) {
  if (canManageSA(user, sa)) return true;
  // members can view
  if (sa.isMember(user.email)) return true;
  return false;
}

function normaliseEmails(arr) {
  return [...new Set((arr || []).map((e) => String(e).trim().toLowerCase()).filter(Boolean))];
}


/**
 * Validate every email in ``emails`` exists as a CitraAIUser. Optionally
 * restricts to the given org_id so an admin cannot accidentally add a
 * user from another org. Returns ``{ ok: true }`` or
 * ``{ ok: false, missing: [...], wrongOrg: [...] }``.
 */
async function validateEmailsAreUsers(emails, { orgId } = {}) {
  const list = normaliseEmails(emails);
  if (list.length === 0) return { ok: true, validated: [] };
  const users = await CitraAIUser.find(
    { email: { $in: list } },
    { email: 1, org_id: 1, _id: 0 },
  ).lean();
  const byEmail = new Map(users.map((u) => [String(u.email || '').toLowerCase(), u]));
  const missing = list.filter((e) => !byEmail.has(e));
  const wrongOrg = orgId
    ? list.filter((e) => {
        const u = byEmail.get(e);
        return u && u.org_id && u.org_id !== orgId;
      })
    : [];
  if (missing.length || wrongOrg.length) {
    return { ok: false, missing, wrongOrg };
  }
  return { ok: true, validated: list };
}


// ── Create ──────────────────────────────────────────────────────

const SA_QUOTA_PER_USER = parseInt(process.env.CITRA_SA_QUOTA_PER_USER || '25', 10);

router.post('/', async (req, res) => {
  try {
    // BREAKING-CHANGE FIX: any authenticated user can create an SA in
    // their own org (think: GitHub Team). They auto-become the first admin.
    // org_admin can additionally create SAs in arbitrary orgs (audit /
    // cross-org platform admin), super_admin can do anything.
    const {
      slug,
      display_name,
      description,
      org_id,
      dept_ids = [],
      admins = [],
      members = [],
      roles = ['service:workflow_runner'],
    } = req.body || {};

    if (!slug || !display_name) {
      return res.status(400).json({ error: 'slug and display_name are required' });
    }
    if (!/^[a-z0-9-]+$/.test(slug)) {
      return res.status(400).json({ error: 'slug must be lowercase alphanumeric + dashes' });
    }
    if (!req.user.email) {
      return res.status(400).json({ error: 'caller has no email — cannot become initial admin' });
    }

    // Default to caller's org. Only super_admin / org_admin can override.
    const effectiveOrgId = org_id || req.user.org_id;
    if (!effectiveOrgId) {
      return res.status(400).json({ error: 'org_id is required (caller has no org)' });
    }
    if (effectiveOrgId !== req.user.org_id && !hasAnyRole(req.user, 'org_admin', 'super_admin')) {
      return res.status(403).json({
        error: 'cannot create SA outside your org (org_admin / super_admin only)',
      });
    }

    // Soft quota — only regular users get capped; admins are exempt.
    if (!hasAnyRole(req.user, 'org_admin', 'super_admin')) {
      const owned = await ServiceAccount.countDocuments({
        admins: req.user.email,
        is_active: true,
      });
      if (owned >= SA_QUOTA_PER_USER) {
        return res.status(429).json({
          error: `you already admin ${owned} service accounts (quota=${SA_QUOTA_PER_USER}); ` +
                 'disable some or ask an org_admin to raise the quota',
        });
      }
    }

    // Caller is always added as initial admin so they don't lock themselves out
    const adminList = normaliseEmails([req.user.email, ...admins]);
    const memberList = normaliseEmails(members);

    // Reject unknown / cross-org emails (caller is implicitly valid; check the rest).
    const otherEmails = [...adminList, ...memberList].filter((e) => e !== req.user.email);
    if (otherEmails.length) {
      const v = await validateEmailsAreUsers(otherEmails, { orgId: effectiveOrgId });
      if (!v.ok) {
        return res.status(400).json({
          error: 'one or more emails do not resolve to a user in this org',
          missing: v.missing,
          wrong_org: v.wrongOrg,
        });
      }
    }

    const sa = new ServiceAccount({
      service_account_id: `svc:${slug}@${effectiveOrgId}.citra.ai`,
      display_name,
      description,
      org_id: effectiveOrgId,
      dept_ids: dept_ids || [],
      admins: adminList,
      members: memberList,
      roles,
      created_by_user_id: req.user.email,
    });

    await sa.save();
    return res.status(201).json({ ok: true, service_account: sa.toObject() });
  } catch (err) {
    if (err.code === 11000) {
      return res.status(409).json({ error: 'service_account_id already exists' });
    }
    return res.status(400).json({ error: err.message });
  }
});


// ── List ────────────────────────────────────────────────────────

router.get('/', async (req, res) => {
  try {
    let query;
    if (hasAnyRole(req.user, 'super_admin')) {
      query = {};
    } else if (hasAnyRole(req.user, 'org_admin') && req.user.org_id) {
      // org_admin sees everything in their org
      query = { org_id: req.user.org_id };
    } else {
      // Everyone else sees only SAs they admin OR member of
      const email = req.user.email;
      query = {
        $or: [{ admins: email }, { members: email }],
      };
    }
    if (req.query.active_only !== 'false') {
      query.is_active = true;
    }
    const docs = await ServiceAccount.find(query).limit(200).sort({ created_at: -1 });
    return res.json({
      total: docs.length,
      service_accounts: docs.map((d) => d.toObject()),
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});


// ── Get one ─────────────────────────────────────────────────────

router.get('/:id', async (req, res) => {
  try {
    const sa = await ServiceAccount.findOne({ service_account_id: req.params.id });
    if (!sa) return res.status(404).json({ error: 'not found' });
    if (!canViewSA(req.user, sa)) {
      return res.status(403).json({ error: 'not a member or admin of this service account' });
    }
    return res.json({ service_account: sa.toObject() });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});


// ── Update (admins/members/dept_ids/display_name/description) ──

router.patch('/:id', async (req, res) => {
  try {
    const sa = await ServiceAccount.findOne({ service_account_id: req.params.id });
    if (!sa) return res.status(404).json({ error: 'not found' });
    if (!canManageSA(req.user, sa)) {
      return res.status(403).json({ error: 'only SA admin or org_admin can update' });
    }

    const allowed = ['display_name', 'description', 'dept_ids', 'admins', 'members', 'roles'];
    for (const k of allowed) {
      if (k in req.body) {
        if (k === 'admins') {
          const newAdmins = normaliseEmails(req.body.admins);
          if (newAdmins.length === 0) {
            return res.status(400).json({ error: 'cannot remove all admins' });
          }
          // Don't let someone remove themselves from admins unless org_admin
          if (!newAdmins.includes(req.user.email) && !hasAnyRole(req.user, 'org_admin', 'super_admin')) {
            return res.status(400).json({ error: 'cannot remove yourself from admins (ask another admin or org_admin)' });
          }
          // Validate any newly-added admins resolve to real users in this SA's org.
          const added = newAdmins.filter((e) => !sa.admins.includes(e));
          if (added.length) {
            const v = await validateEmailsAreUsers(added, { orgId: sa.org_id });
            if (!v.ok) {
              return res.status(400).json({
                error: 'one or more admin emails do not resolve to a user in this org',
                missing: v.missing,
                wrong_org: v.wrongOrg,
              });
            }
          }
          sa.admins = newAdmins;
        } else if (k === 'members') {
          const newMembers = normaliseEmails(req.body.members);
          const added = newMembers.filter((e) => !sa.members.includes(e));
          if (added.length) {
            const v = await validateEmailsAreUsers(added, { orgId: sa.org_id });
            if (!v.ok) {
              return res.status(400).json({
                error: 'one or more member emails do not resolve to a user in this org',
                missing: v.missing,
                wrong_org: v.wrongOrg,
              });
            }
          }
          sa.members = newMembers;
        } else {
          sa[k] = req.body[k];
        }
      }
    }
    await sa.save();
    return res.json({ ok: true, service_account: sa.toObject() });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
});


// ── Mint a token ────────────────────────────────────────────────

router.post('/:id/token', async (req, res) => {
  try {
    const sa = await ServiceAccount.findOne({ service_account_id: req.params.id });
    if (!sa) return res.status(404).json({ error: 'not found' });
    if (!canManageSA(req.user, sa)) {
      return res.status(403).json({ error: 'only SA admin or org_admin can mint a token' });
    }
    const ttl = Math.max(60, Math.min(parseInt(req.body?.ttl_seconds || 3600, 10), 86400));
    const token = tokenService.generateServiceAccountToken(sa, { ttl_seconds: ttl });
    return res.json({
      access_token: token,
      token_type: 'Bearer',
      expires_in: ttl,
      service_account_id: sa.service_account_id,
    });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
});


// ── Rotate (bump last_rotated_at, invalidates pre-existing tokens) ──

router.post('/:id/rotate', async (req, res) => {
  try {
    const sa = await ServiceAccount.findOne({ service_account_id: req.params.id });
    if (!sa) return res.status(404).json({ error: 'not found' });
    if (!canManageSA(req.user, sa)) {
      return res.status(403).json({ error: 'only SA admin or org_admin can rotate' });
    }
    sa.last_rotated_at = new Date();
    await sa.save();
    return res.json({ ok: true, last_rotated_at: sa.last_rotated_at });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
});


// ── Disable / Enable ────────────────────────────────────────────

router.post('/:id/disable', async (req, res) => {
  try {
    const sa = await ServiceAccount.findOne({ service_account_id: req.params.id });
    if (!sa) return res.status(404).json({ error: 'not found' });
    if (!canManageSA(req.user, sa)) {
      return res.status(403).json({ error: 'only SA admin or org_admin can disable' });
    }
    sa.is_active = false;
    sa.disabled_at = new Date();
    sa.disabled_reason = String(req.body?.reason || '').slice(0, 500) || null;
    await sa.save();
    return res.json({ ok: true });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
});

router.post('/:id/enable', async (req, res) => {
  try {
    if (!hasAnyRole(req.user, 'org_admin', 'super_admin')) {
      return res.status(403).json({ error: 'org_admin or super_admin required' });
    }
    const sa = await ServiceAccount.findOne({ service_account_id: req.params.id });
    if (!sa) return res.status(404).json({ error: 'not found' });
    sa.is_active = true;
    sa.disabled_at = null;
    sa.disabled_reason = null;
    await sa.save();
    return res.json({ ok: true });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
});


// ── Delete (super_admin only) ───────────────────────────────────

router.delete('/:id', async (req, res) => {
  try {
    if (!hasAnyRole(req.user, 'super_admin')) {
      return res.status(403).json({ error: 'super_admin only' });
    }
    const r = await ServiceAccount.deleteOne({ service_account_id: req.params.id });
    if (!r.deletedCount) return res.status(404).json({ error: 'not found' });
    return res.json({ ok: true });
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }
});


module.exports = router;
