// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * One-off migration: add google:4@1 pricing to the active pricing_configs document.
 * Run: node add-google-4at1-pricing.js
 */
require('dotenv').config();
const mongoose = require('mongoose');

async function run() {
  const connectionString = process.env.MONGODB_CONNECTION_STRING ||
                           process.env.MONGODB_CONN_STRING ||
                           process.env.MONGODB_URI;

  const databaseName = process.env.MONGODB_DATABASE || 'dev';

  await mongoose.connect(
    connectionString.includes('mongodb.net/?')
      ? connectionString.replace('/?', `/${databaseName}?`)
      : connectionString
  );
  console.log(`✅ Connected to MongoDB (${databaseName})`);

  const collection = mongoose.connection.db.collection('pricing_configs');

  // Find the active config
  const active = await collection.findOne({ $or: [{ is_active: true }, { active: true }] }, { sort: { version: -1 } });
  if (!active) {
    console.error('❌ No active pricing_configs document found.');
    process.exit(1);
  }

  console.log(`📄 Found active config: version=${active.version}, _id=${active._id}`);

  const existing = active?.token_pricing?.runware?.['google:4@1'];
  if (existing) {
    console.log(`ℹ️  google:4@1 already exists: image_generation_price=${existing.image_generation_price}`);
    process.exit(0);
  }

  const result = await collection.updateOne(
    { _id: active._id },
    {
      $set: {
        'token_pricing.runware.google:4@1': { image_generation_price: 5 },
        updated_at: new Date(),
      }
    }
  );

  if (result.modifiedCount === 1) {
    console.log('✅ Added google:4@1 → image_generation_price: 5 credits to runware pricing.');
  } else {
    console.error('❌ Update did not modify any document.');
  }

  await mongoose.disconnect();
}

run().catch(err => {
  console.error('❌ Error:', err);
  process.exit(1);
});
