// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * deptSeedService — read config/depts.seed.json on startup, upsert each entry
 * into the `depts` collection scoped to the deployment's ORG_ID.
 *
 * Ships EMPTY. The departments of a deployment are the operator's to declare,
 * not ours to guess, and an empty file is a no-op (see the guard below).
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

  // An EMPTY seed means "this deployment does not seed departments", and must
  // do nothing at all. It cannot fall through to the prune below: that deletes
  // every dept whose id is $nin the seed ids, and $nin [] matches everything —
  // so an empty file would wipe the org's departments on every boot, silently,
  // including any created through the admin UI.
  //
  // Empty is also the SHIPPED default. This file used to contain
  // { id: "citra-software", name: "Citra Software" } — a department in OUR org
  // — and seedDepts upserts it into whatever ORG_ID the operator set. Every
  // self-hosted install therefore had a "Citra Software" department created
  // inside its own organisation, on every restart, and pruning made the file
  // authoritative so deleting it by hand did not stick.
  if (entries.length === 0) {
    console.log('[deptSeed] seed file is empty — nothing to seed, nothing pruned.');
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
