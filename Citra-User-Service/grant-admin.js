// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * One-off script: grant super_admin role to a user.
 * Run: node grant-admin.js [email]
 * Default email: rohit@trustedweartech.com
 */
require('dotenv').config();
const mongoose = require('mongoose');

const EMAIL = (process.argv[2] || 'rohit@trustedweartech.com').toLowerCase().trim();
const ROLE  = 'super_admin';

(async () => {
  const uri = process.env.MONGODB_CONNECTION_STRING;
  const dbName = process.env.MONGODB_DATABASE || 'dev';
  if (!uri) {
    console.error('MONGODB_CONNECTION_STRING not set');
    process.exit(1);
  }

  console.log(`Connecting to MongoDB db="${dbName}"...`);
  await mongoose.connect(uri, { dbName });

  const col = mongoose.connection.collection('users');

  const before = await col.findOne(
    { email: EMAIL },
    { projection: { _id: 1, email: 1, roles: 1 } }
  );
  console.log('BEFORE:', JSON.stringify(before));

  if (!before) {
    console.error(`No user found with email="${EMAIL}". Check the email exactly matches what's stored (case-sensitive).`);
    await mongoose.disconnect();
    process.exit(2);
  }

  const res = await col.updateOne(
    { email: EMAIL },
    { $addToSet: { roles: ROLE } }
  );
  console.log(`updateOne → matched=${res.matchedCount}, modified=${res.modifiedCount}`);

  const after = await col.findOne(
    { email: EMAIL },
    { projection: { _id: 1, email: 1, roles: 1 } }
  );
  console.log('AFTER: ', JSON.stringify(after));

  await mongoose.disconnect();
  console.log('Done. Now log out + log back in to get a fresh JWT.');
})().catch(err => {
  console.error('Failed:', err);
  process.exit(1);
});
