// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
