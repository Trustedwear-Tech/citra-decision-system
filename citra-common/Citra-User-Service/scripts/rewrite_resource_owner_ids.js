// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Resource owner_id rewrite — one-off cleanup for users whose Personal
 * and Work SA ids changed during the org migration (acme-cement → citra-ai).
 *
 * Symptom this script fixes:
 *   - User logs in fresh; UI shows "0 folders" / "0 skills" / etc.
 *   - Because folder docs still carry owner_id = <old-SA-id>, while the
 *     user's JWT carries the NEW personal_sa_id.
 *
 * For each user with org_id=citra-ai whose email had a matching SA on
 * acme-cement, rewrite every personal-tier and work-tier collection's
 * owner_id from <old> to <new>.
 *
 * Idempotent: a second run finds zero matches.
 */
require('dotenv').config();
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');

const CitraAIUser = require('../src/models/CitraAIUser');
const { migratePersonalSAResources } = require('../src/services/saOrgMigration');
const { migrateWorkSAResources }     = require('../src/services/saOrgMigration');

const APPLY = process.argv.includes('--apply');
const TARGET_ORG = 'citra-ai';

function _slugify(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'anonymous';
}

function oldSaIds(user, oldOrg) {
  const userPart = _slugify(user.email);
  const orgPart  = _slugify(oldOrg);
  return {
    oldPersonal: `svc:personal-${userPart}@${orgPart}.citra.ai`,
    oldWork:     `svc:work-${userPart}@${orgPart}.citra.ai`,
  };
}

async function main() {
  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });
  console.log(`connected to ${mongoose.connection.name} (mode=${APPLY ? 'APPLY' : 'DRY-RUN'})`);

  const users = await CitraAIUser.find({ org_id: TARGET_ORG }).lean();
  console.log(`\nusers on org=${TARGET_ORG}: ${users.length}`);

  for (const u of users) {
    if (!u.personal_sa_id || !u.work_sa_id) {
      console.log(`  skip ${u.email} — missing SA ids`);
      continue;
    }
    // The only previous org we know of was acme-cement; if a user predated
    // the SA system entirely (work_sa_id was null when they were on the
    // demo org), nothing to rewrite. Otherwise compute what their old
    // ids would have been and run the rewrite.
    const { oldPersonal, oldWork } = oldSaIds(u, 'acme-cement');

    if (oldPersonal === u.personal_sa_id && oldWork === u.work_sa_id) {
      console.log(`  skip ${u.email} — still on acme-cement?`);
      continue;
    }

    console.log(`\n  ${u.email}`);
    console.log(`    personal: ${oldPersonal}`);
    console.log(`           →  ${u.personal_sa_id}`);
    console.log(`    work:     ${oldWork}`);
    console.log(`           →  ${u.work_sa_id}`);

    if (!APPLY) continue;

    try {
      const pCounts = await migratePersonalSAResources({
        oldSaId: oldPersonal,
        newSaId: u.personal_sa_id,
        newOrgId: u.org_id,
      });
      console.log('    personal rewrite:', pCounts);
    } catch (e) {
      console.log('    personal rewrite FAILED:', e.message);
    }
    try {
      const wCounts = await migrateWorkSAResources({
        oldSaId: oldWork,
        newSaId: u.work_sa_id,
        newOrgId: u.org_id,
      });
      console.log('    work rewrite:    ', wCounts);
    } catch (e) {
      console.log('    work rewrite FAILED:', e.message);
    }
  }

  console.log('\nDONE');
  await mongoose.disconnect();
}
main().catch(e => { console.error(e); process.exit(1); });
