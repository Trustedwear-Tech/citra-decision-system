// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Provision the officer the embed-test site signs in as.
 *
 * Local registration puts a new user in the DEPLOYMENT's default org
 * (deploymentDefaults → ORG_ID) with no department. That is right for a Citra
 * employee and wrong for this test: the card reads Acme Bank's lending data,
 * and discovery scopes sources by the caller's org and departments. A
 * citra-ai user gets a truthful refusal —
 *
 *     source_id=loan_origination not visible to user
 *
 * — which is the org boundary doing its job, not a bug. So the test officer has
 * to actually BE an Acme Bank lending officer.
 *
 * Touches exactly one document, matched by email. Creates nothing.
 *
 *   node scripts/provision_embed_test_officer.js
 */
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const mongoose = require('mongoose');

const EMAIL = process.env.OFFICER_EMAIL || 'embed.test@acme-bank-demo.citra.ai';
const ORG = process.env.OFFICER_ORG || 'acme-bank';
const DEPTS = (process.env.OFFICER_DEPTS || 'lending').split(',');
// dept_admin: the dept-MCP refuses record_credit_decision to a bare 'user'
// (403 "requires dept_admin/org_admin/super_admin"). A credit officer who can
// commit a decision holds it.
const ROLES = (process.env.OFFICER_ROLES || 'user,dept_admin').split(',');

(async () => {
  const uri = process.env.MONGODB_CONNECTION_STRING
    || process.env.MONGODB_CONN_STRING
    || process.env.MONGODB_URI;
  if (!uri) {
    console.error('no Mongo connection string in Citra-User-Service/.env');
    process.exit(1);
  }
  await mongoose.connect(uri, { dbName: process.env.MONGODB_DATABASE || 'citra-ai' });

  const res = await mongoose.connection.db.collection('citraaiusers').updateOne(
    { email: EMAIL },
    {
      $set: {
        org_id: ORG,
        tenant_id: ORG,
        dept_ids: DEPTS,
        roles: ROLES,
        entity_type: 'company',
        // Local registration leaves this false pending an email that never
        // arrives in dev. Login already works without it; set it so the user
        // is not in a half-provisioned state.
        emailVerified: true,
      },
    },
  );

  if (res.matchedCount === 0) {
    console.error(`no user ${EMAIL} — register first:`);
    console.error(`  POST ${'{user-service}'}/api/auth/local/register`);
    process.exit(2);
  }

  const u = await mongoose.connection.db.collection('citraaiusers')
    .findOne({ email: EMAIL }, { projection: { email: 1, org_id: 1, dept_ids: 1, roles: 1 } });
  console.log('provisioned:', JSON.stringify(u));
  await mongoose.disconnect();
})();
