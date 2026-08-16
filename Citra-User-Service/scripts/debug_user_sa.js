// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

// One-off debug: dump everything we know about a user + their SAs.
// Run: node scripts/debug_user_sa.js [email]
require('dotenv').config();
// Node's default resolver can't reach the system DNS in this environment;
// point at public resolvers so the Atlas SRV lookup works.
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');
const CitraAIUser = require('../src/models/CitraAIUser');
const ServiceAccount = require('../src/models/ServiceAccount');

const TARGET = (process.argv[2] || 'rohit@trustedweartech.com').toLowerCase();

async function main() {
  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });
  console.log('connected to', mongoose.connection.name);

  const u = await CitraAIUser.findOne({ email: TARGET }).lean();
  if (!u) {
    console.log(`NO user found for ${TARGET}`);
    // also search by partial email in case of typos
    const similar = await CitraAIUser.find({
      email: { $regex: 'rohit', $options: 'i' },
    }).select('email org_id work_sa_id personal_sa_id authProvider').limit(20).lean();
    console.log('similar emails:', similar);
  } else {
    console.log('USER DOC:');
    console.log(JSON.stringify({
      _id: u._id,
      email: u.email,
      authProvider: u.authProvider,
      org_id: u.org_id,
      dept_ids: u.dept_ids,
      roles: u.roles,
      personal_sa_id: u.personal_sa_id,
      work_sa_id: u.work_sa_id,
      isActive: u.isActive,
      emailVerified: u.emailVerified,
      createdAt: u.createdAt,
      updatedAt: u.updatedAt,
      lastLogin: u.lastLogin,
    }, null, 2));

    // Look up SAs by both expected ids
    const saIds = [u.personal_sa_id, u.work_sa_id].filter(Boolean);
    if (saIds.length) {
      const sas = await ServiceAccount.find({ service_account_id: { $in: saIds } }).lean();
      console.log(`\nSAs found (${sas.length}/${saIds.length} expected):`);
      for (const sa of sas) {
        console.log(JSON.stringify({
          service_account_id: sa.service_account_id,
          org_id: sa.org_id,
          dept_ids: sa.dept_ids,
          is_personal: sa.is_personal,
          is_active: sa.is_active,
          admins: sa.admins,
        }, null, 2));
      }
      const missing = saIds.filter(id => !sas.some(s => s.service_account_id === id));
      if (missing.length) console.log('MISSING SA docs for ids:', missing);
    } else {
      console.log('\nuser has no SA ids stamped');
    }

    // Also search for any SAs that reference this user as admin/member
    const ownedByUser = await ServiceAccount.find({
      $or: [{ admins: u.email }, { created_by_user_id: u.email }],
    }).select('service_account_id org_id is_personal is_active admins').lean();
    console.log(`\nALL SAs admin/owned-by ${u.email}: ${ownedByUser.length}`);
    for (const sa of ownedByUser) {
      console.log('  -', sa.service_account_id, '| org=', sa.org_id, '| personal=', sa.is_personal, '| active=', sa.is_active);
    }
  }

  // What workSAIdFor() would compute right now for this user
  if (u) {
    const { workSAIdFor } = require('../src/services/workSAService');
    console.log('\nworkSAIdFor(user) =', workSAIdFor(u));
    const { personalSAIdFor } = require('../src/services/personalSAService');
    if (typeof personalSAIdFor === 'function') {
      console.log('personalSAIdFor(user) =', personalSAIdFor(u));
    }
  }

  console.log('\nORG_ID env =', JSON.stringify(process.env.ORG_ID));

  // Mint a JWT exactly like the login path would, then decode and print
  // the claims that the UI banner reads.
  if (u) {
    const tokenService = require('../src/services/tokenService');
    const userDoc = await CitraAIUser.findOne({ email: u.email });
    const token = await tokenService.generateToken(userDoc);
    const jwt = require('jsonwebtoken');
    const decoded = jwt.decode(token);
    console.log('\nFRESH JWT CLAIMS (what UI banner sees):');
    console.log(JSON.stringify({
      email: decoded.email,
      org_id: decoded.org_id,
      personal_sa_id: decoded.personal_sa_id,
      work_sa_id: decoded.work_sa_id,
      service_account_admin_of: decoded.service_account_admin_of,
      iat: decoded.iat,
      exp: decoded.exp,
    }, null, 2));
    console.log('\nBanner condition: !!work_sa_id =', !!decoded.work_sa_id);
    console.log('If true → banner should NOT show.');
  }

  await mongoose.disconnect();
}

main().catch(e => { console.error(e); process.exit(1); });
