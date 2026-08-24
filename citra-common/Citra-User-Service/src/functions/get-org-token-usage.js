// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const CreditTransaction = require('../models/CreditTransaction');

/**
 * GET /api/subscriptions/admin/token-usage
 * Admin-only endpoint: per-user token consumption across the whole org,
 * for token management. Aggregates the `credittransactions` collection
 * grouped by user, plus an org-wide rollup.
 *
 * Query params:
 *   - days (optional): restrict to the last N days (1-365). Omit for all-time.
 *
 * Response:
 *   {
 *     success: true,
 *     range: { days, since },
 *     org_totals: { total_tokens, input_tokens, output_tokens, cached_tokens, total_queries, user_count },
 *     leaderboard: [ { user_id, email, total_tokens, input_tokens, output_tokens, cached_tokens, query_count } ]
 *   }
 */
const handler = async (req, res) => {
  try {
    const match = { type: 'query_usage' };

    let sinceIso = null;
    const daysRaw = req.query.days;
    if (daysRaw !== undefined) {
      const days = parseInt(daysRaw, 10);
      if (Number.isNaN(days) || days <= 0 || days > 365) {
        return res.status(400).json({ success: false, error: 'days must be between 1 and 365' });
      }
      const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
      sinceIso = since.toISOString();
      match.timestamp = { $gte: since };
    }

    const grouped = await CreditTransaction.aggregate([
      { $match: match },
      {
        $group: {
          _id: '$user_id',
          email: { $first: '$email' },
          total_tokens: { $sum: { $ifNull: ['$query_metadata.tokens_used', 0] } },
          input_tokens: { $sum: { $ifNull: ['$query_metadata.input_tokens', 0] } },
          output_tokens: { $sum: { $ifNull: ['$query_metadata.output_tokens', 0] } },
          cached_tokens: { $sum: { $ifNull: ['$query_metadata.cached_tokens', 0] } },
          query_count: { $sum: 1 },
        },
      },
      { $sort: { total_tokens: -1 } },
    ]);

    const leaderboard = grouped.map((g) => ({
      user_id: g._id,
      email: g.email || g._id,
      total_tokens: g.total_tokens || 0,
      input_tokens: g.input_tokens || 0,
      output_tokens: g.output_tokens || 0,
      cached_tokens: g.cached_tokens || 0,
      query_count: g.query_count || 0,
    }));

    const org_totals = leaderboard.reduce(
      (acc, u) => {
        acc.total_tokens += u.total_tokens;
        acc.input_tokens += u.input_tokens;
        acc.output_tokens += u.output_tokens;
        acc.cached_tokens += u.cached_tokens;
        acc.total_queries += u.query_count;
        return acc;
      },
      { total_tokens: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, total_queries: 0, user_count: leaderboard.length }
    );

    return res.status(200).json({
      success: true,
      range: { days: daysRaw !== undefined ? parseInt(daysRaw, 10) : null, since: sinceIso },
      org_totals,
      leaderboard,
    });
  } catch (error) {
    console.error('[OrgTokenUsage] Error:', error);
    return res.status(500).json({
      success: false,
      error: 'Failed to retrieve organization token usage',
      details: error.message,
    });
  }
};

module.exports = handler;
