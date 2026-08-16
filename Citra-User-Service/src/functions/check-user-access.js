// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');
const CitraAIUser = require('../models/CitraAIUser');
const usageTrackingService = require('../services/usageTrackingService');
const envConfig = require('../config/environment');

/**
 * Check user access status (credit-based system)
 * @route POST /api/users/check-access
 */
const handler = async (req, res) => {
  try {
    // Use authenticated user from JWT — prevents IDOR
    const email = req.user.email;

    if (!email) {
      return res.status(401).json({
        success: false,
        error: 'Authentication required'
      });
    }

    // Find user
    let user;
    user = await CitraAIUser.findOne({ email: email });

    if (!user) {
      return res.status(404).json({
        success: false,
        error: 'User not found',
        errorCode: 'USER_NOT_FOUND',
        hasAccess: false,
        requiresAction: 'signup'
      });
    }

    // Get credit usage stats (auto-initializes with 0 credits)
    const stats = await usageTrackingService.getUsageStats(user.email);
    
    const hasCredits = stats.credit_balance > 0;
    const hasAccess = hasCredits;

    let requiresAction = null;
    let message = '';

    if (hasAccess) {
      message = `User has ${stats.credit_balance.toFixed(2)} credits available`;
    } else {
      requiresAction = 'purchase_credits';
      message = 'Insufficient credits. Please purchase credits to continue.';
    }

    console.log('Access check completed:', {
      userId: user._id,
      email: user.email,
      hasAccess,
      creditBalance: stats.credit_balance
    });

    return res.json({
      success: true,
      hasAccess,
      requiresAction,
      message,
      user: {
        id: user._id,
        email: user.email,
        name: user.name,
        user_type: 'paid'
      },
      credits: {
        balance: stats.credit_balance,
        totalPurchased: stats.total_purchased,
        totalUsed: stats.total_used,
        queryUsage: stats.query_usage,
        uploadUsage: stats.upload_usage
      }
    });

  } catch (error) {
    console.log('Error checking user access:', error);
    
    return res.status(500).json({
      success: false,
      error: 'Failed to check user access',
      details: error.message
    });
  }
};

module.exports = handler;




