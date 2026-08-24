// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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


