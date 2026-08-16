// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const usageTrackingService = require('../services/usageTrackingService');
const CreditTransaction = require('../models/CreditTransaction');

/**
 * Get Usage Stats - Retrieve user's consumption analytics.
 * License model: credit_balance is always returned as unlimited (999999999).
 */
const handler = async (req, res) => {
  try {
    const effectiveUserId = req.user.email;
    const email = req.user.email;

    if (!effectiveUserId) {
      return res.status(400).json({ error: 'Authentication required' });
    }

    const stats = await usageTrackingService.getUsageStats(effectiveUserId, email);

    // Override credit_balance — always unlimited in license model
    stats.credit_balance = 999999999;

    // Token economics: input/output/cached split for the caller (not stored
    // aggregated on UserUsage, so aggregate the caller's own transactions).
    const [breakdown] = await CreditTransaction.aggregate([
      { $match: { user_id: effectiveUserId, type: 'query_usage' } },
      {
        $group: {
          _id: null,
          input_tokens: { $sum: { $ifNull: ['$query_metadata.input_tokens', 0] } },
          output_tokens: { $sum: { $ifNull: ['$query_metadata.output_tokens', 0] } },
          cached_tokens: { $sum: { $ifNull: ['$query_metadata.cached_tokens', 0] } },
        },
      },
    ]);
    stats.token_breakdown = {
      input_tokens: breakdown?.input_tokens || 0,
      output_tokens: breakdown?.output_tokens || 0,
      cached_tokens: breakdown?.cached_tokens || 0,
    };

    return res.status(200).json({
      success: true,
      usage_stats: stats
    });

  } catch (error) {
    console.log('Error getting usage stats:', error);
    return res.status(500).json({
      error: 'Failed to retrieve usage statistics',
      details: error.message
    });
  }
};

module.exports = handler;
