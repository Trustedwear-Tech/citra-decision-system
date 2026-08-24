// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const usageTrackingService = require('../services/usageTrackingService');

/**
 * Track Query Usage - Deduct credits after query execution
 * Called from Python service after getting token usage from API
 * Express handler
 */
const handler = async (req, res) => {
  try {
    const {
      model,
      input_tokens,
      output_tokens,
      query_id
    } = req.body;
    
    // Use authenticated user from JWT — prevents IDOR
    const effectiveUserId = req.user.email;
    const email = req.user.email;
    
    if (!effectiveUserId || !model || input_tokens === undefined || output_tokens === undefined) {
      return res.status(400).json({
        error: 'Missing required fields: model, input_tokens, output_tokens'
      });
    }
    
    // Track usage
    const result = await usageTrackingService.trackQueryUsage(
      effectiveUserId,
      email,
      model,
      input_tokens,
      output_tokens,
      query_id
    );
    
    console.log('Query usage tracked:', {
      user_id: effectiveUserId,
      model,
      tokens: input_tokens + output_tokens,
      cost: result.cost,
      remaining: result.remaining_balance
    });
    
    return res.status(200).json({
      success: true,
      cost: result.cost,
      remaining_balance: result.remaining_balance,
      tokens_used: result.tokens_used,
      model: result.model,
      low_balance_warning: result.remaining_balance < 10
    });
    
  } catch (error) {
    console.log('Error tracking query usage:', error);
    
    if (error.message === 'Insufficient credits') {
      return res.status(402).json({ // Payment Required
        error: 'Insufficient credits',
        message: 'Please purchase more credits to continue'
      });
    }
    
    return res.status(500).json({
      error: 'Failed to track query usage',
      details: error.message
    });
  }
};

module.exports = handler;




