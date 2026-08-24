// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const CitraAIUser = require('../models/CitraAIUser');

/**
 * GET /api/subscriptions/license-stats
 * Admin-only endpoint: returns user counts for license reporting.
 * - total_users: all registered users
 * - active_last_30_days: users who logged in within the last 30 days
 * - active_last_7_days: users who logged in within the last 7 days
 */
const handler = async (req, res) => {
  try {
    const now = new Date();
    const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    const [total_users, active_last_30_days, active_last_7_days] = await Promise.all([
      CitraAIUser.countDocuments({}),
      CitraAIUser.countDocuments({ lastLogin: { $gte: thirtyDaysAgo } }),
      CitraAIUser.countDocuments({ lastLogin: { $gte: sevenDaysAgo } }),
    ]);

    return res.status(200).json({
      success: true,
      license_stats: {
        total_users,
        active_last_30_days,
        active_last_7_days,
        generated_at: now.toISOString(),
      },
    });
  } catch (error) {
    console.error('[LicenseStats] Error:', error);
    return res.status(500).json({
      error: 'Failed to retrieve license stats',
      details: error.message,
    });
  }
};

module.exports = handler;
