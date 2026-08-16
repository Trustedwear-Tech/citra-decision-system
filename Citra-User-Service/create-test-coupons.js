// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');
const Coupon = require('./src/models/Coupon');
require('dotenv').config();

const sampleCoupons = [
  {
    coupon_name: 'Law Logic Professional',
    coupon_code: 'Citra-AI1000',
    expiry_date: new Date(Date.now() + 60 * 24 * 60 * 60 * 1000), // 60 days from now
    validity_days: null,
    credit_amount: 1000,
    max_uses: null, // Unlimited
    description: 'Professional tier access with 1000 credits'
  },
  {
    coupon_name: 'Test 7-Day Trial',
    coupon_code: 'TEST7DAY',
    expiry_date: new Date(Date.now() + 90 * 24 * 60 * 60 * 1000), // 90 days from now
    validity_days: 7,
    credit_amount: 1000,
    max_uses: 100,
    description: 'Standard 7-day trial for testing'
  },
  {
    coupon_name: 'Extended Trial',
    coupon_code: 'EXTENDED15',
    expiry_date: new Date(Date.now() + 60 * 24 * 60 * 60 * 1000), // 60 days from now
    validity_days: 15,
    credit_amount: 1000,
    max_uses: 50,
    description: 'Extended 15-day trial for special users'
  },
  {
    coupon_name: 'Demo Access',
    coupon_code: 'DEMO2025',
    expiry_date: new Date(Date.now() + 180 * 24 * 60 * 60 * 1000), // 180 days from now
    validity_days: 14,
    credit_amount: 1000,
    max_uses: null, // Unlimited
    description: 'Unlimited demo access for demonstrations'
  },
  {
    coupon_name: 'Partner Program',
    coupon_code: 'PARTNER30',
    expiry_date: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000), // 365 days from now
    validity_days: 30,
    credit_amount: 1000,
    max_uses: null, // Unlimited
    description: 'Extended trial for business partners'
  },
  {
    coupon_name: 'Single Use Test',
    coupon_code: 'SINGLE1',
    expiry_date: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000), // 30 days from now
    validity_days: 7,
    credit_amount: 1000,
    max_uses: 1,
    description: 'Single-use coupon for testing limits'
  }
];

async function createCoupons() {
  try {
    console.log('========================================');
    console.log('  Creating Sample Coupons Directly');
    console.log('========================================\n');

    // Connect to MongoDB
    const mongoUri = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING;
    if (!mongoUri) {
      throw new Error('MongoDB connection string not found in environment');
    }

    await mongoose.connect(mongoUri, {
      useNewUrlParser: true,
      useUnifiedTopology: true,
    });
    console.log('✅ Connected to MongoDB\n');

    // Clear existing test coupons
    const deleted = await Coupon.deleteMany({
      coupon_code: { $in: sampleCoupons.map(c => c.coupon_code) }
    });
    if (deleted.deletedCount > 0) {
      console.log(`🗑️  Deleted ${deleted.deletedCount} existing test coupons\n`);
    }

    // Create coupons
    let successCount = 0;
    for (const couponData of sampleCoupons) {
      try {
        const coupon = new Coupon(couponData);
        await coupon.save();
        console.log(`✅ Created: ${couponData.coupon_code} (${couponData.validity_days} days, ${couponData.credit_amount} credits, ${couponData.max_uses || 'Unlimited'} uses)`);
        successCount++;
      } catch (error) {
        console.log(`❌ Failed: ${couponData.coupon_code} - ${error.message}`);
      }
    }

    console.log('\n========================================');
    console.log(`  ${successCount}/${sampleCoupons.length} Coupons Created`);
    console.log('========================================\n');

    // Display created coupons
    console.log('Sample Coupons:\n');
    for (const coupon of sampleCoupons) {
      console.log(`Code: ${coupon.coupon_code}`);
      console.log(`  Name: ${coupon.coupon_name}`);
      console.log(`  Validity: ${coupon.validity_days} days`);
      console.log(`  Credits: ${coupon.credit_amount}`);
      console.log(`  Max Uses: ${coupon.max_uses || 'Unlimited'}`);
      console.log(`  Description: ${coupon.description}\n`);
    }

    console.log('========================================');
    console.log('  Testing Instructions');
    console.log('========================================\n');
    console.log('1. Open the app at http://localhost:8081');
    console.log('2. Click "Start for Free Now" button on IntroScreen');
    console.log('3. Enter coupon code: TEST7DAY');
    console.log('4. Complete signup with Google');
    console.log('5. Verify trial access is granted\n');

    await mongoose.disconnect();
    console.log('✅ Disconnected from MongoDB');

  } catch (error) {
    console.error('❌ Error:', error.message);
    process.exit(1);
  }
}

createCoupons();
