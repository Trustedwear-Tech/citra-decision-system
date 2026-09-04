// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * orgSeedService — read config/orgs.seed.json on startup, upsert each entry
 *
 * Ships EMPTY. It used to carry { id: "citra-ai", name: "Citra AI" }, and this
 * runs on EVERY startup of EVERY deployment -- so every self-hosted install
 * grew an organisation named after the vendor, sitting in its kill-switch
 * console next to the operator's own. Same defect as config/depts.seed.json,
 * which shipped a "Citra Software" department for the same reason.
 *
 * Emptying it is safe: the block at the end of seedOrgs() creates the
 * deployment's own ORG_ID when the seed file does not name it, which is now
 * always. The organisations of a deployment are the operator's to declare.
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

  // The DEPLOYMENT org must exist, whether or not the seed file names it.
  //
  // validateDeploymentOrg() throws when ORG_ID has no row here, and server.js
  // calls it a few lines after this function -- so an ORG_ID that is not in
  // orgs.seed.json crash-loops the service. On a fresh install that is a
  // deadlock, because the thing that would create the org (seed-demo.sh) is
  // run by start.sh only AFTER it has waited for this service to report
  // healthy, which it never does.
  //
  // It applied to both wizard paths: the demo pins ORG_ID=acme-bank and the
  // own-database path defaults it to my-org, and orgs.seed.json ships only
  // citra-ai. deptSeed already seeds departments for ORG_ID, so creating the
  // org itself is the consistent thing to do rather than the special case.
  const deploymentOrgId = (process.env.ORG_ID || '').trim();
  if (deploymentOrgId && !entries.some((e) => e && e.id === deploymentOrgId)) {
    const existing = await Org.findOne({ id: deploymentOrgId }).lean();
    if (!existing) {
      await Org.updateOne(
        { id: deploymentOrgId },
        { $set: { id: deploymentOrgId, name: deploymentOrgId, domain: null, is_demo: false } },
        { upsert: true }
      );
      upserted++;
      console.log(`[orgSeed] created deployment org '${deploymentOrgId}' (ORG_ID, not in the seed file)`);
    }
  }

  console.log(`[orgSeed] ✅ upserted=${upserted}`);
}

module.exports = { seedOrgs };
