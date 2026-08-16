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

async function reactivateV2() {
  try {
    const mongoUri = process.env.MONGODB_URI || process.env.MONGODB_CONNECTION_STRING;
    const dbName = process.env.MONGODB_DATABASE || 'dev';
    
    console.log('🔄 Connecting to MongoDB...');
    await mongoose.connect(mongoUri, { dbName });
    console.log(`✅ Connected\n`);

    const v2 = await PricingConfig.findOne({ version: 2 });
    if (v2) {
      v2.is_active = true;
      await v2.save();
      console.log('✅ Reactivated version 2');
      console.log(`Document: ${v2.upload_pricing.document} credits/MB`);
    } else {
      console.log('❌ Version 2 not found');
      
      // List all versions
      const all = await PricingConfig.find().sort({ version: 1 });
      console.log('\n📋 All pricing configs:');
      all.forEach(p => console.log(`   Version ${p.version}: ${p.is_active ? '🟢 Active' : '⭕ Inactive'}`));
    }

    await mongoose.connection.close();
  } catch (error) {
    console.error('❌ Error:', error.message);
    process.exit(1);
  }
}

reactivateV2();
