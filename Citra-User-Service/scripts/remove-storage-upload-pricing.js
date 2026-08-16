// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Migration: Remove storage_pricing and upload_pricing from pricing configs
 * 
 * These fields are no longer used — file-size-based billing was disabled.
 * Billing is handled exclusively via token-based costs (embeddings, queries).
 * 
 * Usage: node scripts/remove-storage-upload-pricing.js
 */

require('dotenv').config();
const mongoose = require('mongoose');
const dns = require('dns');

// Configure DNS for MongoDB Atlas SRV resolution
dns.setServers(['8.8.8.8', '8.8.4.4', '1.1.1.1']);

const MONGODB_URI = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
const DB_NAME = process.env.MONGODB_DATABASE || 'dev';

async function migrate() {
  if (!MONGODB_URI) {
    console.error('❌ No MongoDB connection string found in environment');
    process.exit(1);
  }

  await mongoose.connect(MONGODB_URI, { dbName: DB_NAME });
  console.log(`✅ Connected to MongoDB (${DB_NAME})`);

  const db = mongoose.connection.db;
  const collection = db.collection('pricingconfigs');

  // Show current state
  const active = await collection.findOne({ $or: [{ is_active: true }, { active: true }] }, { sort: { version: -1 } });
  if (!active) {
    console.error('❌ No active pricing config found');
    await mongoose.disconnect();
    process.exit(1);
  }

  console.log(`\n📋 Current active pricing (v${active.version}):`);
  console.log(`   Has storage_pricing: ${!!active.storage_pricing}`);
  console.log(`   Has upload_pricing: ${!!active.upload_pricing}`);

  if (!active.storage_pricing && !active.upload_pricing) {
    console.log('\n✅ Fields already removed — nothing to do.');
    await mongoose.disconnect();
    return;
  }

  // Remove storage_pricing and upload_pricing from all documents
  const result = await collection.updateMany(
    {},
    { $unset: { storage_pricing: '', upload_pricing: '' } }
  );

  console.log(`\n✅ Removed storage_pricing and upload_pricing from ${result.modifiedCount} document(s)`);

  // Verify
  const updated = await collection.findOne({ _id: active._id });
  console.log(`\n📋 Verified active pricing (v${updated.version}):`);
  console.log(`   Has storage_pricing: ${!!updated.storage_pricing}`);
  console.log(`   Has upload_pricing: ${!!updated.upload_pricing}`);
  console.log(`   Token pricing keys: ${Object.keys(updated.token_pricing || {}).join(', ')}`);
  console.log(`   Embedding pricing: ${JSON.stringify(updated.embedding_pricing)}`);

  await mongoose.disconnect();
  console.log('\n✅ Migration complete');
}

migrate().catch(err => {
  console.error('❌ Migration failed:', err);
  process.exit(1);
});
