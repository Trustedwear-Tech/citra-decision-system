// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

require('dotenv').config();
const mongoose = require('mongoose');
const PricingConfig = require('../src/models/PricingConfig');

async function checkV3() {
  try {
    const mongoUri = process.env.MONGODB_URI || process.env.MONGODB_CONNECTION_STRING;
    const dbName = process.env.MONGODB_DATABASE || 'dev';
    
    console.log('🔄 Connecting to MongoDB...');
    await mongoose.connect(mongoUri, { dbName });
    console.log(`✅ Connected\n`);

    const v3 = await PricingConfig.findOne({ version: 3, is_active: true });
    if (v3) {
      console.log('✅ Version 3 found and active');
      console.log('\n📤 Upload Pricing Object:');
      console.log(JSON.stringify(v3.upload_pricing, null, 2));
    } else {
      console.log('❌ Version 3 not found or not active');
    }

    await mongoose.connection.close();
  } catch (error) {
    console.error('❌ Error:', error.message);
    process.exit(1);
  }
}

checkV3();
