// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * One-shot backfill: provision Work SA for every existing user who has
 * `work_sa_id == null AND org_id != null`. Users created before the
 * Work-SA rollout already have a Personal SA but no Work SA, which
 * means every skill / workflow / smart-app create call returns 400
 * `work_sa_id_missing` until they happen to log in again (which lazily
 * calls `ensureWorkSA`).
 *
 * This script is the eager fix.
 *
 * Usage:
 *   node scripts/backfill_work_sa.js              # live run
 *   node scripts/backfill_work_sa.js --dry-run    # just print counts
 *
 * Safe to re-run. `ensureWorkSA` is idempotent (deterministic SA id +
 * dup-key race handling). The script exits 0 even if some users fail —
 * failures are logged and the rest of the cohort proceeds.
 */

require('dotenv').config();
const mongoose = require('mongoose');
const dns = require('dns');

dns.setServers(['8.8.8.8', '8.8.4.4', '1.1.1.1']);

const MONGODB_URI = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
const DB_NAME = process.env.MONGODB_DATABASE || 'dev';

const DRY = process.argv.includes('--dry-run');


async function run() {
  if (!MONGODB_URI) {
    console.error('No MongoDB connection string in env (MONGODB_CONNECTION_STRING / MONGODB_CONN_STRING / MONGODB_URI).');
    process.exit(1);
  }

  await mongoose.connect(MONGODB_URI, { dbName: DB_NAME });
  console.log(`Connected to ${DB_NAME}`);

  // Defer model + service loading so Mongoose has a live connection first.
  const CitraAIUser = require('../src/models/CitraAIUser');
  const { ensureWorkSA } = require('../src/services/workSAService');

  const filter = {
    $and: [
      { $or: [{ work_sa_id: null }, { work_sa_id: { $exists: false } }, { work_sa_id: '' }] },
      { org_id: { $nin: [null, ''] } },
      { isActive: { $ne: false } },
      { deletion_state: { $ne: 'deleted' } },
    ],
  };

  const total = await CitraAIUser.countDocuments(filter);
  console.log(`Found ${total} user(s) needing Work SA backfill`);

  if (DRY) {
    const sample = await CitraAIUser.find(filter, { email: 1, org_id: 1, dept_ids: 1 }).limit(20).lean();
    console.log('DRY-RUN — first 20:');
    for (const u of sample) {
      console.log(`  ${u.email}  org=${u.org_id}  dept_ids=${JSON.stringify(u.dept_ids || [])}`);
    }
    await mongoose.disconnect();
    return;
  }

  let ok = 0;
  let fail = 0;
  const cursor = CitraAIUser.find(filter).cursor();
  for await (const user of cursor) {
    try {
      const sa = await ensureWorkSA(user);
      if (sa && sa.service_account_id) {
        ok += 1;
        if (ok % 50 === 0) console.log(`  …${ok} done`);
      } else {
        // ensureWorkSA returned null — usually means org_id was actually
        // empty after our filter (e.g. set to '' which $nin caught,
        // but still pass into the function). Treat as fail.
        fail += 1;
        console.warn(`  skip ${user.email}: ensureWorkSA returned null`);
      }
    } catch (err) {
      fail += 1;
      console.warn(`  fail ${user.email}: ${err.message}`);
    }
  }

  console.log(`Done. ok=${ok} fail=${fail}`);
  await mongoose.disconnect();
}

run().catch((err) => {
  console.error('Unhandled:', err);
  process.exit(1);
});
