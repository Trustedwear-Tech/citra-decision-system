// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Cleanup Script - Remove Legacy Subscription Collection
 * 
 * ✅ COMPLETED: The 'citraaisubscriptions' collection has been manually dropped from MongoDB
 * 
 * This script was used to drop the old subscription collection.
 * The collection is no longer used after migrating to the credit-based billing system.
 * 
 * Legacy files removed:
 * - src/models/CitraAISubscription.js
 * - src/functions/cancel-subscription.js
 * - src/tests/run_subscription_schedule_test.js
 * - MongoDB collection: citraaisubscriptions (dropped manually)
 */

require('dotenv').config();
const mongoose = require('mongoose');

async function cleanupSubscriptionCollection() {
  try {
    console.log('🗑️  Starting cleanup of legacy subscription collection...\n');

    // Connect to MongoDB
    const mongoUri = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
    const databaseName = process.env.MONGODB_DATABASE || 'citra-ai';

    if (!mongoUri) {
      console.error('❌ MongoDB connection string not found in .env');
      console.error('   Looking for: MONGODB_CONNECTION_STRING, MONGODB_CONN_STRING, or MONGODB_URI');
      process.exit(1);
    }

    await mongoose.connect(mongoUri, {
      dbName: databaseName,
      serverSelectionTimeoutMS: 5000
    });
    console.log('✅ Connected to MongoDB database:', databaseName);
    console.log('');

    // Get database instance
    const db = mongoose.connection.db;

    // Check if collection exists
    const collections = await db.listCollections({ name: 'citraaisubscriptions' }).toArray();

    if (collections.length === 0) {
      console.log('ℹ️  Collection "citraaisubscriptions" does not exist.');
      console.log('   Nothing to clean up.');
    } else {
      // Get collection stats before deletion
      const stats = await db.collection('citraaisubscriptions').stats();
      console.log(`📊 Collection stats BEFORE deletion:`);
      console.log(`   Documents: ${stats.count}`);
      console.log(`   Size: ${(stats.size / 1024).toFixed(2)} KB`);
      console.log(`   Storage Size: ${(stats.storageSize / 1024).toFixed(2)} KB`);
      console.log('');

      // Drop the collection
      console.log('🗑️  Dropping collection "citraaisubscriptions"...');
      await db.dropCollection('citraaisubscriptions');
      console.log('✅ Collection "citraaisubscriptions" has been dropped successfully!');
      console.log('');
    }

    // Verify remaining collections
    console.log('📋 Remaining collections in database:');
    const allCollections = await db.listCollections().toArray();
    allCollections.forEach(col => {
      console.log(`   - ${col.name}`);
    });
    console.log('');

    console.log('✅ Cleanup completed successfully!');
    console.log('');
    console.log('📝 Summary:');
    console.log('   ✅ Legacy subscription model deleted (CitraAISubscription.js)');
    console.log('   ✅ Legacy subscription functions deleted (cancel-subscription.js)');
    console.log('   ✅ Legacy subscription tests deleted (run_subscription_schedule_test.js)');
    console.log('   ✅ MongoDB collection dropped (citraaisubscriptions)');
    console.log('');
    console.log('💡 Current billing system:');
    console.log('   - userusages: Credit balance and usage stats');
    console.log('   - credittransactions: Transaction audit trail');
    console.log('   - pricing_configs: Pricing configuration');

  } catch (error) {
    console.error('❌ Error during cleanup:', error.message);
    console.error('Stack trace:', error.stack);
    process.exit(1);
  } finally {
    // Close MongoDB connection
    await mongoose.connection.close();
    console.log('\n🔌 MongoDB connection closed');
    process.exit(0);
  }
}

// Run the cleanup
cleanupSubscriptionCollection();
