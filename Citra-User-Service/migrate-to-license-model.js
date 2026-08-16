// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * migrate-to-license-model.js
 *
 * One-time migration: transition all existing users from credit-based pay-as-you-go
 * to the unlimited-credits license model.
 *
 * Safe to run multiple times (idempotent).
 *
 * What it does:
 *   1. Sets credit_balance = 999999999 for every userusages document
 *   2. Sets user_type = 'paid' and isActive = true for every user in the users collection
 *   3. Logs a summary
 *
 * Usage:
 *   node migrate-to-license-model.js
 *
 * Requires MONGODB_CONNECTION_STRING (or MONGODB_CONN_STRING / MONGODB_URI) in env.
 * Load from Vault or .env before running:
 *   node -r dotenv/config migrate-to-license-model.js
 */

'use strict';

const { MongoClient } = require('mongodb');

const UNLIMITED_BALANCE = 999999999;

async function migrate() {
  const uri =
    process.env.MONGODB_CONNECTION_STRING ||
    process.env.MONGODB_CONN_STRING ||
    process.env.MONGODB_URI;

  const dbName = process.env.MONGODB_DATABASE || 'citra-ai';

  if (!uri) {
    console.error('❌ MONGODB_CONNECTION_STRING not set. Aborting.');
    process.exit(1);
  }

  const client = new MongoClient(uri, { serverSelectionTimeoutMS: 10000 });

  try {
    await client.connect();
    console.log('✅ Connected to MongoDB');

    const db = client.db(dbName);

    // ── 1. Top up all userusages credit_balance to unlimited ──────────────────
    const usageResult = await db.collection('userusages').updateMany(
      {},
      { $set: { credit_balance: UNLIMITED_BALANCE } }
    );
    console.log(
      `💰 userusages: ${usageResult.matchedCount} matched, ${usageResult.modifiedCount} updated → credit_balance = ${UNLIMITED_BALANCE}`
    );

    // ── 2. Mark all users as paid and active ──────────────────────────────────
    const usersResult = await db.collection('users').updateMany(
      {},
      { $set: { user_type: 'paid', isActive: true } }
    );
    console.log(
      `👥 users: ${usersResult.matchedCount} matched, ${usersResult.modifiedCount} updated → user_type='paid', isActive=true`
    );

    // ── 3. Summary ────────────────────────────────────────────────────────────
    const totalUsers = await db.collection('users').countDocuments();
    const totalUsage = await db.collection('userusages').countDocuments();
    console.log(`\n✅ Migration complete`);
    console.log(`   Total users in DB   : ${totalUsers}`);
    console.log(`   Total usage records : ${totalUsage}`);
    console.log(`   All credit balances : UNLIMITED (${UNLIMITED_BALANCE})`);
    console.log(`   All user types      : paid`);
  } catch (err) {
    console.error('❌ Migration failed:', err);
    process.exit(1);
  } finally {
    await client.close();
    console.log('🔌 MongoDB connection closed');
  }
}

migrate();
