// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Check Credits — License model: always returns sufficient.
 * Usage tracking continues but no enforcement.
 */
const handler = async (req, res) => {
  return res.status(200).json({
    success: true,
    sufficient: true,
    balance: 999999999,
    required: req.body?.required_amount || req.body?.estimated_cost || 0,
    message: 'Unlimited (license model)'
  });
};

module.exports = handler;


