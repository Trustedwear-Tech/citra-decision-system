// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Seed one officer per acme-bank department.
 *
 * Run BY A HUMAN — it registers accounts and therefore needs a password, which
 * you supply and which is never written to the repo or logged.
 *
 *   # prod demo box (from a machine that can reach the user service)
 *   OFFICER_PASSWORD='<choose one>' \
 *   USER_SERVICE_URL=https://<prod-user-service> \
 *   MONGODB_CONNECTION_STRING='<prod uri>' MONGODB_DATABASE=citra-ai \
 *   node scripts/seed_acme_bank_officers.js
 *
 * Two steps per officer, and both matter:
 *
 *   1. REGISTER through the service's own endpoint, so password hashing,
 *      validation and any signup side effects are the real ones. Writing a user
 *      document straight into Mongo would produce an account that cannot log in.
 *   2. PROVISION org_id / dept_ids / roles directly, because local registration
 *      puts a new user in the DEPLOYMENT's default org with no department. A
 *      user left that way gets a truthful refusal from discovery
 *      ("source_id=... not visible to user") — the org boundary working, not a
 *      bug.
 *
 * `dept_admin` is deliberate: the dept-MCP refuses write actions such as
 * record_credit_decision to a bare `user` (403 "requires
 * dept_admin/org_admin/super_admin"). An officer who can commit a decision
 * holds it.
 *
 * Idempotent: an already-registered email is provisioned, not duplicated.
 */
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const mongoose = require('mongoose');

const ORG = 'acme-bank';

// One officer per department, matching the depts already seeded in prod.
const OFFICERS = [
  { email: 'credit.officer@acme-bank.citra.ai',      name: 'Credit Officer',       dept: 'lending' },
  { email: 'collections.officer@acme-bank.citra.ai', name: 'Collections Officer',  dept: 'collections' },
  { email: 'claims.officer@acme-bank.citra.ai',      name: 'Claims Officer',       dept: 'claims' },
  { email: 'sales.officer@acme-bank.citra.ai',       name: 'Sales Officer',        dept: 'sales_distribution' },
  { email: 'ops.manager@acme-bank.citra.ai',         name: 'Operations Manager',   dept: 'central_ops',
    roles: ['user', 'dept_admin', 'org_admin'] },
];

const PASSWORD = process.env.OFFICER_PASSWORD;
const USER_SERVICE = (process.env.USER_SERVICE_URL || '').replace(/\/+$/, '');

async function register(o) {
  const r = await fetch(`${USER_SERVICE}/api/auth/local/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: o.email, password: PASSWORD, name: o.name }),
  });
  const text = await r.text();
  if (r.ok) return 'registered';
  // Already there is a success for our purposes — we still provision below.
  if (/exist|already|duplicate/i.test(text)) return 'already existed';
  throw new Error(`register ${o.email} -> HTTP ${r.status}: ${text.slice(0, 200)}`);
}

(async () => {
  if (!PASSWORD) {
    console.error('OFFICER_PASSWORD is required — this script will not invent one.');
    process.exit(2);
  }
  if (!USER_SERVICE) {
    console.error('USER_SERVICE_URL is required (the service does the hashing).');
    process.exit(2);
  }
  const uri = process.env.MONGODB_CONNECTION_STRING
    || process.env.MONGODB_CONN_STRING
    || process.env.MONGODB_URI;
  if (!uri) {
    console.error('No Mongo connection string — needed for step 2 (provisioning).');
    process.exit(2);
  }

  await mongoose.connect(uri, { dbName: process.env.MONGODB_DATABASE || 'citra-ai' });
  const users = mongoose.connection.db.collection('citraaiusers');

  for (const o of OFFICERS) {
    let how;
    try {
      how = await register(o);
    } catch (e) {
      console.error(`  ✗ ${o.email}: ${e.message}`);
      continue;
    }
    const res = await users.updateOne(
      { email: o.email },
      {
        $set: {
          org_id: ORG,
          tenant_id: ORG,
          dept_ids: [o.dept],
          roles: o.roles || ['user', 'dept_admin'],
          entity_type: 'company',
          // Local registration leaves this false pending an email that never
          // arrives on a demo box. Login works without it; set it so the user
          // is not left half-provisioned.
          emailVerified: true,
        },
      },
    );
    if (res.matchedCount === 0) {
      console.error(`  ✗ ${o.email}: ${how}, but no user document to provision`);
      continue;
    }
    console.log(`  ✓ ${o.email}  (${how})  org=${ORG} dept=${o.dept} roles=${(o.roles || ['user','dept_admin']).join(',')}`);
  }

  await mongoose.disconnect();
  console.log('\nDone. Sign in to the bank demo with any of the above.');
})();
