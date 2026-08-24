// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Personal Service Account helpers.
 *
 * Every CitraAIUser owns exactly one Personal SA. It's the default owner
 * of any workflow / smart-app / skill they create when they don't pick a
 * shared SA.
 *
 * Lifecycle:
 *   - Auto-created at signup (both Google + local paths call ensurePersonalSA).
 *   - Idempotent: re-calling for an existing user returns the existing SA.
 *   - Mirrored when the user's org_id or dept_ids change (mirrorOrgChange).
 *   - Cascade-deleted as part of the user-delete worker job.
 *
 * The Personal SA id is deterministic so other services can derive it
 * without a round-trip if they only have the user's email + org:
 *   svc:personal-<userid-slug>@<org-id>.citra.ai
 */

const ServiceAccount = require('../models/ServiceAccount');
const CitraAIUser = require('../models/CitraAIUser');
const { migratePersonalSAResources } = require('./saOrgMigration');


function _slugify(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'anonymous';
}


function personalSAIdFor(user) {
  if (!user) return null;
  const userPart = _slugify(user.email || user.user_id || '');
  const orgPart = _slugify(user.org_id || 'default');
  return `svc:personal-${userPart}@${orgPart}.citra.ai`;
}


/**
 * Idempotently create the user's Personal SA and stamp ``personal_sa_id``
 * on the user doc. Safe to call on every signup or login.
 *
 * Returns the SA doc, or null if the user has no org_id (cannot mint
 * an SA without one).
 */
async function ensurePersonalSA(user) {
  if (!user || !user.email) return null;
  if (!user.org_id) {
    // Without an org_id we cannot build a deterministic SA id. Skip
    // silently — caller can retry once the user is assigned to an org.
    return null;
  }

  const saId = personalSAIdFor(user);
  let sa = await ServiceAccount.findOne({ service_account_id: saId });
  if (!sa) {
    try {
      sa = await ServiceAccount.create({
        service_account_id: saId,
        display_name: `${user.email}'s personal workspace`,
        description:
          'Auto-created Personal Service Account. Owns workflows, smart-apps, and skills you create without picking a shared SA. Single-admin (you).',
        org_id: user.org_id,
        dept_ids: Array.isArray(user.dept_ids) ? user.dept_ids : [],
        admins: [user.email],
        members: [],
        roles: [],
        is_active: true,
        created_by_user_id: user.email,
        is_personal: true,
      });
    } catch (err) {
      // Race: another concurrent request created it first. Look it up.
      if (err && err.code === 11000) {
        sa = await ServiceAccount.findOne({ service_account_id: saId });
      } else {
        throw err;
      }
    }
  }

  // Stamp the user doc so admin/delete flows can find it cheaply.
  if (!user.personal_sa_id || user.personal_sa_id !== saId) {
    await CitraAIUser.updateOne(
      { email: user.email },
      { $set: { personal_sa_id: saId, updatedAt: new Date() } },
    );
    user.personal_sa_id = saId;
  }
  return sa;
}


/**
 * When a user's org_id or dept_ids change, mirror the change to their
 * Personal SA so resources owned by it stay correctly scoped.
 *
 * Same-org PATCH (dept change only): update dept_ids on the existing SA.
 *
 * Cross-org PATCH (org change): mint a new Personal SA under the new org
 * AND rewrite every resource (folders, presentations, printables,
 * reports, diagrams, vault_shares) from the OLD personal SA id to the
 * NEW one. Without the rewrite the user's body of work would disappear
 * from their view after an org move. The OLD SA doc itself is left in
 * place for audit / historical reference.
 */
async function mirrorOrgChange(user) {
  if (!user || !user.email || !user.org_id) return null;
  const expectedId = personalSAIdFor(user);

  // Same id → just update dept_ids on the existing SA.
  if (user.personal_sa_id === expectedId) {
    await ServiceAccount.updateOne(
      { service_account_id: expectedId },
      { $set: { dept_ids: Array.isArray(user.dept_ids) ? user.dept_ids : [] } },
    );
    return null;
  }

  // Different id (user moved org) → mint a new Personal SA, then
  // migrate every resource that used to point at the old one. The old
  // SA id is captured BEFORE ensurePersonalSA stamps the new value.
  const oldSaId = user.personal_sa_id || null;
  const sa = await ensurePersonalSA(user);
  if (oldSaId && sa && sa.service_account_id !== oldSaId) {
    try {
      const counts = await migratePersonalSAResources({
        oldSaId,
        newSaId: sa.service_account_id,
        newOrgId: user.org_id,
      });
      console.info(
        '[personalSA] org-move resource migration counts for %s:',
        user.email, counts,
      );
    } catch (err) {
      console.warn('[personalSA] resource migration failed for %s: %s', user.email, err.message);
    }
  }
  return sa;
}


module.exports = {
  personalSAIdFor,
  ensurePersonalSA,
  mirrorOrgChange,
};
