// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * User-admin routes — endpoints for deactivating users and triggering
 * the resource-inheritance flow.
 *
 * RBAC:
 *   POST /api/admin/users/:userId/deactivate
 *      admin only — flips isActive=false, then enqueues a
 *      `user.deactivated` job for Citra-Worker which fans out across
 *      Mongo: every workflow + smart-app the user owns gets the
 *      inheritance_policy chosen at creation time applied (archive,
 *      transfer_to_sa, transfer_to_org, delete_after_grace). A
 *      HandoffReport is written for admin review.
 *
 *   POST /api/admin/users/:userId/reactivate
 *      admin only — flips isActive back to true (does NOT auto-revert
 *      inherited resources; admins must restore individually via the
 *      lifecycle endpoints since other people may already depend on
 *      the new owners).
 */

const express = require('express');
const crypto = require('crypto');
const mongoose = require('mongoose');
const CitraAIUser = require('../models/CitraAIUser');
const ServiceAccount = require('../models/ServiceAccount');
const DeletionRequest = require('../models/DeletionRequest');
const ImpersonationAudit = require('../models/ImpersonationAudit');
const workerQueue = require('../services/workerQueue');
const tokenService = require('../services/tokenService');
const { mirrorOrgChange, ensurePersonalSA } = require('../services/personalSAService');
const { ensureWorkSA, mirrorWorkSAOrgChange } = require('../services/workSAService');
const { authenticateToken, requireAdmin } = require('../middleware/authMiddleware');

// Citra-Service base URL — used to call the personal-content summary +
// apply endpoints during admin-delete. Required (RULE #1): no localhost
// fallback — resolved via the central env config, which throws if unset.
const CITRA_SERVICE_URL = require('../config/environment').citraServiceUrl;


async function _fetchPersonalContentSummary(targetEmail, authHeader) {
  /**
   * Calls GET /api/v2/admin/user-content-summary on Citra-Service.
   *
   * The Citra-Service endpoint is org_admin / super_admin gated and
   * authenticates with the same JWT the admin sent us. We forward the
   * caller's Authorization header so authorization is consistent.
   *
   * Returns the summary payload, or null when Citra-Service is
   * unreachable (preflight still works, just without content counts).
   */
  if (!authHeader) return null;
  try {
    const url = `${CITRA_SERVICE_URL}/api/v2/admin/user-content-summary?email=${encodeURIComponent(targetEmail)}`;
    const res = await fetch(url, {
      method: 'GET',
      headers: { Authorization: authHeader, 'Content-Type': 'application/json' },
    });
    if (!res.ok) {
      // eslint-disable-next-line no-console
      console.warn('[userAdmin] content-summary returned', res.status);
      return null;
    }
    return await res.json();
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[userAdmin] content-summary fetch failed:', err.message);
    return null;
  }
}

const router = express.Router();
router.use(authenticateToken);


function _toUserId(rawId) {
  // Accept email or _id; the model has both as identifiers.
  return (rawId || '').trim().toLowerCase();
}


/**
 * Deactivate a user + kick off resource inheritance.
 *
 * Body (optional):
 *   { reason: "left company" }
 *
 * Response:
 *   { success: true, user_id, job_id, message }
 *
 * The job_id can be polled against Citra-Worker's status endpoint to
 * know when the inheritance fan-out is complete. The HandoffReport for
 * this deactivation surfaces in Mongo at HandoffReports.
 */
