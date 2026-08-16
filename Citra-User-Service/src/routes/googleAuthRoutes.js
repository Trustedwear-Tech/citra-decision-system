// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const express = require('express');
const router = express.Router();
const { authenticateToken } = require('../middleware/authMiddleware');

// Genuinely Google-OAuth-specific routes only. /me, /users, /logout,
// /send-welcome-email and /delete-account used to live here too, but they
// are provider-agnostic (JWT + user-doc lookup, nothing Google about them)
// -- they were only reachable when AUTH_PROVIDERS included 'google', so a
// local-only deployment had no working /me or /logout. Moved to
// accountRoutes.js, mounted unconditionally in server.js. See that file's
// header comment.
const {
  googleAuthHandler,
  refreshUserDataHandler,
} = require('../functions/google-auth');

const {
  healthCheckHandler
} = require('../functions/google-auth-health');

/**
 * @route   GET /api/auth/health
 * @desc    Health check for Google authentication system
 * @access  Public
 */
router.get('/health', healthCheckHandler);

/**
 * @route   POST /api/auth/google
 * @desc    Authenticate user with Google OAuth
 * @access  Public
 * @body    { idToken: string, phoneNumber?: string }
 */
router.post('/google', googleAuthHandler);

/**
 * @route   POST /api/auth/refresh
 * @desc    Refresh user data from Google
 * @access  Private (requires JWT token)
 * @headers Authorization: Bearer <token>
 * @body    { idToken: string }
 */
router.post('/refresh', authenticateToken, refreshUserDataHandler);

module.exports = router;

