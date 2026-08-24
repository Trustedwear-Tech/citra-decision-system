// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const express = require('express');
const rateLimit = require('express-rate-limit');
const router = express.Router();
const { authenticateToken } = require('../middleware/authMiddleware');

// Rate limit for email endpoints (scoped to this router only)
const emailLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 10,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many email requests, please try again later.' }
});

// Import Azure Function handlers and convert them
// REMOVED: Missing files (not converted from Azure Functions yet)
// const sendEncryptionKeyHandler = require('../functions/send-encryption-key');
// DISABLED: sendMailToNominee.js uses undefined executeQuery and Azure Function pattern — non-functional
// const sendMailToNomineeHandler = require('../functions/sendMailToNominee');
const sendContactEmailHandler = require('../functions/send-contact-email');
const sendTemplatedEmailHandler = require('../functions/sendTemplatedEmail');
const sendWorkflowEmailHandler = require('../functions/send-workflow-email');

// Email routes
// DISABLED: sendMailToNominee is non-functional (uses undefined executeQuery against SQL tables that don't exist)
// router.post('/send-mail-to-nominee', authenticateToken, sendMailToNomineeHandler);

// Public endpoint - no authentication required for contact form
router.post('/send-contact-email', emailLimiter, sendContactEmailHandler);

// Workflow transactional email - internal use by Citra-Service workflow engine
router.post('/send-workflow-email', emailLimiter, sendWorkflowEmailHandler);

// Templated email endpoint - authenticated users
router.post('/send', emailLimiter, authenticateToken, sendTemplatedEmailHandler);

module.exports = router;

