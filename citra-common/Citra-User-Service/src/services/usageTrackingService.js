// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');
const UserUsage = require('../models/UserUsage');
const CreditTransaction = require('../models/CreditTransaction');
const PricingConfig = require('../models/PricingConfig');

/**
 * Usage Tracking Service - Handles all credit-based billing operations
 * Replaces subscription-based tracking with pay-as-you-go model
 * 
 * PERFORMANCE OPTIMIZATION: Pricing config is cached in memory to avoid database queries on every request
 */
class UsageTrackingService {
  constructor() {
    this.cachedPricing = null;
    this.lastPricingLoad = null;
    this.CACHE_DURATION_MS = 500 * 60 * 1000; // Refresh every 500 minutes (~8.3 hours)
  }
  
  /**
   * Initialize pricing cache at server startup
   * Call this from server.js after MongoDB connection
   */
  async initializePricingCache() {
    try {
      console.log('💰 Loading pricing configuration from database...');
      this.cachedPricing = await PricingConfig.getActivePricing();
      this.lastPricingLoad = Date.now();
      
      console.log('✅ Pricing cache initialized:');
      const defaultTier = this.cachedPricing.token_pricing?.default || {};
      console.log(`   Token: ${defaultTier.input_per_1k ?? 0.05} credits/1K input, ${defaultTier.output_per_1k ?? 0.10} credits/1K output, ${defaultTier.cached_per_1k ?? 0.02} credits/1K cached`);
      console.log(`   Internet Grounding: ${defaultTier.internet_grounding_price ?? 1.0} credits/source`);
      console.log(`   Image Generation: ${defaultTier.image_generation_price ?? 1.0} credits/image`);
      console.log(`   Version: ${this.cachedPricing.version}`);
      
      return this.cachedPricing;
    } catch (error) {
      console.error('❌ Failed to initialize pricing cache:', error);
      throw error;
    }
  }
  
  /**
   * Get cached pricing (with auto-refresh if stale)
   */
  async getPricing() {
    const now = Date.now();
    
    // Check if cache needs refresh
    if (!this.cachedPricing || !this.lastPricingLoad || (now - this.lastPricingLoad) > this.CACHE_DURATION_MS) {
      console.log('🔄 Pricing cache stale or empty, refreshing...');
      await this.initializePricingCache();
    }
    
    return this.cachedPricing;
  }
  
  /**
   * Force refresh pricing cache (call when pricing is updated in admin panel)
   */
  async refreshPricingCache() {
    console.log('🔄 Force refreshing pricing cache...');
    await this.initializePricingCache();
    return this.cachedPricing;
  }
  
  /**
   * Check if user has sufficient credits for an operation
   */
  async checkCredits(user_id, requiredAmount) {
    try {
      const usage = await UserUsage.findOne({ user_id });
      
      if (!usage) {
        return {
          sufficient: false,
          balance: 0,
          required: requiredAmount,
          message: 'User usage record not found. Please contact support.'
        };
      }
      
      const sufficient = usage.hasSufficientCredits(requiredAmount);
      
      return {
        sufficient,
        balance: usage.credit_balance,
        required: requiredAmount,
        message: sufficient 
          ? 'Sufficient credits available' 
          : `Insufficient credits. Balance: ${usage.credit_balance.toFixed(2)} credits, Required: ${requiredAmount.toFixed(2)} credits`
      };
    } catch (error) {
      console.error('Error checking credits:', error);
      throw error;
    }
  }
  
