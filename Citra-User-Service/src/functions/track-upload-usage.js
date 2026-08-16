// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const usageTrackingService = require('../services/usageTrackingService');

/**
 * Track Upload Usage - Deduct credits after file upload
 * Called from Python service after file upload completes
 * Express handler
 */
const handler = async (req, res) => {
  try {
    const {
      file_id,
      filename,
      file_size_bytes,
      file_type
    } = req.body;
    
    // Use authenticated user from JWT — prevents IDOR
    const effectiveUserId = req.user.email;
    const email = req.user.email;
    
    if (!effectiveUserId || !file_id || !filename || file_size_bytes === undefined || !file_type) {
      return res.status(400).json({
        error: 'Missing required fields: file_id, filename, file_size_bytes, file_type'
      });
    }
    
    // Track usage
    const result = await usageTrackingService.trackUploadUsage(
      effectiveUserId,
      email,
      file_id,
      filename,
      file_size_bytes,
      file_type
    );
    
    console.log('Upload usage tracked:', {
      user_id: effectiveUserId,
      file_id,
      size_mb: result.file_size_mb,
      cost: result.cost,
      remaining: result.remaining_balance
    });
    
    return res.status(200).json({
      success: true,
      cost: result.cost,
      remaining_balance: result.remaining_balance,
      file_size_mb: result.file_size_mb,
      file_type: result.file_type,
      low_balance_warning: result.remaining_balance < 10
    });
    
  } catch (error) {
    console.log('Error tracking upload usage:', error);
    
    if (error.message === 'Insufficient credits') {
      return res.status(402).json({ // Payment Required
        error: 'Insufficient credits',
        message: 'Please purchase more credits to continue'
      });
    }
    
    return res.status(500).json({
      error: 'Failed to track upload usage',
      details: error.message
    });
  }
};

module.exports = handler;




