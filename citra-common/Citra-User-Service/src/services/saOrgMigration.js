// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Cross-collection rewrite when a user's Personal/Work SA changes id
 * (org-move case). Without this, every resource on the OLD SA becomes
 * orphaned — invisible to the user in Mine/Shared/Admin tabs.
 *
 * Trigger: `mirrorOrgChange` / `mirrorWorkSAOrgChange` after they mint
 * the new SA id. The OLD SA doc is left in place by design (historical
 * audit), but all live resources move to the new SA so the user keeps
 * seeing their work.
 *
 * Single-tenant + pre-prod: we use Mongo `update_many` per collection,
 * not a background job. The PATCH that triggered this is already
 * happening server-side; users moving across orgs is rare enough that
 * inline rewrites are acceptable. Per-collection failures are logged
 * and skipped — the user-doc update has already succeeded and the
 * remaining collections can be retried by an admin via the same PATCH.
 *
 * NOTE: We do NOT rewrite the SA's own document. The OLD SA stays; the
 * NEW SA is freshly minted by the caller. Only resource docs that
 * reference the SA's id in their `owner_id` get updated.
 */

const mongoose = require('mongoose');

// Folders/workflows/smart-apps live in whichever Mongo DB
// citra-service writes to. In this deployment that's the same `dev` DB
// the user-service uses (the legacy `citra` default predates the
// single-DB consolidation). Resolve from env first; fall back to the
// active connection's DB so the cross-collection rewrite hits the
// actual home of these docs.
const CITRA_DB = process.env.CITRA_APP_DB
  || process.env.MONGODB_DATABASE
  || (mongoose.connection && mongoose.connection.name)
  || 'dev';

// Mapping of every collection that stamps `owner_id = <sa_id>` and which
// SA tier owns which collections. Mirror of the worker's
// `_RESOURCE_KINDS` + `PERSONAL_COLLECTIONS` lists.
//
// `org_id_field` is the dotted path to the org_id field on each doc —
// some live at top-level, smart-apps nest under `app_spec`.
const PERSONAL_SA_COLLECTIONS = [
  { collection: 'folders',          owner_path: 'owner_id', org_path: 'org_id' },
  { collection: 'presentations',    owner_path: 'owner_id', org_path: 'org_id' },
  { collection: 'printables',       owner_path: 'owner_id', org_path: 'org_id' },
  { collection: 'composer_reports', owner_path: 'owner_id', org_path: 'org_id' },
  { collection: 'diagrams',         owner_path: 'owner_id', org_path: 'org_id' },
];

const WORK_SA_COLLECTIONS = [
  { collection: 'Workflows',  owner_path: 'owner_id',          org_path: 'org_id' },
  // Published SmartApps live in ``smartapp_apps`` (smart-app-service
  // APPS_COLLECTION), not ``smart_apps`` — the old name matched nothing, so a
  // cross-org Work-SA move silently left apps owned by the OLD SA id.
  { collection: 'smartapp_apps', owner_path: 'app_spec.owner_id', org_path: 'app_spec.org_id' },
];


function _citraDb() {
  if (!mongoose.connection || mongoose.connection.readyState !== 1) {
    throw new Error('mongoose not connected — cannot reach citra DB');
  }
  return mongoose.connection.client.db(CITRA_DB);
}


/**
 * Rewrite every resource whose `<owner_path>` is `oldSaId` to point at
 * `newSaId`, and bump its `<org_path>` to `newOrgId`. Also updates
 * `vault_shares.owner_sa_id` for folder shares (Personal SA tier only).
 *
 * Returns a per-collection count for the audit trail.
 */
async function _rewriteForSpecs(specs, { oldSaId, newSaId, newOrgId, extraOps = [] }) {
  if (!oldSaId || !newSaId || oldSaId === newSaId) return {};
  const db = _citraDb();
  const counts = {};
  for (const spec of specs) {
    try {
      const filter = { [spec.owner_path]: oldSaId };
      const set = { [spec.owner_path]: newSaId };
      if (newOrgId && spec.org_path) set[spec.org_path] = newOrgId;
      const res = await db.collection(spec.collection).updateMany(filter, { $set: set });
      counts[spec.collection] = res.modifiedCount || 0;
    } catch (err) {
      // Don't let one collection's failure abort the rest — but a failed
      // rewrite means the OLD SA's resources are ORPHANED, so log at ERROR
      // (the LogMonitor watches ERROR lines and alerts IT) — never a quiet
      // warn that reads like success. counts[...]=-1 marks the failure for
      // the caller / audit.
      console.error(
        `[saOrgMigration] ORPHAN-RISK: ${spec.collection} rewrite failed (oldSa=${oldSaId} -> newSa=${newSaId}); resources left on old SA:`,
        err.message,
      );
      counts[spec.collection] = -1;
    }
  }
  for (const op of extraOps) {
    try {
      counts[op.label] = await op.run(db);
    } catch (err) {
      console.error(
        `[saOrgMigration] ORPHAN-RISK: ${op.label} failed (oldSa=${oldSaId}):`,
        err.message,
      );
      counts[op.label] = -1;
    }
  }
  return counts;
}


/**
 * Migrate every Personal-SA-owned resource (folders, presentations,
 * printables, reports, diagrams) from oldSaId to newSaId. Also rewrites
 * vault_shares.owner_sa_id so existing per-recipient shares on the
 * affected folders keep resolving.
 */
async function migratePersonalSAResources({ oldSaId, newSaId, newOrgId }) {
  const extraOps = [{
    label: 'vault_shares',
    run: async (db) => {
      const res = await db.collection('vault_shares').updateMany(
        { owner_sa_id: oldSaId },
        { $set: { owner_sa_id: newSaId, updated_at: new Date() } },
      );
      return res.modifiedCount || 0;
    },
  }];
  return _rewriteForSpecs(PERSONAL_SA_COLLECTIONS, { oldSaId, newSaId, newOrgId, extraOps });
}


/**
 * Migrate every Work-SA-owned resource (workflows, smart-apps)
 * from oldSaId to newSaId.
 */
async function migrateWorkSAResources({ oldSaId, newSaId, newOrgId }) {
  return _rewriteForSpecs(WORK_SA_COLLECTIONS, { oldSaId, newSaId, newOrgId });
}


module.exports = {
  migratePersonalSAResources,
  migrateWorkSAResources,
};
