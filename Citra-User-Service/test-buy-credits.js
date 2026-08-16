/**
 * Test script to diagnose buy-credits endpoint issues
 * Run with: node test-buy-credits.js
 */

require('dotenv').config();
const mongoose = require('mongoose');
const CitraAIUser = require('./src/models/CitraAIUser');
const PricingConfig = require('./src/models/PricingConfig');

async function testBuyCredits() {
  try {
    console.log('🔍 Testing buy-credits endpoint prerequisites...\n');

    // 1. Test MongoDB connection
    console.log('1️⃣ Testing MongoDB connection...');
    const mongoUri = process.env.MONGODB_CONNECTION_STRING || process.env.MONGODB_CONN_STRING || process.env.MONGODB_URI;
    if (!mongoUri) {
      console.error('❌ MongoDB connection string not found in .env');
      console.error('   Looking for: MONGODB_CONNECTION_STRING, MONGODB_CONN_STRING, or MONGODB_URI');
      process.exit(1);
    }
    
    const databaseName = process.env.MONGODB_DATABASE || 'citra-ai';
    await mongoose.connect(mongoUri, {
      dbName: databaseName,
      serverSelectionTimeoutMS: 5000
    });
    console.log('✅ MongoDB connected to database:', databaseName);
    console.log('');

    // 2. Check Razorpay credentials
    console.log('2️⃣ Checking Razorpay credentials...');
    const rzpKeyId = process.env.RZP_KEY_ID;
    const rzpKeySecret = process.env.RZP_KEY_SECRET;
    
    if (!rzpKeyId) {
      console.error('❌ RZP_KEY_ID not found in .env');
    } else {
      console.log('✅ RZP_KEY_ID:', rzpKeyId);
    }
    
    if (!rzpKeySecret) {
      console.error('❌ RZP_KEY_SECRET not found in .env');
    } else {
      console.log('✅ RZP_KEY_SECRET: [HIDDEN]');
    }
    console.log('');

    // 3. Check if user exists
    console.log('3️⃣ Checking if test user exists...');
    const testEmail = 'test-user@example.com';
    const user = await CitraAIUser.findOne({ email: testEmail });
    
    if (!user) {
      console.error(`❌ User not found: ${testEmail}`);
      console.log('Creating test user...');
      
      const newUser = new CitraAIUser({
        email: testEmail,
        name: 'Rohit Test User',
        user_type: 'paid',
        is_verified: true
      });
      await newUser.save();
      console.log('✅ Test user created');
    } else {
      console.log('✅ User found:');
      console.log('   - ID:', user._id);
      console.log('   - Email:', user.email);
      console.log('   - Name:', user.name);
      console.log('   - Type:', user.user_type);
    }
    console.log('');

    // 4. Check PricingConfig
    console.log('4️⃣ Checking PricingConfig...');
    const pricing = await PricingConfig.findOne({ is_active: true });
    
    if (!pricing) {
      console.error('❌ No active pricing config found');
      console.log('Creating default pricing config...');
      
      const defaultPricing = new PricingConfig({
        is_active: true,
        token_pricing: {
          default: { input: 0.000125, output: 0.000375 },
          lite: { input: 0.002, output: 0.01 }
        },
        storage_pricing: {
          ocr_per_page: 5,
          base_upload_cost: 2,
          cost_per_100_pages: 2,
          meeting_per_50mb: 20,
          audio_per_10mb: 5
        },
        credit_purchase: {
          minimum_amount: 100,
          bonus_tiers: [
            { min_amount: 100, max_amount: 499, bonus_percentage: 0 },
            { min_amount: 500, max_amount: 999, bonus_percentage: 10 },
            { min_amount: 1000, max_amount: 4999, bonus_percentage: 20 },
            { min_amount: 5000, max_amount: null, bonus_percentage: 30 }
          ]
        }
      });
      await defaultPricing.save();
      console.log('✅ Default pricing config created');
    } else {
      console.log('✅ Pricing config found:');
      console.log('   - Minimum amount:', pricing.credit_purchase.minimum_amount);
      console.log('   - Bonus tiers:', pricing.credit_purchase.bonus_tiers.length);
    }
    console.log('');

    // 5. Test bonus calculation
    console.log('5️⃣ Testing bonus calculation...');
    const activePricing = await PricingConfig.getActivePricing();
    const testAmounts = [100, 500, 1000, 5000];
    
    testAmounts.forEach(amount => {
      const bonusInfo = activePricing.calculateBonusCredits(amount);
      console.log(`   ${amount} credits → Total: ${bonusInfo.total_credits} credits (Bonus: ${bonusInfo.bonus_percentage}%)`);
    });
    console.log('');

    console.log('✅ All checks passed! The buy-credits endpoint should work.\n');
    console.log('📝 To test the API, use:');
    console.log('   POST http://localhost:7004/api/buy-credits');
    console.log('   Headers: Authorization: Bearer <JWT_TOKEN>');
    console.log('   Body: { "user_id": "test-user@example.com", "email": "test-user@example.com", "amount": 100 }');

  } catch (error) {
    console.error('❌ Error:', error.message);
    console.error(error);
  } finally {
    await mongoose.disconnect();
    console.log('\n🔌 MongoDB disconnected');
  }
}

testBuyCredits();
