// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Database Migration Script: Remove Legacy User Type Fields
 * 
 * This script cleans up old subscription/trial-based fields from the users collection
 * and migrates all users to the credit-based system.
 * 
 * Fields to be removed:
 * - trial_start_date
 * - trial_expiry_date
 * - has_active_subscription
 * - plan_type
 * - usage_reset_date
 * 
 * User types to be migrated:
 * - 'free_trial' → 'free'
 * - 'credit_based' → 'paid'
 * - 'free' → remains 'free'
 */

require('dotenv').config();
const mongoose = require('mongoose');

const MONGODB_URI = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING;
const DB_NAME = process.env.MONGODB_DATABASE || 'dev';

async function cleanupLegacyFields() {
  try {
    console.log('🔌 Connecting to MongoDB...');
    await mongoose.connect(MONGODB_URI, {
      dbName: DB_NAME,
      useNewUrlParser: true,
      useUnifiedTopology: true,
    });
    console.log('✅ Connected to MongoDB');
    console.log(`📊 Database: ${DB_NAME}`);
    console.log('');

    const db = mongoose.connection.db;
    const usersCollection = db.collection('users');

    // Step 1: Get count of documents with legacy fields
    const totalUsers = await usersCollection.countDocuments({});
    const legacyUsers = await usersCollection.countDocuments({
      $or: [
        { trial_start_date: { $exists: true } },
        { trial_expiry_date: { $exists: true } },
        { has_active_subscription: { $exists: true } },
        { plan_type: { $exists: true } },
        { usage_reset_date: { $exists: true } }
      ]
    });

    console.log('📈 Migration Statistics:');
    console.log(`   Total users: ${totalUsers}`);
    console.log(`   Users with legacy fields: ${legacyUsers}`);
    console.log('');

    // Step 2: Update user_type values
    console.log('🔄 Migrating user types...');
    
    const freeTrialResult = await usersCollection.updateMany(
      { user_type: 'free_trial' },
      { $set: { user_type: 'free' } }
    );
    console.log(`   'free_trial' → 'free': ${freeTrialResult.modifiedCount} users`);

    const creditBasedResult = await usersCollection.updateMany(
      { user_type: 'credit_based' },
      { $set: { user_type: 'paid' } }
    );
    console.log(`   'credit_based' → 'paid': ${creditBasedResult.modifiedCount} users`);

    console.log('');

    // Step 3: Remove legacy fields
    console.log('🗑️  Removing legacy fields...');
    
    const cleanupResult = await usersCollection.updateMany(
      {},
      {
        $unset: {
          trial_start_date: '',
          trial_expiry_date: '',
          has_active_subscription: '',
          plan_type: '',
          usage_reset_date: ''
        }
      }
    );
    
    console.log(`   Removed legacy fields from ${cleanupResult.modifiedCount} documents`);
    console.log('');

    // Step 4: Verify cleanup
    console.log('✅ Verifying cleanup...');
    
    const remainingLegacy = await usersCollection.countDocuments({
      $or: [
        { trial_start_date: { $exists: true } },
        { trial_expiry_date: { $exists: true } },
        { has_active_subscription: { $exists: true } },
        { plan_type: { $exists: true } },
        { usage_reset_date: { $exists: true } }
      ]
    });

    if (remainingLegacy === 0) {
      console.log('   ✅ All legacy fields successfully removed!');
    } else {
      console.log(`   ⚠️  Warning: ${remainingLegacy} documents still have legacy fields`);
    }
    console.log('');

    // Step 5: Show final user type distribution
    console.log('📊 Final User Type Distribution:');
    
    const userTypes = await usersCollection.aggregate([
      { $group: { _id: '$user_type', count: { $sum: 1 } } },
      { $sort: { count: -1 } }
    ]).toArray();

    userTypes.forEach(type => {
      console.log(`   ${type._id || 'undefined'}: ${type.count} users`);
    });
    console.log('');

    console.log('🎉 Migration completed successfully!');
    console.log('');
    console.log('Summary:');
    console.log(`   ✅ User types migrated: ${freeTrialResult.modifiedCount + paidResult.modifiedCount}`);
    console.log(`   ✅ Legacy fields removed: ${cleanupResult.modifiedCount} documents`);
    console.log(`   ✅ Total users in system: ${totalUsers}`);

  } catch (error) {
    console.error('❌ Migration failed:', error);
    throw error;
  } finally {
    await mongoose.disconnect();
    console.log('');
    console.log('🔌 Disconnected from MongoDB');
  }
}

// Run the migration
console.log('');
console.log('═══════════════════════════════════════════════════════');
console.log('  Database Migration: Clean Up Legacy User Fields');
console.log('═══════════════════════════════════════════════════════');
console.log('');

cleanupLegacyFields()
  .then(() => {
    console.log('✅ Script completed successfully');
    process.exit(0);
  })
  .catch((error) => {
    console.error('❌ Script failed:', error.message);
    process.exit(1);
  });
