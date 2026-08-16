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

async function updateUploadPricing() {
  try {
    console.log('🔧 Connecting to MongoDB...');
    
    let connectionString = process.env.MONGO_URI || process.env.MONGODB_CONNECTION_STRING || 'mongodb://localhost:27017/';
    const databaseName = process.env.MONGO_DB_NAME || process.env.MONGODB_DATABASE || 'dev';
    
    if (!connectionString.includes('mongodb.net/')) {
      connectionString = connectionString.replace('/?', `/${databaseName}?`);
    } else if (connectionString.includes('mongodb.net/?')) {
      connectionString = connectionString.replace('/?', `/${databaseName}?`);
    }
    
    console.log('   Using database:', databaseName);
    
    await mongoose.connect(connectionString);
    console.log('✅ Connected to MongoDB');

    // Delete existing pricing documents
    const deleteResult = await PricingConfig.deleteMany({});
    console.log(`🗑️  Deleted ${deleteResult.deletedCount} existing pricing document(s)`);

    // Create new pricing configuration with updated upload pricing
    const newPricing = await PricingConfig.create({
      version: 5,
      is_active: true,
      active: true,
      token_pricing: {
        default: {
          input_per_1k: 0.5,
          output_per_1k: 0.5,
          cached_per_1k: 0.01,
          internet_grounding_price: 4,
          collections_search_price: 0.4
        },
        lite: {
          input_per_1k: 0.06,
          output_per_1k: 0.12,
          cached_per_1k: 0.01
        },
        pro: {
          input_per_1k: 0.5,
          output_per_1k: 0.5,
          cached_per_1k: 0.02
        }
      },
      embedding_pricing: {
        per_1k: 0.05
      },
      storage_pricing: {
        aws_per_gb_per_day: 0.2,
        database_per_gb_per_day: 10
      },
      upload_pricing: {
        document: 0.02,    // 0.02 credits per page (unchanged)
        audio: 0.1,        // 0.1 credits per MB (updated from 0.5)
        video: 0.02,       // 0.02 credits per MB (updated from 0.4)
        ocr: 0.5           // 0.5 credits per page (updated from 5)
      },
      credit_purchase: {
        minimum_amount: 100,
        bonus_tiers: [
          { threshold: 500, bonus_percentage: 10 },
          { threshold: 1000, bonus_percentage: 20 },
          { threshold: 5000, bonus_percentage: 30 }
        ]
      },
      description: 'Pay-as-you-go pricing v5 - Updated upload pricing: OCR 0.5 credits/page, Audio 0.1 credits/MB, Video 0.02 credits/MB',
      effective_date: new Date()
    });

    console.log('\n✅ Pricing configuration created successfully!');
    console.log('\n📊 Updated Upload Pricing:');
    console.log(`   Document: ${newPricing.upload_pricing.document} credits per page`);
    console.log(`   Audio: ${newPricing.upload_pricing.audio} credits per MB (reduced from 0.5)`);
    console.log(`   Video: ${newPricing.upload_pricing.video} credits per MB (reduced from 0.4)`);
    console.log(`   OCR: ${newPricing.upload_pricing.ocr} credits per page (reduced from 5)`);

    console.log('\n📈 Example Upload Costs:');
    console.log('   10-page PDF: 0.20 credits (10 × 0.02)');
    console.log('   10-page OCR: 5.00 credits (10 × 0.5)');
    console.log('   10 MB Audio: 1.00 credits (10 × 0.1)');
    console.log('   10 MB Video: 0.20 credits (10 × 0.02)');

    console.log('\n💾 Storage Pricing:');
    console.log(`   AWS Storage: ${newPricing.storage_pricing.aws_per_gb_per_day} credits per GB per day`);
    console.log(`   Database Storage: ${newPricing.storage_pricing.database_per_gb_per_day} credits per GB per day`);

  } catch (error) {
    console.error('\n❌ Error creating pricing:', error);
    console.error('Stack trace:', error.stack);
    process.exit(1);
  } finally {
    await mongoose.connection.close();
    console.log('\n🔌 MongoDB connection closed');
  }
}

// Run the creation
if (require.main === module) {
  updateUploadPricing();
}

module.exports = updateUploadPricing;
