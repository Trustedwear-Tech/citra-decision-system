// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Work Service Account helpers.
 *
 * Every CitraAIUser owns exactly ONE Work SA in addition to their Personal
 * SA. The split exists because the two SAs have different lifecycles:
 *
 *   Personal SA  — owns ephemeral / personal-output resources
 *                  (presentations, printables, reports, diagrams).
 *                  Deleted with the user.
 *
 *   Work SA      — owns durable team-portable resources
 *                  (skills, smart-apps, workflows).
 *                  When the user is deleted by an admin, the admin can
 *                  re-home this SA to another user or roll it up to the
 *                  dept, so the team's work is preserved.
 *
 * The Work SA always carries the user's dept_ids on the SA doc, which
 * makes "dept_admin sees all SAs operating in their dept" naturally true.
 *
 * Deterministic id so other services can derive it from email + org
 * without a round-trip:
 *
 *   svc:work-<userid-slug>@<org-id>.citra.ai
 */

const ServiceAccount = require('../models/ServiceAccount');
const CitraAIUser = require('../models/CitraAIUser');
const { migrateWorkSAResources } = require('./saOrgMigration');


function _slugify(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'anonymous';
}


function workSAIdFor(user) {
  if (!user) return null;
  const userPart = _slugify(user.email || user.user_id || '');
  const orgPart = _slugify(user.org_id || 'default');
  return `svc:work-${userPart}@${orgPart}.citra.ai`;
}


/**
 * Idempotently create the user's Work SA and stamp ``work_sa_id`` on the
 * user doc. Safe to call on every signup or login.
 *
 * Returns the SA doc, or null if the user has no org_id (cannot mint an
 * SA without one — caller must assign an org first).
 */
async function ensureWorkSA(user) {
  if (!user || !user.email) return null;
  if (!user.org_id) {
    // Without an org_id we cannot build a deterministic SA id. Skip
    // silently — caller can retry once the user is assigned to an org.
    return null;
  }

  const saId = workSAIdFor(user);
  let sa = await ServiceAccount.findOne({ service_account_id: saId });
  if (!sa) {
    try {
      sa = await ServiceAccount.create({
        service_account_id: saId,
        display_name: `${user.email}'s work workspace`,
        description:
          'Auto-created Work Service Account. Owns durable team-portable resources (skills, smart-apps, workflows). Transferable to another user or to the dept when the owning user is removed.',
        org_id: user.org_id,
        dept_ids: Array.isArray(user.dept_ids) ? user.dept_ids : [],
        admins: [user.email],
        members: [],
        roles: [],
        is_active: true,
        created_by_user_id: user.email,
        is_personal: false,
      });
    } catch (err) {
      if (err && err.code === 11000) {
        sa = await ServiceAccount.findOne({ service_account_id: saId });
      } else {
        throw err;
      }
    }
  }

  if (!user.work_sa_id || user.work_sa_id !== saId) {
    await CitraAIUser.updateOne(
      { email: user.email },
      { $set: { work_sa_id: saId, updatedAt: new Date() } },
    );
    user.work_sa_id = saId;
  }
  return sa;
}


/**
 * When a user's org_id or dept_ids change, mirror the change to their
 * Work SA so resources owned by it stay correctly scoped (and the
 * dept-admin roll-up keeps working).
 *
 * Same-org PATCH: update dept_ids on the existing SA.
 *
 * Cross-org PATCH: mint a new Work SA under the new org AND rewrite
 * every resource (skills, workflows, smart-apps) from the OLD work SA
 * id to the NEW one. The old SA is left in place for audit only.
 */
async function mirrorWorkSAOrgChange(user) {
  if (!user || !user.email || !user.org_id) return null;
  const expectedId = workSAIdFor(user);

  if (user.work_sa_id === expectedId) {
    await ServiceAccount.updateOne(
      { service_account_id: expectedId },
      { $set: { dept_ids: Array.isArray(user.dept_ids) ? user.dept_ids : [] } },
    );
    return null;
  }

  const oldSaId = user.work_sa_id || null;
  const sa = await ensureWorkSA(user);
  if (oldSaId && sa && sa.service_account_id !== oldSaId) {
    try {
      const counts = await migrateWorkSAResources({
        oldSaId,
        newSaId: sa.service_account_id,
        newOrgId: user.org_id,
      });
      // A -1 in any collection = that collection's resources are orphaned on
      // the OLD SA id. Surface it at ERROR (monitored) rather than info, so an
      // org-move that left resources behind is visible, not reported as clean.
      const orphaned = Object.entries(counts || {})
        .filter(([, n]) => n === -1)
        .map(([c]) => c);
      if (orphaned.length) {
        console.error(
          '[workSA] ORPHAN-RISK: org-move for %s migrated some collections but FAILED on [%s] (oldSa=%s -> newSa=%s) — those resources stay on the old SA; admin should retry.',
          user.email, orphaned.join(', '), oldSaId, sa.service_account_id,
        );
      } else {
        console.info(
          '[workSA] org-move resource migration counts for %s:',
          user.email, counts,
        );
      }
    } catch (err) {
      console.error(
        '[workSA] ORPHAN-RISK: resource migration threw for %s (oldSa=%s) — resources may be orphaned on the old SA: %s',
        user.email, oldSaId, err.message,
      );
    }
  }
  return sa;
}


module.exports = {
  workSAIdFor,
  ensureWorkSA,
  mirrorWorkSAOrgChange,
};
