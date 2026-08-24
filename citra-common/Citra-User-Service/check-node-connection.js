// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Show exactly which database Node.js connects to and verify pricing data
 */
require('dotenv').config();
const mongoose = require('mongoose');

async function checkNodeConnection() {
  try {
    console.log('🔧 Checking Node.js MongoDB connection...\n');
    
    let connectionString = process.env.MONGODB_CONNECTION_STRING || 
                          process.env.MONGODB_CONN_STRING;
    
    const databaseName = process.env.MONGODB_DATABASE || 'dev';
    
    console.log('Original connection string:', connectionString);
    console.log('Database name from env:', databaseName);
    
    // Check if database is in connection string
    const hasDbInUri = connectionString.includes('.net/') && 
                       !connectionString.includes('.net/?') &&
                       !connectionString.includes('.net?');
    
    if (!hasDbInUri) {
      if (connectionString.includes('.net/?')) {
        connectionString = connectionString.replace('/?', `/${databaseName}?`);
        console.log('Modified connection string to include database');
      }
    }
    
    console.log('Final connection string:', connectionString);
    console.log('');
    
    await mongoose.connect(connectionString);
    console.log('✅ Connected to MongoDB');
    console.log('   Database name:', mongoose.connection.db.databaseName);
    console.log('');
    
    // List collections
    const collections = await mongoose.connection.db.listCollections().toArray();
    console.log(`📋 Collections in database '${mongoose.connection.db.databaseName}':`);
    collections.forEach(col => console.log(`   - ${col.name}`));
    console.log('');
    
    // Check pricing_configs
    const pricingCount = await mongoose.connection.db.collection('pricing_configs').countDocuments();
    console.log(`📊 pricing_configs collection: ${pricingCount} documents`);
    
    if (pricingCount > 0) {
      const pricing = await mongoose.connection.db.collection('pricing_configs').findOne({});
      console.log(`   Sample doc: version=${pricing.version}, is_active=${pricing.is_active}`);
    }
    
  } catch (error) {
    console.error('❌ Error:', error.message);
  } finally {
    await mongoose.connection.close();
    console.log('\n🔌 Connection closed');
  }
}

checkNodeConnection();
