// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Promote a user to super_admin. Use after wipe + first login to give
 * rohit@trustedweartech.com (or any Citra employee) platform admin rights.
 *
 * Idempotent — re-runs are no-ops.
 *
 * Usage:
 *   node scripts/promote_super_admin.js <email>
 */
require('dotenv').config();
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');
const CitraAIUser = require('../src/models/CitraAIUser');

async function main() {
  const email = (process.argv[2] || '').trim().toLowerCase();
  if (!email) {
    console.error('Usage: node scripts/promote_super_admin.js <email>');
    process.exit(2);
  }

  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });

  const user = await CitraAIUser.findOne({ email });
  if (!user) {
    console.error(`No user found for ${email}. Sign in via the UI at least once, then re-run.`);
    process.exit(3);
  }

  const before = [...(user.roles || [])];
  const rolesSet = new Set(before);
  rolesSet.add('user');
  rolesSet.add('super_admin');
  user.roles = Array.from(rolesSet);
  await user.save();

  console.log(`✓ ${email}`);
  console.log(`  org_id   : ${user.org_id}`);
  console.log(`  dept_ids : ${JSON.stringify(user.dept_ids)}`);
  console.log(`  roles    : ${JSON.stringify(before)} → ${JSON.stringify(user.roles)}`);
  console.log('\nUser must sign out + sign in to pick up the new role in their JWT.');

  await mongoose.disconnect();
}
main().catch(e => { console.error('fatal:', e); process.exit(1); });