router.post('/:userId/deactivate', requireAdmin, async (req, res) => {
  try {
    const targetEmailOrId = _toUserId(req.params.userId);
    if (!targetEmailOrId) {
      return res.status(400).json({ success: false, error: 'userId required' });
    }

    // Locate the user. Either email or _id is accepted.
    const user = await CitraAIUser.findOne({
      $or: [
        { email: targetEmailOrId },
        { _id: targetEmailOrId.length === 24 ? targetEmailOrId : undefined },
      ].filter((q) => q._id !== undefined || q.email),
    });
    if (!user) {
      return res.status(404).json({ success: false, error: 'user not found' });
    }

    if (user.isActive === false) {
      return res.status(200).json({
        success: true,
        already_deactivated: true,
        user_id: user.email,
        message: 'user was already deactivated; not re-enqueuing inheritance',
      });
    }

    // 1. Flip the flag in Mongo
    user.isActive = false;
    user.updatedAt = new Date();
    await user.save();

    // 2. Enqueue the inheritance job — best-effort; we don't block the
    //    admin response on Redis being up because the deactivation itself
    //    has already taken effect and the job can be retried.
    let jobId = null;
    try {
      jobId = await workerQueue.enqueue('user.deactivated', {
        user_id: user.email,
        email: user.email,
        org_id: user.org_id || user.tenant_id || null,
        deactivated_by: req.user && (req.user.email || req.user.user_id),
        deactivated_at: new Date().toISOString(),
        reason: (req.body && req.body.reason) || '',
      });
    } catch (qErr) {
      // eslint-disable-next-line no-console
      console.error('[userAdmin] failed to enqueue user.deactivated job:', qErr);
    }

    return res.status(200).json({
      success: true,
      user_id: user.email,
      isActive: user.isActive,
      job_id: jobId,
      message:
        jobId
          ? 'user deactivated; resource inheritance job enqueued'
          : 'user deactivated; inheritance job FAILED to enqueue — retry via /retry-inheritance',
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[userAdmin] deactivate failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * Re-enable a user. Does NOT auto-revert inherited resources; admins
 * who restore a user should manually use the per-resource transfer
 * endpoints to move things back if needed (since other users may
 * already be operating under the new owners).
 */
router.post('/:userId/reactivate', requireAdmin, async (req, res) => {
  try {
    const targetEmailOrId = _toUserId(req.params.userId);
    const user = await CitraAIUser.findOne({ email: targetEmailOrId });
    if (!user) {
      return res.status(404).json({ success: false, error: 'user not found' });
    }
    user.isActive = true;
    user.updatedAt = new Date();
    await user.save();
    return res.status(200).json({
      success: true,
      user_id: user.email,
      isActive: user.isActive,
      message:
        'user reactivated. Note: previously-inherited resources are NOT auto-restored; use lifecycle endpoints to transfer them back if desired.',
    });
  } catch (err) {
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * Manually retry the inheritance job for a user whose deactivation
 * succeeded but whose initial enqueue failed (Redis was down, etc.).
 * Admin-only — won't fire if the user is currently active.
 */
router.post('/:userId/retry-inheritance', requireAdmin, async (req, res) => {
  try {
    const targetEmailOrId = _toUserId(req.params.userId);
    const user = await CitraAIUser.findOne({ email: targetEmailOrId });
    if (!user) {
      return res.status(404).json({ success: false, error: 'user not found' });
    }
    if (user.isActive !== false) {
      return res.status(400).json({
        success: false,
        error: 'user is active; deactivate first',
      });
    }
    const jobId = await workerQueue.enqueue('user.deactivated', {
      user_id: user.email,
      email: user.email,
      org_id: user.org_id || user.tenant_id || null,
      deactivated_by: req.user && (req.user.email || req.user.user_id),
      deactivated_at: new Date().toISOString(),
      reason: 'retry: inheritance re-enqueued',
    });
    return res.status(200).json({ success: true, user_id: user.email, job_id: jobId });
  } catch (err) {
    return res.status(500).json({ success: false, error: err.message });
  }
});


// ─── Admin-driven user deletion (Phase E) ──────────────────────────────


/**
 * Workflows and smart_apps live in the Citra application DB (default
 * "citra"), separate from this service's user DB. We reuse the open
 * mongoose connection to read across DBs.
 */
// Resolve the app DB the same way saOrgMigration does: env first, then the
// active connection's DB, then 'dev'. The old hard 'citra' default pointed at
// a DB where SmartApps don't live, so the offboarding picker listed zero apps.
const CITRA_DB = process.env.CITRA_APP_DB
  || process.env.MONGODB_DATABASE
  || (mongoose.connection && mongoose.connection.name)
  || 'dev';

function _citraColl(name) {
  if (!mongoose.connection || mongoose.connection.readyState !== 1) {
    throw new Error('mongoose not connected — cannot reach citra DB');
  }
  return mongoose.connection.client.db(CITRA_DB).collection(name);
}


function _isAdminInScope(adminUser, targetOrgId, targetDeptIds) {
  const roles = adminUser.roles || [];
  if (roles.includes('super_admin')) return { ok: true, role: 'super_admin' };
  if (roles.includes('org_admin') && adminUser.org_id === targetOrgId) {
    return { ok: true, role: 'org_admin' };
  }
  if (roles.includes('dept_admin')) {
    const adminDepts = new Set(adminUser.dept_ids || []);
    const overlap = (targetDeptIds || []).some((d) => adminDepts.has(d));
    if (overlap) return { ok: true, role: 'dept_admin' };
  }
  return { ok: false };
}


/**
 * POST /api/admin/users/:userId/preflight-delete
 *
 * Creates a draft DeletionRequest and returns the per-resource picker
 * payload. Admin uses this to render the "Delete user X" table.
 *
 * Returns:
 *   {
 *     request_id, target_email, personal_sa_id, resources: [
 *       { kind, resource_id, name, current_sa, sole_admin, suggested_action }
 *     ]
 *   }
 */
router.post('/:userId/preflight-delete', async (req, res) => {
  try {
    const target = _toUserId(req.params.userId);
    const user = await CitraAIUser.findOne({ email: target });
    if (!user) return res.status(404).json({ success: false, error: 'user not found' });

    const scope = _isAdminInScope(req.user || {}, user.org_id, user.dept_ids || []);
    if (!scope.ok) {
      return res.status(403).json({
        success: false,
        error: 'org_admin / dept_admin (overlapping dept) / super_admin required',
      });
    }

    // Find SAs where this user is an admin or member.
    const userSAs = await ServiceAccount.find({
      $or: [{ admins: user.email }, { members: user.email }],
    }).lean();
    const saIds = userSAs.map((s) => s.service_account_id);
    const adminOnlySAIds = userSAs
      .filter((s) => (s.admins || []).includes(user.email))
      .map((s) => s.service_account_id);

    // Resources where target user is sole admin's SA owner.
    // NOTE: folders/vaults are Personal-SA-owned and cascade-delete with
    // the user (worker.PERSONAL_COLLECTIONS). They appear in the
    // personal_content bucket counts, NOT here.
    const wfColl = _citraColl('Workflows');
    const appColl = _citraColl('smartapp_apps');

    const workflows = saIds.length
      ? await wfColl
          .find({
            owner_type: 'service_account',
            owner_id: { $in: saIds },
            is_active: { $ne: false },
          })
          .toArray()
      : [];
    const apps = saIds.length
      ? await appColl
          .find({
            $or: [
              { 'app_spec.owner_type': 'service_account', 'app_spec.owner_id': { $in: saIds } },
            ],
          })
          .toArray()
      : [];
    // sole_admin = true when the only admin of the owning SA is this user.
    const saByIdAdmins = Object.fromEntries(
      userSAs.map((s) => [s.service_account_id, (s.admins || []).filter((a) => a)])
    );

    function _buildRow(kind, doc, ownerId) {
      const admins = saByIdAdmins[ownerId] || [];
      const sole =
        admins.length === 1 && admins[0].toLowerCase() === user.email.toLowerCase();
      // Default suggestion: sole → transfer_to_sa (admin picks target);
      //                     shared → keep (other admins continue).
      const suggested = sole ? 'transfer_to_sa' : 'keep';
      return {
        kind,
        resource_id: kind === 'workflow' ? doc.workflow_id : doc.slug,
        name: doc.name || (doc.app_spec && doc.app_spec.title) || doc.title || '',
        current_sa: ownerId,
        sole_admin: sole,
        action: suggested,
        action_target: null,
      };
    }

    const resourceRows = [];
    for (const wf of workflows) {
      resourceRows.push(_buildRow('workflow', wf, wf.owner_id));
    }
    for (const app of apps) {
      const ownerId = (app.app_spec && app.app_spec.owner_id) || null;
      if (ownerId) resourceRows.push(_buildRow('smart_app', app, ownerId));
    }
    // ── Phase H: pull personal-content counts from Citra-Service ──
    // (presentations, reports, notes, mindmaps, pages, etc.). The admin
    // will pick a bulk policy per bucket in the picker UI.
    const contentSummary = await _fetchPersonalContentSummary(
      user.email,
      req.headers.authorization || '',
    );

    // Persist a draft DeletionRequest so the admin can come back later.
    const request_id = crypto.randomUUID();
    await DeletionRequest.create({
      request_id,
      target_user_id: user.email,
      target_email: user.email,
      target_personal_sa_id: user.personal_sa_id || null,
      requested_by: (req.user && (req.user.email || req.user.user_id)) || 'unknown',
      requested_by_role: scope.role,
      org_id: user.org_id || '',
      dept_ids: user.dept_ids || [],
      status: 'draft',
      resources: resourceRows,
      personal_content_summary: contentSummary || null,
    });

    return res.status(200).json({
      success: true,
      request_id,
      target_email: user.email,
      target_personal_sa_id: user.personal_sa_id || null,
      service_accounts_user_admins: adminOnlySAIds,
      resources: resourceRows,
      // New: per-bucket counts so the UI can render a "Personal content"
      // section alongside the workflow/app picker. Null when Citra-Service
      // is unreachable — the UI degrades gracefully.
      personal_content: contentSummary || null,
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[userAdmin] preflight-delete failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * POST /api/admin/users/:userId/delete
 *
 * Body:
 *   {
 *     request_id: "<from preflight>",     // optional but recommended
 *     resource_actions: [                  // overrides for what preflight saw
 *       { kind, resource_id, action, action_target? }
 *     ]
 *   }
 *
 * Validates each action, enqueues a `user.delete_applied` job, marks the
 * DeletionRequest submitted, and responds with the job_id the UI can
 * poll on for the resulting HandoffReport id.
 */
router.post('/:userId/delete', async (req, res) => {
  try {
    const target = _toUserId(req.params.userId);
    const user = await CitraAIUser.findOne({ email: target });
    if (!user) return res.status(404).json({ success: false, error: 'user not found' });

    const scope = _isAdminInScope(req.user || {}, user.org_id, user.dept_ids || []);
    if (!scope.ok) {
      return res.status(403).json({ success: false, error: 'admin scope required' });
    }
    if (user.deletion_state === 'deleted') {
      return res.status(409).json({ success: false, error: 'user already deleted' });
    }

    const incoming = Array.isArray(req.body && req.body.resource_actions)
      ? req.body.resource_actions
      : [];
    const validActions = new Set([
      'keep', 'transfer_to_sa', 'transfer_to_dept', 'make_me_admin',
      'archive', 'delete',
    ]);
    for (const a of incoming) {
      if (!validActions.has(a.action)) {
        return res.status(400).json({
          success: false,
          error: `invalid action '${a.action}' for ${a.kind}:${a.resource_id}`,
        });
      }
      if (
        ['transfer_to_sa', 'transfer_to_dept'].includes(a.action)
        && !a.action_target
      ) {
        return res.status(400).json({
          success: false,
          error: `action_target required for ${a.action} on ${a.kind}:${a.resource_id}`,
        });
      }
    }

    // Load or create the DeletionRequest. If request_id is supplied,
    // overlay the picker's choices on the draft; otherwise create a new
    // submitted-state request from scratch.
    let dreq = null;
    if (req.body && req.body.request_id) {
      dreq = await DeletionRequest.findOne({ request_id: req.body.request_id });
    }
    if (!dreq) {
      dreq = new DeletionRequest({
        request_id: crypto.randomUUID(),
        target_user_id: user.email,
        target_email: user.email,
        target_personal_sa_id: user.personal_sa_id || null,
        requested_by: (req.user && (req.user.email || req.user.user_id)) || 'unknown',
        requested_by_role: scope.role,
        org_id: user.org_id || '',
        dept_ids: user.dept_ids || [],
        resources: [],
      });
    }
    // Merge the picker's action choices into the draft's resource list.
    const byKey = new Map(
      (dreq.resources || []).map((r) => [`${r.kind}:${r.resource_id}`, r])
    );
    for (const a of incoming) {
      const key = `${a.kind}:${a.resource_id}`;
      const existing = byKey.get(key) || {
        kind: a.kind,
        resource_id: a.resource_id,
        name: a.name || '',
        current_sa: a.current_sa || null,
        sole_admin: !!a.sole_admin,
      };
      existing.action = a.action;
      existing.action_target = a.action_target || null;
      byKey.set(key, existing);
    }
    dreq.resources = [...byKey.values()];

    // ── Phase H: persist the bulk policy per personal-content bucket ──
    // Body: { personal_content_policies: { documents: 'keep'|'transfer_to_admin'|'delete', ... } }
    const validPolicies = new Set(['keep', 'transfer_to_admin', 'delete']);
    const incomingPolicies =
      (req.body && req.body.personal_content_policies) || {};
    const policies = {};
    for (const [bucket, policy] of Object.entries(incomingPolicies)) {
      if (!validPolicies.has(policy)) {
        return res.status(400).json({
          success: false,
          error: `invalid personal_content policy '${policy}' for bucket '${bucket}'`,
        });
      }
      policies[bucket] = policy;
    }
    dreq.personal_content_policies = policies;

    dreq.status = 'submitted';
    dreq.updated_at = new Date();
    await dreq.save();

    // Flag the user as pending_deletion so they can't auth in
    // (consumers should check deletion_state in their JWT-issuance path).
    user.deletion_state = 'pending_deletion';
    user.updatedAt = new Date();
    await user.save();

    // Enqueue the worker job. Worker applies each action, writes a
    // HandoffReport, then we hard-delete the user.
    const jobId = await workerQueue.enqueue('user.delete_applied', {
      request_id: dreq.request_id,
      user_id: user.email,
      email: user.email,
      org_id: user.org_id || null,
      dept_ids: user.dept_ids || [],
      personal_sa_id: user.personal_sa_id || null,
      requested_by: dreq.requested_by,
      requested_at: new Date().toISOString(),
      resources: dreq.resources.map((r) => ({
        kind: r.kind,
        resource_id: r.resource_id,
        action: r.action,
        action_target: r.action_target,
        current_sa: r.current_sa,
        sole_admin: r.sole_admin,
      })),
      // ── Personal content (Phase H) ──
      // Bucket → policy ('keep' | 'transfer_to_admin' | 'delete').
      // Worker forwards this to Citra-Service /admin/user-content-apply.
      personal_content_policies: policies,
      // Default transfer target = the calling admin. Worker uses this
      // when any bucket is set to 'transfer_to_admin'.
      personal_content_transfer_target:
        (req.user && (req.user.email || req.user.user_id)) || '',
      // Pass the caller's JWT so the worker can authenticate to
      // Citra-Service's admin endpoint (org_admin/super_admin gated).
      admin_auth_token: (req.headers.authorization || '').replace(/^Bearer\s+/i, ''),
    });

    return res.status(202).json({
      success: true,
      request_id: dreq.request_id,
      job_id: jobId,
      target_email: user.email,
      pending_resources: dreq.resources.length,
      message: 'submitted to Citra-Worker; poll /admin/users/' +
        user.email + '/deletion-status?request_id=' + dreq.request_id,
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[userAdmin] delete failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * GET /api/admin/users/:userId/deletion-status?request_id=<>
 *
 * Returns the DeletionRequest doc for status polling.
 */
router.get('/:userId/deletion-status', async (req, res) => {
  try {
    const requestId = req.query.request_id;
    if (!requestId) return res.status(400).json({ success: false, error: 'request_id required' });
    const dreq = await DeletionRequest.findOne({ request_id: requestId }).lean();
    if (!dreq) return res.status(404).json({ success: false, error: 'request not found' });
    return res.status(200).json({ success: true, deletion_request: dreq });
  } catch (err) {
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * GET /api/admin/users
 *
 * Dept- or org-scoped user list for the admin pickers. dept_admin sees
 * users in their dept; org_admin sees their whole org; super_admin sees
 * everyone. Query params:
 *   q       — free-text email substring
 *   dept_id — narrow to one dept (must be a dept the admin has scope over)
 *   limit   — default 50, max 200
 */
router.get('/', async (req, res) => {
  try {
    const u = req.user || {};
    const roles = u.roles || [];
    const limit = Math.min(parseInt(req.query.limit || '50', 10) || 50, 200);
    const q = (req.query.q || '').trim();

    let filter = {};
    if (roles.includes('super_admin')) {
      // no scope restriction
    } else if (roles.includes('org_admin')) {
      filter.org_id = u.org_id;
    } else if (roles.includes('dept_admin')) {
      const adminDepts = u.dept_ids || [];
      if (!adminDepts.length) {
        return res.status(403).json({ success: false, error: 'dept_admin without dept_ids' });
      }
      filter.org_id = u.org_id;
      filter.dept_ids = { $in: adminDepts };
    } else if (roles.includes('decision-app-builder')) {
      // Builders (non-admin) may list users in their OWN org so they can
      // publish a Decision App to a specific person (via that user's Work SA).
      // Org-scoped only — no cross-org visibility.
      filter.org_id = u.org_id;
    } else {
      return res.status(403).json({ success: false, error: 'admin or decision-app-builder role required' });
    }

    if (req.query.dept_id) {
      filter.dept_ids = { $in: [req.query.dept_id] };
    }
    if (q) {
      filter.email = { $regex: q.toLowerCase().replace(/[^a-z0-9@._-]/g, ''), $options: 'i' };
    }

    const users = await CitraAIUser.find(filter)
      .select('email org_id dept_ids roles isActive deletion_state personal_sa_id work_sa_id createdAt lastLogin')
      .limit(limit)
      .lean();

    return res.status(200).json({
      success: true,
      total: users.length,
      users,
    });
  } catch (err) {
    return res.status(500).json({ success: false, error: err.message });
  }
});


// ─── Phase I: invite + edit endpoints ──────────────────────────────────
//
// The auth flows (Google + local register) create users with NO org_id,
// NO dept_ids, and roles=['user']. Those users see + create NOTHING
// until an admin assigns their enterprise context. These endpoints
// close that gap.
//
// RBAC:
//   - super_admin: can invite/edit anyone in any org
//   - org_admin:   can invite/edit users in their own org_id only
//   - dept_admin:  can invite/edit users in their dept_ids only
//                   AND can only assign role <= dept_admin
//                   AND must leave the user's org_id as their own org


const VALID_ROLES = new Set(['user', 'dept_admin', 'org_admin', 'super_admin', 'IT-workflow', 'decision-app-builder']);

// Each role can assign roles up to and INCLUDING this rank.
// (Used to prevent privilege escalation: dept_admin can't make org_admins.)
// IT-workflow is an access flag (grants workflow surface) not an admin tier —
// rank 1 keeps holders out of privilege-escalation math; requireAdmin blocks
// them from role-assignment endpoints regardless. decision-app-builder is the
// same shape: a capability flag (grants the build surface), rank 1 — so any
// admin (rank ≥ 2) can grant it and it never affects escalation math.
const ROLE_RANK = { user: 1, 'IT-workflow': 1, 'decision-app-builder': 1, dept_admin: 2, org_admin: 3, super_admin: 4 };

function _highestRank(roles) {
  return Math.max(0, ...(roles || []).map((r) => ROLE_RANK[r] || 0));
}

function _maxAssignableRank(adminUser) {
  // super_admin can assign any role (rank ≤ 4)
  // org_admin  can assign up to org_admin (rank ≤ 3)
  // dept_admin can assign up to dept_admin (rank ≤ 2)
  const adminRank = _highestRank(adminUser.roles);
  // Floor at 2 so dept_admins can at least promote users to dept_admin.
  return Math.max(2, adminRank);
}


/**
 * POST /api/admin/users
 *
 * Invite (upsert) a user with their enterprise context pre-filled. If
 * the user already exists, the supplied fields are merged in (additive
 * for arrays, replacing for scalars). If they don't exist yet, a
 * placeholder is created — they'll claim it on first Google/local
 * sign-in (matched by email) and the org/dept/roles will already be set.
 *
 * Body:
 *   {
 *     email: "alice@acme.com",            // required
 *     name?: "Alice",
 *     org_id?: "acme",
 *     dept_ids?: ["plant_ops"],
 *     roles?: ["dept_admin"],             // defaults to ["user"]
 *     authProvider?: "google" | "local",  // defaults to "google"
 *     activate?: true                      // default true
 *   }
 */
router.post('/', requireAdmin, async (req, res) => {
  try {
    const adminUser = req.user || {};
    const body = req.body || {};
    const targetEmail = String(body.email || '').trim().toLowerCase();
    if (!targetEmail) {
      return res.status(400).json({ success: false, error: 'email is required' });
    }

    const requestedRoles = Array.isArray(body.roles) && body.roles.length
      ? body.roles
      : ['user'];
    for (const r of requestedRoles) {
      if (!VALID_ROLES.has(r)) {
        return res.status(400).json({ success: false, error: `invalid role: ${r}` });
      }
    }

    // Scope check — admins can only invite into their own scope.
    const adminRoles = new Set(adminUser.roles || []);
    const isSuper = adminRoles.has('super_admin');
    const isOrgAdmin = adminRoles.has('org_admin');
    const isDeptAdmin = adminRoles.has('dept_admin');

    let target_org_id = body.org_id || null;
    let target_dept_ids = Array.isArray(body.dept_ids) ? body.dept_ids : [];

    if (!isSuper) {
      // Force org to admin's own org
      if (!target_org_id) target_org_id = adminUser.org_id || null;
      if (target_org_id !== adminUser.org_id) {
        return res.status(403).json({
          success: false,
          error: 'cannot invite into another org (super_admin required)',
        });
      }
      // dept_admin must keep target's depts inside their own dept scope
      if (!isOrgAdmin && isDeptAdmin) {
        const adminDepts = new Set(adminUser.dept_ids || []);
        const stray = target_dept_ids.filter((d) => !adminDepts.has(d));
        if (stray.length) {
          return res.status(403).json({
            success: false,
            error: `dept_admin can only assign their own depts (${[...adminDepts].join(',')}); blocked: ${stray.join(',')}`,
          });
        }
      }
    }

    // Privilege-escalation guard
    const maxAssign = _maxAssignableRank(adminUser);
    const requestedRank = _highestRank(requestedRoles);
    if (requestedRank > maxAssign) {
      return res.status(403).json({
        success: false,
        error: `cannot assign role rank ${requestedRank} (your max is ${maxAssign})`,
      });
    }

    // Upsert
    let user = await CitraAIUser.findOne({ email: targetEmail });
    const now = new Date();
    if (user) {
      // Merge — additive for dept_ids, replacing for roles/org_id when provided.
      if (target_org_id) user.org_id = target_org_id;
      if (target_dept_ids.length) {
        user.dept_ids = [...new Set([...(user.dept_ids || []), ...target_dept_ids])];
      }
      const merged = new Set([...(user.roles || ['user']), ...requestedRoles]);
      merged.add('user');
      user.roles = [...merged];
      if (body.name) user.name = body.name;
      if (body.activate !== false) user.isActive = true;
      user.updatedAt = now;
      await user.save();
      try { await mirrorOrgChange(user); } catch (e) { console.warn('[userAdmin] mirrorOrgChange:', e.message); }
    } else {
      // Create placeholder. The user will claim it on first sign-in
      // (Google or local) — auth flows match by email and update the
      // doc instead of duplicating it.
      user = await CitraAIUser.create({
        email: targetEmail,
        name: body.name || '',
        authProvider: body.authProvider || 'google',
        emailVerified: false,
        isActive: body.activate !== false,
        org_id: target_org_id || null,
        dept_ids: target_dept_ids,
        roles: [...new Set(['user', ...requestedRoles])],
        createdAt: now,
        updatedAt: now,
      });
      try { await ensurePersonalSA(user); } catch (e) { console.warn('[userAdmin] ensurePersonalSA:', e.message); }
      try { await ensureWorkSA(user);    } catch (e) { console.warn('[userAdmin] ensureWorkSA:',    e.message); }
    }

    return res.status(200).json({
      success: true,
      user: {
        email: user.email,
        org_id: user.org_id,
        dept_ids: user.dept_ids,
        roles: user.roles,
        isActive: user.isActive,
        emailVerified: user.emailVerified,
        deletion_state: user.deletion_state,
      },
      created: !user.lastLogin,  // never logged in yet
      invited_by: adminUser.email || adminUser.user_id,
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[userAdmin] invite failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * PATCH /api/admin/users/:userId
 *
 * Edit a user's enterprise context (org_id, dept_ids, roles, name,
 * isActive). Same RBAC as invite: scope-restricted for org_admin /
 * dept_admin, no privilege escalation past their own role.
 *
 * Body fields (all optional — only supplied ones are updated):
 *   { name, org_id, dept_ids, roles, isActive }
 */
router.patch('/:userId', requireAdmin, async (req, res) => {
  try {
    const adminUser = req.user || {};
    const target = _toUserId(req.params.userId);
    const user = await CitraAIUser.findOne({ email: target });
    if (!user) return res.status(404).json({ success: false, error: 'user not found' });

    // Scope check — same rules as invite.
    const scope = _isAdminInScope(adminUser, user.org_id, user.dept_ids || []);
    if (!scope.ok) {
      return res.status(403).json({
        success: false,
        error: 'org_admin / dept_admin (overlapping dept) / super_admin required',
      });
    }

    const body = req.body || {};
    const adminRoles = new Set(adminUser.roles || []);
    const isSuper = adminRoles.has('super_admin');

    // org_id change is super_admin-only (mass-moves a user between orgs)
    if (typeof body.org_id !== 'undefined' && body.org_id !== user.org_id) {
      if (!isSuper) {
        return res.status(403).json({
          success: false,
          error: 'changing org_id requires super_admin',
        });
      }
      user.org_id = body.org_id || null;
    }

    if (Array.isArray(body.dept_ids)) {
      // dept_admin can only assign their own depts
      if (!isSuper && !adminRoles.has('org_admin')) {
        const adminDepts = new Set(adminUser.dept_ids || []);
        const stray = body.dept_ids.filter((d) => !adminDepts.has(d));
        if (stray.length) {
          return res.status(403).json({
            success: false,
            error: `dept_admin can only assign their own depts; blocked: ${stray.join(',')}`,
          });
        }
      }
      user.dept_ids = [...new Set(body.dept_ids)];
    }

    if (Array.isArray(body.roles)) {
      for (const r of body.roles) {
        if (!VALID_ROLES.has(r)) {
          return res.status(400).json({ success: false, error: `invalid role: ${r}` });
        }
      }
      const maxAssign = _maxAssignableRank(adminUser);
      const requestedRank = _highestRank(body.roles);
      if (requestedRank > maxAssign) {
        return res.status(403).json({
          success: false,
          error: `cannot assign role rank ${requestedRank} (your max is ${maxAssign})`,
        });
      }
      const merged = new Set(body.roles);
      merged.add('user');
      user.roles = [...merged];
    }

    if (typeof body.name === 'string') user.name = body.name;
    if (typeof body.isActive === 'boolean') user.isActive = body.isActive;
    // Allow super_admin to fix broken SA pointers (the homepanel warning
    // banner can land users here via the "contact admin" CTA).
    if (typeof body.personal_sa_id !== 'undefined') {
      if (!isSuper) {
        return res.status(403).json({ success: false, error: 'editing personal_sa_id requires super_admin' });
      }
      user.personal_sa_id = body.personal_sa_id || null;
    }
    if (typeof body.work_sa_id !== 'undefined') {
      if (!isSuper) {
        return res.status(403).json({ success: false, error: 'editing work_sa_id requires super_admin' });
      }
      user.work_sa_id = body.work_sa_id || null;
    }
    user.updatedAt = new Date();
    await user.save();

    // Mirror org/dept changes to both SAs so SA-owned resources stay
    // correctly scoped (dept-admin roll-up needs SA.dept_ids in sync).
    try {
      await mirrorOrgChange(user);
    } catch (err) {
      console.warn('[userAdmin] mirrorOrgChange failed (continuing):', err.message);
    }
    try {
      await mirrorWorkSAOrgChange(user);
    } catch (err) {
      console.warn('[userAdmin] mirrorWorkSAOrgChange failed (continuing):', err.message);
    }

    return res.status(200).json({
      success: true,
      user: {
        email: user.email,
        name: user.name,
        org_id: user.org_id,
        dept_ids: user.dept_ids,
        roles: user.roles,
        isActive: user.isActive,
        personal_sa_id: user.personal_sa_id,
        work_sa_id: user.work_sa_id,
        deletion_state: user.deletion_state,
      },
      updated_by: adminUser.email || adminUser.user_id,
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[userAdmin] patch failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


/**
 * POST /api/admin/users/:userId/ensure-sas
 *
 * Idempotently (re-)mint a user's Personal + Work SAs and stamp their
 * ids on the user doc. Used by the admin panel's "Fix Service Accounts"
 * button when a user reports the home-panel warning that their SAs
 * are missing (homePanel surfaces it when JWT lacks personal_sa_id /
 * work_sa_id — usually means the user pre-dates the work-SA rollout
 * or org_id was unset at signup).
 *
 * Refuses to provision if the user has no org_id (SAs are deterministic
 * on org_id) — the admin must assign org_id first via the regular PATCH.
 */
router.post('/:userId/ensure-sas', requireAdmin, async (req, res) => {
  try {
    const adminUser = req.user || {};
    const target = _toUserId(req.params.userId);
    const user = await CitraAIUser.findOne({ email: target });
    if (!user) return res.status(404).json({ success: false, error: 'user not found' });

    const scope = _isAdminInScope(adminUser, user.org_id, user.dept_ids || []);
    if (!scope.ok) {
      return res.status(403).json({
        success: false,
        error: 'org_admin / dept_admin (overlapping dept) / super_admin required',
      });
    }

    if (!user.org_id) {
      return res.status(400).json({
        success: false,
        error: 'user has no org_id — assign one via PATCH first; SAs are deterministic on org_id',
      });
    }

    let personalSA = null;
    let workSA = null;
    try { personalSA = await ensurePersonalSA(user); }
    catch (e) { console.warn('[userAdmin] ensure-sas personal failed:', e.message); }
    try { workSA = await ensureWorkSA(user); }
    catch (e) { console.warn('[userAdmin] ensure-sas work failed:', e.message); }

    return res.status(200).json({
      success: true,
      user: {
        email: user.email,
        org_id: user.org_id,
        dept_ids: user.dept_ids,
        personal_sa_id: user.personal_sa_id,
        work_sa_id: user.work_sa_id,
      },
      provisioned: {
        personal: !!personalSA,
        work: !!workSA,
      },
      ensured_by: adminUser.email || adminUser.user_id,
    });
  } catch (err) {
    console.error('[userAdmin] ensure-sas failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


// ──────────────────────────────────────────────────────────────────────
// Impersonation — super_admin-only. Mints a normal user JWT for any
// target user so admins can reproduce issues + run demo flows without
// shadow-creating accounts. Every mint is audited in
// `impersonation_audit`; the JWT carries `act` + `impersonation_id`
// claims for downstream visibility.
// ──────────────────────────────────────────────────────────────────────

const IMPERSONATION_TTL_SECONDS = 14400; // 4 hours — extended for ACME-POWER demo sessions

const Org = require('../models/Org');

const requireSuperAdmin = (req, res, next) => {
  const roles = (req.user && req.user.roles) || [];
  if (!roles.includes('super_admin')) {
    return res.status(403).json({
      success: false,
      error: 'super_admin required for impersonation',
    });
  }
  next();
};

/**
 * GET /api/admin/users/impersonation-candidates
 *
 * The "pick a demo persona" picker for super_admins. Returns all users
 * whose org is flagged `is_demo: true`, grouped by tenant org so the UI
 * can render them as "Acme Cement → Anita Rao / Vikram Singh / …".
 *
 * Demo personas are seeded via demo-data/scripts/seed_demo_users.py;
 * they don't have passwords and can't log in directly. Impersonation is
 * the ONLY way to act as them, which is the whole point of the demo
 * flow: a Citra super_admin clicks "Login as Anita" and walks the
 * prospect through their company's data.
 *
 * Excludes: deactivated/deleted users, the caller themselves.
 */
router.get('/impersonation-candidates', requireSuperAdmin, async (req, res) => {
  try {
    const callerEmail = (req.user && (req.user.email || req.user.user_id)) || null;

    const demoOrgs = await Org.find({ is_demo: true }).select('id name domain').lean();
    if (!demoOrgs.length) {
      return res.status(200).json({ success: true, orgs: [] });
    }
    const demoOrgIds = demoOrgs.map(o => o.id);

    const users = await CitraAIUser.find({
      org_id: { $in: demoOrgIds },
      isActive: { $ne: false },
      deletion_state: { $ne: 'deleted' },
      ...(callerEmail ? { email: { $ne: callerEmail } } : {}),
    })
      .select('email name org_id dept_ids roles personal_sa_id work_sa_id lastLogin')
      .sort({ org_id: 1, email: 1 })
      .lean();

    const byOrg = new Map();
    for (const o of demoOrgs) byOrg.set(o.id, { ...o, users: [] });
    for (const u of users) {
      const bucket = byOrg.get(u.org_id);
      if (bucket) bucket.users.push(u);
    }

    return res.status(200).json({
      success: true,
      orgs: Array.from(byOrg.values()).filter(o => o.users.length > 0),
    });
  } catch (err) {
    console.error('[userAdmin] impersonation-candidates failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});


router.post('/:userId/impersonation-token', requireSuperAdmin, async (req, res) => {
  const targetEmailOrId = _toUserId(req.params.userId);
  if (!targetEmailOrId) {
    return res.status(400).json({ success: false, error: 'userId required' });
  }

  try {
    const target = await CitraAIUser.findOne({
      $or: [
        { email: targetEmailOrId },
        { _id: targetEmailOrId.length === 24 ? targetEmailOrId : undefined },
      ].filter((q) => q._id !== undefined || q.email),
    });
    if (!target) {
      return res.status(404).json({ success: false, error: 'target user not found' });
    }
    if (target.isActive === false) {
      return res.status(409).json({
        success: false,
        error: 'target user is deactivated — reactivate first',
      });
    }
    if (target.deletion_state === 'deleted') {
      return res.status(409).json({
        success: false,
        error: 'target user is deleted',
      });
    }

    const adminEmail = (req.user && (req.user.email || req.user.user_id)) || 'unknown';
    if (target.email === adminEmail) {
      return res.status(400).json({
        success: false,
        error: 'cannot impersonate yourself',
      });
    }

    const impersonationId = crypto.randomUUID();
    // Mint the token with a known jti so "revoke" can blocklist THIS token.
    const tokenJti = crypto.randomUUID();
    const now = new Date();
    const expiresAt = new Date(now.getTime() + IMPERSONATION_TTL_SECONDS * 1000);

    const token = await tokenService.generateToken(target, {
      ttlSeconds: IMPERSONATION_TTL_SECONDS,
      jti: tokenJti,
      extraClaims: {
        act: adminEmail,
        impersonation_id: impersonationId,
      },
    });

    await ImpersonationAudit.create({
      impersonation_id: impersonationId,
      // Stored so the revoke endpoint can add this exact token to the
      // revoked_tokens blocklist (makes revoke a real kill-switch, not just
      // an audit flag).
      jti: tokenJti,
      admin_email: adminEmail,
      admin_user_id: (req.user && req.user.user_id) || null,
      target_email: target.email,
      target_org_id: target.org_id || null,
      target_dept_ids: target.dept_ids || [],
      target_roles: target.roles || [],
      issued_at: now,
      expires_at: expiresAt,
      ip: req.ip || (req.headers && req.headers['x-forwarded-for']) || null,
      user_agent: (req.headers && req.headers['user-agent']) || null,
      reason: (req.body && req.body.reason) || '',
    });

    return res.status(200).json({
      success: true,
      impersonation_id: impersonationId,
      token,
      expires_at: expiresAt.toISOString(),
      target_user: {
        email: target.email,
        org_id: target.org_id,
        dept_ids: target.dept_ids,
        roles: target.roles,
      },
    });
  } catch (err) {
    console.error('[userAdmin] impersonation mint failed:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// Revoke endpoint lives in src/routes/impersonationAuditRoutes.js mounted
// at /api/admin/impersonations to give the audit surface its own URL space.

module.exports = router;
