// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Update existing coupons to support credit-based system
 * Run with: node update-coupons-for-credits.js
 */

require('dotenv').config();
const mongoose = require('mongoose');
const Coupon = require('./src/models/Coupon');

async function updateCoupons() {
  try {
    console.log('🔄 Updating coupons for credit-based system...\n');

    // Connect to MongoDB
    const mongoUri = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
    const databaseName = process.env.MONGODB_DATABASE || 'citra-ai';
    
    await mongoose.connect(mongoUri, {
      dbName: databaseName,
      serverSelectionTimeoutMS: 5000
    });
    console.log('✅ MongoDB connected\n');

    // 1. Update all existing coupons to have credit_amount = 1000
    console.log('1️⃣ Updating existing coupons...');
    const updateResult = await Coupon.updateMany(
      { credit_amount: { $exists: false } },
      { $set: { credit_amount: 1000 } }
    );
    console.log(`   Updated ${updateResult.modifiedCount} coupons with credit_amount = 1000 credits\n`);

    // 2. Create a sample coupon if none exists
    console.log('2️⃣ Checking for sample coupons...');
    const existingCoupons = await Coupon.find({ is_active: true });
    
    if (existingCoupons.length === 0) {
      console.log('   No active coupons found. Creating sample coupons...\n');
      
      // Create sample coupons
      const sampleCoupons = [
        {
          coupon_name: 'Citra-AI Welcome Offer',
          coupon_code: 'Citra-AI1000',
          expiry_date: new Date('2026-12-31'),
          credit_amount: 1000,
          max_uses: null,
          description: 'Get 1000 credits to try Citra AI services',
          is_active: true
        },
        {
          coupon_name: 'Law Student Special',
          coupon_code: 'LAWSTUDENT500',
          expiry_date: new Date('2026-12-31'),
          credit_amount: 500,
          max_uses: null,
          description: 'Law student special - 500 credits',
          is_active: true
        },
        {
          coupon_name: 'Premium Trial',
          coupon_code: 'PREMIUM2000',
          expiry_date: new Date('2026-12-31'),
          credit_amount: 2000,
          max_uses: 100,
          description: 'Premium trial with 2000 credits (limited to 100 users)',
          is_active: true
        }
      ];

      for (const couponData of sampleCoupons) {
        const coupon = await Coupon.createCoupon(couponData);
        console.log(`   ✅ Created: ${coupon.coupon_code} (${coupon.credit_amount} credits)`);
      }
    } else {
      console.log(`   Found ${existingCoupons.length} active coupons:`);
      existingCoupons.forEach(c => {
        console.log(`   - ${c.coupon_code}: ${c.credit_amount || 1000} credits (uses: ${c.current_uses}/${c.max_uses || '∞'})`);
      });
    }

    console.log('\n✅ Coupon update complete!\n');
    console.log('📝 Sample coupon codes:');
    const allCoupons = await Coupon.find({ is_active: true });
    allCoupons.forEach(c => {
      console.log(`   ${c.coupon_code} - ${c.credit_amount || 1000} credits`);
    });

  } catch (error) {
    console.error('❌ Error:', error.message);
    console.error(error);
  } finally {
    await mongoose.disconnect();
    console.log('\n🔌 MongoDB disconnected');
  }
}

updateCoupons();
