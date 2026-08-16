// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const express = require('express');
const router = express.Router();
const { authenticateToken, requireAdmin } = require('../middleware/authMiddleware');

// Usage stats and tracking handlers
const getUsageStatsHandler = require('../functions/get-usage-stats');
const checkCreditsHandler = require('../functions/check-credits');
const trackQueryUsageHandler = require('../functions/track-query-usage');
const getLicenseStatsHandler = require('../functions/get-license-stats');
const getOrgTokenUsageHandler = require('../functions/get-org-token-usage');
// Note: Daily usage trend moved to Python Citra AI Service
// GET /citra-ai/api/usage/get-daily-usage-trend?user_id=X&days=30

// ============= USAGE TRACKING ROUTES (LICENSE MODEL) =============
// Usage stats — returns consumption data; credit_balance overridden to UNLIMITED
router.post('/get-credits-usage-stats', authenticateToken, getUsageStatsHandler);
router.post('/check-credits', authenticateToken, checkCreditsHandler);
router.post('/track-query-usage', authenticateToken, trackQueryUsageHandler);

// ============= LICENSE MANAGEMENT ROUTES =============
// Admin: user count metrics for license reporting
router.get('/license-stats', authenticateToken, requireAdmin, getLicenseStatsHandler);

// Admin: per-user token consumption across the org (leaderboard + rollup)
router.get('/admin/token-usage', authenticateToken, requireAdmin, getOrgTokenUsageHandler);

module.exports = router;

