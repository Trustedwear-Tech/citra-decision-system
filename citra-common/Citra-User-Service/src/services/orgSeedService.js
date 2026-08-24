// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * orgSeedService — read config/orgs.seed.json on startup, upsert each entry
 * into the `orgs` collection.
 *
 * Idempotent: safe to run on every boot. Does NOT prune entries that are in
 * the DB but missing from the seed file — orgs may be created via the admin
 * API (POST /api/admin/orgs) and intentionally not present in the seed.
 * Deletion of an org row goes through the admin API, which refuses if any
 * user still references it.
 *
 * Failures are logged but do NOT abort startup.
 */

const fs = require('fs');
const path = require('path');
const Org = require('../models/Org');

async function seedOrgs() {
  const seedPath = process.env.ORGS_SEED_PATH || './config/orgs.seed.json';
  const absPath = path.isAbsolute(seedPath) ? seedPath : path.join(process.cwd(), seedPath);

  let entries;
  try {
    const raw = fs.readFileSync(absPath, 'utf-8');
    entries = JSON.parse(raw);
  } catch (err) {
    console.warn(`[orgSeed] Could not read seed file at ${absPath}: ${err.message}`);
    return;
  }

  if (!Array.isArray(entries)) {
    console.warn(`[orgSeed] Seed file at ${absPath} is not an array — skipping.`);
    return;
  }

  let upserted = 0;
  for (const e of entries) {
    if (!e || !e.id || !e.name) {
      console.warn('[orgSeed] Skipping invalid entry:', e);
      continue;
    }
    await Org.updateOne(
      { id: e.id },
      {
        $set: {
          id:      e.id,
          name:    e.name,
          domain:  e.domain || null,
          is_demo: !!e.is_demo,
        },
      },
      { upsert: true }
    );
    upserted++;
  }

  console.log(`[orgSeed] ✅ upserted=${upserted}`);
}

module.exports = { seedOrgs };
