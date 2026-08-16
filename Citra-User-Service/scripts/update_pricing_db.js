// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

require('dotenv').config();
const mongoose = require('mongoose');
const path = require('path');
const PricingConfig = require('../src/models/PricingConfig');

// Load config
const { connectToMongoDB, disconnectFromMongoDB } = require('../src/config/mongodb');

async function updatePricing() {
    try {
        console.log('🔌 Connecting to MongoDB...');
        await connectToMongoDB();
        console.log('✅ Connected to MongoDB');

        console.log('🔄 Creating new default pricing configuration...');
        const newPricing = await PricingConfig.createDefaultPricing();

        console.log('✅ New Pricing Configuration Created:');
        console.log(`   Version: ${newPricing.version}`);
        console.log(`   Default: ${JSON.stringify(newPricing.token_pricing.default)}`);
        console.log(`   Lite: ${JSON.stringify(newPricing.token_pricing.lite)}`);
        console.log(`   Pro: ${JSON.stringify(newPricing.token_pricing.pro)}`);

        // Handle Map conversion for logging
        const imageGenPricing = newPricing.token_pricing.image_generation instanceof Map ?
            Object.fromEntries(newPricing.token_pricing.image_generation) :
            newPricing.token_pricing.image_generation;

        console.log(`   Image Generation: ${JSON.stringify(imageGenPricing, null, 2)}`);

        console.log('🎉 Database updated successfully!');
    } catch (error) {
        console.error('❌ Error updating pricing:', error);
    } finally {
        await disconnectFromMongoDB();
        console.log('👋 Disconnected');
        process.exit(0);
    }
}

updatePricing();
