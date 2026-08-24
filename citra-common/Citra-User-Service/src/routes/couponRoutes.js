// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const express = require('express');
const router = express.Router();
const { authenticateToken, requireAdmin } = require('../middleware/authMiddleware');

// Coupon system disabled — on-premises license model, coupons not applicable

router.post('/validate', authenticateToken, (req, res) => {
  res.status(410).json({ success: false, message: 'Coupon system not available.' });
});

router.post('/apply', authenticateToken, (req, res) => {
  res.status(410).json({ success: false, message: 'Coupon system not available.' });
});

router.post('/create', authenticateToken, requireAdmin, (req, res) => {
  res.status(410).json({ success: false, message: 'Coupon system not available.' });
});

router.post('/check-access', authenticateToken, (req, res) => {
  res.json({ has_access: true, access_type: 'license' });
});

module.exports = router;