  /**
   * Add credits to user account (after purchase)
   * Uses MongoDB session/transaction for atomicity:
   *   - CreditTransaction insert (with unique razorpay_payment_id) is the idempotency gate
   *   - UserUsage balance update is atomic $inc
   *   - Both succeed or both rollback
   */
  async addCredits(user_id, email, amount, ipAddress = null, userAgent = null, extraMeta = {}) {
    const session = await mongoose.startSession();
    try {
      session.startTransaction();

      let usage = await UserUsage.findOne({ user_id }).session(session);
      
      if (!usage) {
        // Create usage record if doesn't exist
        [usage] = await UserUsage.create([{
          user_id,
          email,
          credit_balance: 999999999,
        }], { session });
      }
      
      const balanceBefore = usage.credit_balance;
      
      // Check for bonus using cached pricing
      const pricing = await this.getPricing(); // Use cached pricing
      const bonusInfo = pricing.calculateBonusCredits(amount);
      const totalCredits = bonusInfo.total_credits;
      
      // Generate transaction ID
      const transactionId = `purchase_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

      // ── Step 1: Insert CreditTransaction FIRST — unique transaction_id acts as idempotency gate.
      await CreditTransaction.createPurchaseTransaction({
        transaction_id: transactionId,
        user_id,
        email,
        amount: totalCredits,
        balance_before: balanceBefore,
        balance_after: balanceBefore + totalCredits, // estimated; actual is from $inc
        source: extraMeta.source || null,
        currency: extraMeta.currency || null,
        original_amount: extraMeta.original_amount || null,
        exchange_rate: extraMeta.exchange_rate || null,
        bonus_amount: bonusInfo.bonus_amount,
        bonus_percentage: bonusInfo.bonus_percentage,
        ip_address: ipAddress,
        user_agent: userAgent
      });
      
      // ── Step 2: Add credits to user (atomic $inc)
      await usage.addCredits(totalCredits, transactionId, {
        base_amount: amount,
        bonus_amount: bonusInfo.bonus_amount,
        bonus_percentage: bonusInfo.bonus_percentage
      });

      await session.commitTransaction();
      
      return {
        success: true,
        new_balance: usage.credit_balance,
        amount_purchased: amount,
        bonus_applied: bonusInfo.bonus_amount,
        bonus_percentage: bonusInfo.bonus_percentage,
        total_credits_added: totalCredits,
        transaction_id: transactionId
      };
    } catch (error) {
      await session.abortTransaction();

      // E11000 = duplicate key on transaction_id → already processed
      if (error.code === 11000) {
        console.warn(`⚠️ Duplicate transaction caught by unique index`);
        return {
          success: true,
          duplicate: true,
          new_balance: null,
          amount_purchased: amount,
          total_credits_added: 0,
          transaction_id: transactionId
        };
      }

      console.error('Error adding credits:', error);
      throw error;
    } finally {
      session.endSession();
    }
  }
  
  /**
   * Get user's usage statistics
   * Auto-initializes new users with 0 credits
   */
  async getUsageStats(user_id, email = null) {
    try {
      let usage = await UserUsage.findOne({ user_id });
      
      if (!usage) {
        // Auto-initialize new user with 0 credits
        if (email) {
          await this.initializeNewUser(user_id, email, 999999999);
          usage = await UserUsage.findOne({ user_id });
          console.log(`New user ${user_id} initialized with unlimited credits (license model)`);
        } else {
          // Return empty stats with unlimited balance if email not provided
          return {
            user_id,
            credit_balance: 999999999,
            total_credits_purchased: 0,
            total_credits_consumed: 0,
            query_usage: {
              total_queries: 0,
              total_tokens_used: 0,
              total_cost: 0
            },
            file_upload_usage: {
              total_files: 0,
              total_size_mb: 0,
              total_cost: 0
            },
            recent_transactions: []
          };
        }
      }
      
      return {
        user_id: usage.user_id,
        email: usage.email,
        credit_balance: usage.credit_balance,
        total_credits_purchased: usage.total_credits_purchased,
        total_credits_consumed: usage.total_credits_consumed,
        query_usage: usage.query_usage,
        file_upload_usage: usage.file_upload_usage,
        recent_transactions: usage.recent_transactions.slice(0, 10), // Last 10
        created_at: usage.created_at,
        updated_at: usage.updated_at
      };
    } catch (error) {
      console.error('Error getting usage stats:', error);
      throw error;
    }
  }
  
  /**
   * Get current pricing information
   * Returns cached pricing to avoid database query
   */
  async getPricingInfo() {
    try {
      const pricing = await this.getPricing(); // Use cached pricing
      
      return {
        version: pricing.version,
        token_pricing: pricing.token_pricing,
        embedding_pricing: pricing.embedding_pricing,
        currency_config: pricing.currency_config || {},
        effective_date: pricing.effective_date,
        cached: true,
        last_refresh: new Date(this.lastPricingLoad).toISOString()
      };
    } catch (error) {
      console.error('Error getting pricing info:', error);
      throw error;
    }
  }
  
  /**
   * Initialize usage tracking for new user
   */
  async initializeNewUser(user_id, email, welcomeBonus = 0) {
    try {
      const existing = await UserUsage.findOne({ user_id });
      if (existing) {
        return {
          success: true,
          message: 'User already initialized',
          credit_balance: existing.credit_balance
        };
      }
      
      const usage = await UserUsage.initializeNewUser(user_id, email, welcomeBonus);
      
      return {
        success: true,
        message: welcomeBonus > 0 ? 'User initialized with welcome bonus' : 'User initialized',
        credit_balance: usage.credit_balance,
        welcome_bonus: welcomeBonus
      };
    } catch (error) {
      console.error('Error initializing new user:', error);
      throw error;
    }
  }
}

module.exports = new UsageTrackingService();
