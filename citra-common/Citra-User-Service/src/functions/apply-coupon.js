// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');
const Coupon = require('../models/Coupon');
const UserCoupon = require('../models/UserCoupon');
const CitraAIUser = require('../models/CitraAIUser');
const UserUsage = require('../models/UserUsage');
const CreditTransaction = require('../models/CreditTransaction');
const envConfig = require('../config/environment');

/**
 * Apply a coupon code to a user and give them 1000 credits
 * @route POST /api/coupons/apply
 */
const handler = async (req, res) => {
  try {
    const { coupon_code } = req.body;

    // Use authenticated user from JWT — prevents IDOR
    const email = req.user.email;

    if (!coupon_code || !email) {
      return res.status(400).json({
        success: false,
        error: 'Coupon code is required'
      });
    }

    // Find user by authenticated email
    let user;
    user = await CitraAIUser.findOne({ email: email });

    if (!user) {
      return res.status(404).json({
        success: false,
        error: 'User not found',
        errorCode: 'USER_NOT_FOUND'
      });
    }

    // In credit-based system, ALL users can use coupons to get additional credits
    // This includes new users, free users, and users who have purchased credits before
    // The only restriction is: each user can only use a specific coupon code once

    // IMPORTANT: user_usage.user_id is keyed by email across the stack (JWT + usage service)
    // so we should always use email as the lookup key to avoid duplicate usage rows
    const usageKey = user.email;

    // Validate coupon
    const coupon = await Coupon.findValidCoupon(coupon_code);

    // Block referral coupons from the normal coupon flow — they must go through the referral signup flow
    if (coupon && coupon.coupon_type === 'referral') {
      return res.status(400).json({
        success: false,
        error: 'This is a referral code. Share the referral link with new users instead of applying it as a coupon.',
        errorCode: 'REFERRAL_COUPON_NOT_ALLOWED'
      });
    }

    if (!coupon) {
      // Check if coupon exists but is invalid
      const existingCoupon = await Coupon.findOne({ 
        coupon_code: coupon_code.toUpperCase() 
      });

      if (existingCoupon) {
        const now = new Date();
        
        if (!existingCoupon.is_active) {
          return res.status(400).json({
            success: false,
            error: 'This coupon has been deactivated',
            errorCode: 'COUPON_INACTIVE'
          });
        }
        
        if (existingCoupon.expiry_date <= now) {
          return res.status(400).json({
            success: false,
            error: 'This coupon has expired',
            errorCode: 'COUPON_EXPIRED'
          });
        }
        
        if (existingCoupon.max_uses !== null && 
            existingCoupon.current_uses >= existingCoupon.max_uses) {
          return res.status(400).json({
            success: false,
            error: 'This coupon has reached its usage limit',
            errorCode: 'COUPON_LIMIT_REACHED'
          });
        }
      }

      return res.status(404).json({
        success: false,
        error: 'Invalid coupon code',
        errorCode: 'COUPON_NOT_FOUND'
      });
    }

    // Check if user has already used this specific coupon
    const hasUsedCoupon = await UserCoupon.hasUserUsedCoupon(user._id, coupon_code);
    
    if (hasUsedCoupon) {
      return res.status(400).json({
        success: false,
        error: 'You have already used this coupon',
        errorCode: 'COUPON_ALREADY_USED'
      });
    }

    // Apply coupon: Give user credits based on coupon configuration
    const COUPON_CREDIT_AMOUNT = coupon.credit_amount || 1000;
    
    // Get or create user usage record
    let userUsage = await UserUsage.findOne({ user_id: usageKey });
    
    if (!userUsage) {
      // Initialize new user with coupon credits
      userUsage = await UserUsage.initializeNewUser(
        usageKey,
        user.email,
        COUPON_CREDIT_AMOUNT
      );
      
      // Create a proper transaction record for welcome bonus
      const transactionId = `welcome_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      await CreditTransaction.create({
        transaction_id: transactionId,
        user_id: usageKey,
        email: user.email,
        type: 'bonus',
        amount: COUPON_CREDIT_AMOUNT,
        balance_before: 0,
        balance_after: COUPON_CREDIT_AMOUNT,
        notes: `Welcome bonus - Coupon: ${coupon.coupon_code}${coupon.coupon_name ? ' - ' + coupon.coupon_name : ''}`,
        timestamp: new Date()
      });
      
      console.log('New user initialized with coupon credits:', {
        userId: user._id,
        email: user.email,
        creditsAdded: COUPON_CREDIT_AMOUNT
      });
    } else {
      // Add credits to existing user
      const balanceBefore = userUsage.credit_balance;
      const transactionId = `coupon_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      
      await userUsage.addCredits(COUPON_CREDIT_AMOUNT, transactionId, {
        coupon_code: coupon.coupon_code,
        coupon_name: coupon.coupon_name,
        source: 'coupon_redemption'
      });
      
      // Create transaction record
      await CreditTransaction.create({
        transaction_id: transactionId,
        user_id: usageKey,
        email: user.email,
        type: 'bonus',
        amount: COUPON_CREDIT_AMOUNT,
        balance_before: balanceBefore,
        balance_after: userUsage.credit_balance,
        notes: `Coupon bonus - ${coupon.coupon_code}${coupon.coupon_name ? ' - ' + coupon.coupon_name : ''}`,
        timestamp: new Date()
      });
      
      console.log('Credits added via coupon:', {
        userId: user._id,
        email: user.email,
        creditsAdded: COUPON_CREDIT_AMOUNT,
        newBalance: userUsage.credit_balance
      });
    }

    // Record coupon usage
    await UserCoupon.recordUsage(
      user._id,
      user.email,
      coupon.coupon_code,
      0 // No validity days for credit-based system
    );

    // Increment coupon usage count
    await coupon.incrementUsage();

    // Update user type to paid if needed
    if (user.user_type !== 'paid') {
      user.user_type = 'paid';
      await user.save();
    }

    console.log('Coupon applied successfully:', {
      userId: user._id,
      email: user.email,
      couponCode: coupon.coupon_code,
      creditsAdded: COUPON_CREDIT_AMOUNT,
      newBalance: userUsage.credit_balance
    });

    return res.status(200).json({
      success: true,
      message: `Coupon applied successfully! ${COUPON_CREDIT_AMOUNT} credits have been added to your account.`,
      credits: {
        amount_added: COUPON_CREDIT_AMOUNT,
        new_balance: userUsage.credit_balance,
        currency: 'credits'
      },
      user: {
        id: user._id,
        email: user.email,
        user_type: user.user_type
      }
    });

  } catch (error) {
    console.log('Error applying coupon:', error);
    
    // Handle duplicate key error (race condition)
    if (error.code === 11000) {
      return res.status(400).json({
        success: false,
        error: 'You have already used this coupon',
        errorCode: 'COUPON_ALREADY_USED'
      });
    }
    
    return res.status(500).json({
      success: false,
      error: 'Failed to apply coupon',
      details: error.message
    });
  }
};

module.exports = handler;




