// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Simulate the GET /api/admin/users/impersonation-candidates handler
 * against the dev DB to confirm demo personas surface per tenant.
 * Read-only.
 */
require('dotenv').config();
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');
const CitraAIUser = require('../src/models/CitraAIUser');
const Org = require('../src/models/Org');

async function main() {
  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });

  const demoOrgs = await Org.find({ is_demo: true }).select('id name domain').lean();
  console.log(`demo orgs (is_demo=true): ${demoOrgs.length}`);
  for (const o of demoOrgs) console.log(`  - ${o.id} | ${o.name} | ${o.domain}`);

  const demoOrgIds = demoOrgs.map(o => o.id);
  const users = await CitraAIUser.find({
    org_id: { $in: demoOrgIds },
    isActive: { $ne: false },
    deletion_state: { $ne: 'deleted' },
  })
    .select('email name org_id dept_ids roles')
    .sort({ org_id: 1, email: 1 })
    .lean();

  console.log(`\ncandidates: ${users.length}\n`);
  for (const o of demoOrgs) {
    const usersInOrg = users.filter(u => u.org_id === o.id);
    if (!usersInOrg.length) continue;
    console.log(`▼ ${o.name || o.id}  (${usersInOrg.length} persona${usersInOrg.length === 1 ? '' : 's'})`);
    for (const u of usersInOrg) {
      console.log(`    ${u.name || ''} <${u.email}>  roles=${JSON.stringify(u.roles)}  depts=${JSON.stringify(u.dept_ids)}`);
    }
  }

  console.log('\n=== sanity check: any non-demo user accidentally tagged into a demo org? ===');
  const citraUsers = await CitraAIUser.find({ org_id: 'citra-ai' }).select('email org_id dept_ids').lean();
  console.log(`citra-ai users: ${citraUsers.length}`);
  for (const u of citraUsers) {
    if (!u.dept_ids?.includes('citra-software')) {
      console.log(`  ⚠ ${u.email} on citra-ai but dept_ids=${JSON.stringify(u.dept_ids)}`);
    }
  }
  const ok = citraUsers.every(u => u.dept_ids?.includes('citra-software'));
  console.log(ok ? '  ✓ all citra-ai users have citra-software dept' : '  ✗ at least one citra-ai user is mis-departed');

  await mongoose.disconnect();
}
main().catch(e => { console.error(e); process.exit(1); });
