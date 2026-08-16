// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Migration: Simplify pricing to model-agnostic defaults
 * 
 * - Replaces all model-specific token_pricing keys (gemini, gemini_flash, gemini_pro, grok, runware)
 *   with a single `default` tier using grok-equivalent rates
 * - Sets flat 1.0 credits for image generation and internet grounding
 * - Replaces embedding_pricing with single `per_1k` key
 * - Removes credit_purchase (on-prem, no credit purchases)
 * - Removes collections_search_price (not used)
 * - Removes storage_pricing and upload_pricing (file-size billing disabled)
 * 
 * Usage: node scripts/migrate-to-default-pricing.js
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

  // Find the active pricing config
  const active = await collection.findOne(
    { $or: [{ is_active: true }, { active: true }] },
    { sort: { version: -1 } }
  );

  if (!active) {
    console.error('❌ No active pricing config found');
    process.exit(1);
  }

  console.log(`📋 Found active pricing v${active.version}`);
  console.log('   Current token_pricing keys:', Object.keys(active.token_pricing || {}));
  console.log('   Current embedding_pricing keys:', Object.keys(active.embedding_pricing || {}));
  console.log('   Has credit_purchase:', !!active.credit_purchase);

  // Build the update
  const result = await collection.updateOne(
    { _id: active._id },
    {
      $set: {
        'token_pricing': {
          default: {
            input_per_1k: 0.05,
            output_per_1k: 0.10,
            cached_per_1k: 0.02,
            internet_grounding_price: 1.0,
            image_generation_price: 1.0
          },
          image_generation: {
            default: { image_generation_price: 1.0 }
          }
        },
        'embedding_pricing': {
          per_1k: 0.05
        },
        'description': `Model-agnostic default pricing (migrated from v${active.version})`,
        'updated_at': new Date()
      },
      $unset: {
        'credit_purchase': '',
        'storage_pricing': '',
        'upload_pricing': ''
      }
    }
  );

  console.log(`\n✅ Migration complete (matched: ${result.matchedCount}, modified: ${result.modifiedCount})`);

  // Verify
  const updated = await collection.findOne({ _id: active._id });
  console.log('\n📋 Updated pricing config:');
  console.log('   token_pricing keys:', Object.keys(updated.token_pricing || {}));
  console.log('   token_pricing.default:', JSON.stringify(updated.token_pricing?.default, null, 2));
  console.log('   embedding_pricing:', JSON.stringify(updated.embedding_pricing, null, 2));
  console.log('   credit_purchase:', updated.credit_purchase || '(removed)');
  console.log('   version:', updated.version);

  await mongoose.disconnect();
  console.log('\n🔐 Disconnected from MongoDB');
}

migrate().catch(err => {
  console.error('❌ Migration failed:', err);
  process.exit(1);
});
