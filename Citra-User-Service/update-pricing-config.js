// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

require('dotenv').config();
const mongoose = require('mongoose');
const dns = require('dns');

// Use Google DNS to bypass VPN DNS issues with MongoDB Atlas SRV records
dns.setServers(['8.8.8.8', '8.8.4.4']);

const MONGODB_URI = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
if (!MONGODB_URI) {
  console.error('❌ MONGODB_URI / MONGODB_CONN_STRING must be set in the environment (.env)');
  process.exit(1);
}

async function run() {
  try {
    await mongoose.connect(MONGODB_URI);
    console.log('Connected to MongoDB');

    const result = await mongoose.connection.db.collection('pricingconfigs').updateOne(
      { version: 7, is_active: true },
      { $set: { currency_config: {} } }  // credits/tokens model: no INR conversion
    );

    console.log('Update result:', {
      matchedCount: result.matchedCount,
      modifiedCount: result.modifiedCount
    });

    // Verify
    const doc = await mongoose.connection.db.collection('pricingconfigs').findOne(
      { version: 7 },
      { projection: { currency_config: 1, version: 1, is_active: 1 } }
    );
    console.log('Verified document:', JSON.stringify(doc, null, 2));

  } finally {
    await mongoose.disconnect();
    console.log('Connection closed');
  }
}

run().catch(console.error);
