// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Check pricing configuration details in MongoDB
 */
require('dotenv').config();
const mongoose = require('mongoose');
const PricingConfig = require('./src/models/PricingConfig');

async function checkPricing() {
  try {
    console.log('🔧 Connecting to MongoDB...');
    
    const connectionString = process.env.MONGODB_CONNECTION_STRING || 
                            process.env.MONGODB_CONN_STRING || 
                            process.env.MONGODB_URI;
    
    const databaseName = process.env.MONGODB_DATABASE || 'dev';
    
    await mongoose.connect(connectionString.includes('mongodb.net/?') 
      ? connectionString.replace('/?', `/${databaseName}?`)
      : connectionString);
    
    console.log('✅ Connected to MongoDB');
    console.log(`   Database: ${databaseName}\n`);
    
    // Get all pricing configs
    const allConfigs = await PricingConfig.find({}).sort({ version: -1 });
    console.log(`📊 Total pricing configs: ${allConfigs.length}\n`);
    
    allConfigs.forEach(config => {
      console.log(`Version ${config.version}:`);
      console.log(`  is_active: ${config.is_active}`);
      console.log(`  active: ${config.active}`);
      console.log(`  created_at: ${config.created_at}`);
      console.log(`  Default pricing: ${config.token_pricing.default?.input_per_1k} credits/1K input, ${config.token_pricing.default?.output_per_1k} credits/1K output`);
      console.log();
    });
    
    // Test the queries
    console.log('Testing queries:\n');
    
    // Query 1: is_active = true
    const query1 = await PricingConfig.findOne({ is_active: true }).sort({ version: -1 });
    console.log('Query: { is_active: true }');
    console.log(query1 ? `  ✅ Found version ${query1.version}` : '  ❌ Not found');
    
    // Query 2: active = true
    const query2 = await PricingConfig.findOne({ active: true }).sort({ version: -1 });
    console.log('Query: { active: true }');
    console.log(query2 ? `  ✅ Found version ${query2.version}` : '  ❌ Not found');
    
    // Query 3: $or query
    const query3 = await PricingConfig.findOne({ $or: [{ is_active: true }, { active: true }] }).sort({ version: -1 });
    console.log('Query: { $or: [{ is_active: true }, { active: true }] }');
    console.log(query3 ? `  ✅ Found version ${query3.version}` : '  ❌ Not found');
    
  } catch (error) {
    console.error('❌ Error:', error.message);
  } finally {
    await mongoose.connection.close();
    console.log('\n🔌 MongoDB connection closed');
  }
}

checkPricing();
