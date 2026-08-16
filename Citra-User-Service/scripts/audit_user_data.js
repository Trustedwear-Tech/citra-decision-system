// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * One-shot read-only audit: dump the current shape of users/orgs/depts +
 * highlight empty/null/redundant fields so we can plan the cleanup.
 */
require('dotenv').config();
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');
const CitraAIUser = require('../src/models/CitraAIUser');

async function main() {
  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });
  const db = mongoose.connection.db;
  console.log('connected to', mongoose.connection.name);

  // ── Orgs ─────────────────────────────────────────────────────────
  const orgs = await db.collection('orgs').find({}).toArray();
  console.log(`\n=== ORGS (${orgs.length}) ===`);
  for (const o of orgs) {
    console.log(`  ${o.id || o._id}  is_demo=${!!o.is_demo}  name=${o.name}  domain=${o.domain}`);
  }

  // ── Depts ────────────────────────────────────────────────────────
  const depts = await db.collection('depts').find({}).toArray();
  console.log(`\n=== DEPTS (${depts.length}) ===`);
  for (const d of depts) {
    console.log(`  ${d.id || d._id}  org_id=${d.org_id}  name=${d.name}`);
  }

  // ── Users ────────────────────────────────────────────────────────
  const users = await CitraAIUser.find({}).lean();
  console.log(`\n=== USERS (${users.length}) ===`);
  for (const u of users) {
    console.log(`\n  email          : ${u.email}`);
    console.log(`  authProvider   : ${u.authProvider}`);
    console.log(`  org_id         : ${u.org_id}`);
    console.log(`  dept_ids       : ${JSON.stringify(u.dept_ids)}`);
    console.log(`  district_ids   : ${JSON.stringify(u.district_ids)}`);
    console.log(`  entity_type    : ${u.entity_type}`);
    console.log(`  roles          : ${JSON.stringify(u.roles)}`);
    console.log(`  personal_sa_id : ${u.personal_sa_id}`);
    console.log(`  work_sa_id     : ${u.work_sa_id}`);
    console.log(`  isActive       : ${u.isActive}`);
    console.log(`  emailVerified  : ${u.emailVerified}`);
    console.log(`  deletion_state : ${u.deletion_state}`);
    console.log(`  user_type      : ${u.user_type}`);
    console.log(`  passwordHash?  : ${!!u.passwordHash}`);
    console.log(`  googleId?      : ${!!u.googleId}`);
    console.log(`  lastLogin      : ${u.lastLogin}`);
  }

  // ── Field-emptiness audit ─────────────────────────────────────────
  console.log('\n=== EMPTY/REDUNDANT FIELD AUDIT ===');
  const checks = [
    ['null org_id',                        u => !u.org_id],
    ['empty dept_ids',                     u => !u.dept_ids || u.dept_ids.length === 0],
    ['null personal_sa_id',                u => !u.personal_sa_id],
    ['null work_sa_id',                    u => !u.work_sa_id],
    ['entity_type=general (default)',      u => u.entity_type === 'general'],
    ['roles=[user] only',                  u => Array.isArray(u.roles) && u.roles.length === 1 && u.roles[0] === 'user'],
    ['user_type=free',                     u => u.user_type === 'free'],
    ['has empty district_ids',             u => !u.district_ids || u.district_ids.length === 0],
    ['has gdpr_consent block all-null',    u => u.gdpr_consent && !u.gdpr_consent.terms_accepted_at && !u.gdpr_consent.privacy_accepted_at],
    ['has stale emailVerificationToken',   u => !!u.emailVerificationToken],
    ['has stale passwordResetToken',       u => !!u.passwordResetToken],
    ['device_fingerprints empty',          u => Array.isArray(u.device_fingerprints) && u.device_fingerprints.length === 0],
  ];
  for (const [label, pred] of checks) {
    const hits = users.filter(pred).map(u => u.email);
    if (hits.length) console.log(`  ${label.padEnd(38)} ${hits.length}/${users.length}  ${hits.slice(0, 6).join(', ')}${hits.length>6?', …':''}`);
  }

  // ── ServiceAccount summary ───────────────────────────────────────
  const sas = await db.collection('serviceaccounts').find({}).toArray();
  console.log(`\n=== SERVICE ACCOUNTS (${sas.length}) ===`);
  const byOrg = {};
  for (const sa of sas) (byOrg[sa.org_id] ||= []).push(sa.service_account_id);
  for (const [org, ids] of Object.entries(byOrg)) {
    console.log(`  org=${org}  count=${ids.length}`);
    for (const id of ids) console.log(`    ${id}`);
  }

  await mongoose.disconnect();
}
main().catch(e => { console.error(e); process.exit(1); });
