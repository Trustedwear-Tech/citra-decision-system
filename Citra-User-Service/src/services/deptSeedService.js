// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * deptSeedService — read config/depts.seed.json on startup, upsert each entry
 * into the `depts` collection scoped to the deployment's ORG_ID.
 *
 * Idempotent: safe to run on every boot. Removes entries that are in the DB
 * but no longer in the seed file (so deleting from JSON = deleting from DB).
 * If you want to preserve hand-edited depts, drop the prune step.
 *
 * Failures are logged but do NOT abort startup — the manage-users dropdown
 * just falls back to an empty list.
 */

const fs = require('fs');
const path = require('path');
const Dept = require('../models/Dept');

async function seedDepts() {
  const orgId = (process.env.ORG_ID || '').trim();
  if (!orgId) {
    console.warn('[deptSeed] ORG_ID not set — skipping dept seed (single-tenant disabled).');
    return;
  }

  const seedPath = process.env.DEPTS_SEED_PATH || './config/depts.seed.json';
  const absPath = path.isAbsolute(seedPath) ? seedPath : path.join(process.cwd(), seedPath);

  let entries;
  try {
    const raw = fs.readFileSync(absPath, 'utf-8');
    entries = JSON.parse(raw);
  } catch (err) {
    console.warn(`[deptSeed] Could not read seed file at ${absPath}: ${err.message}`);
    return;
  }

  if (!Array.isArray(entries)) {
    console.warn(`[deptSeed] Seed file at ${absPath} is not an array — skipping.`);
    return;
  }

  let upserted = 0;
  const seedIds = new Set();
  for (const e of entries) {
    if (!e || !e.id || !e.name) {
      console.warn('[deptSeed] Skipping invalid entry:', e);
      continue;
    }
    seedIds.add(e.id);
    await Dept.updateOne(
      { org_id: orgId, id: e.id },
      {
        $set: {
          name:      e.name,
          parent_id: e.parent_id || null,
          org_id:    orgId,
          id:        e.id,
        },
      },
      { upsert: true }
    );
    upserted++;
  }

  // Prune depts that were removed from the seed file. Hand-edits via UI
  // shouldn't live in the seed model — they belong in a separate admin
  // surface. Until that exists, the seed file is the source of truth.
  const removed = await Dept.deleteMany({
    org_id: orgId,
    id: { $nin: Array.from(seedIds) },
  });

  console.log(`[deptSeed] ✅ org=${orgId} upserted=${upserted} pruned=${removed.deletedCount}`);
}

module.exports = { seedDepts };
