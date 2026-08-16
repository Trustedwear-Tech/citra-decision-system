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

// These handlers live in google-auth.js / delete-account.js for historical
// reasons (that's where Google auth was first built), but none of them are
// Google-specific -- they read the JWT and look up the user document, same
// as a local- or OIDC-authenticated session would. Mounting them here,
// unconditionally, fixes a real bug: they used to be reachable only when
// AUTH_PROVIDERS included 'google' (see googleAuthRoutes.js), so a
// local-only deployment (AUTH_PROVIDERS=local) had no working /me or
// /logout -- every session check 404'd. See ARCHITECTURE.md / the
// 2026-08-09 auth-strip note.
const {
  getCurrentUserHandler,
  getAllUsersHandler,
  logoutHandler,
  sendWelcomeEmailHandler
} = require('../functions/google-auth');

const {
  deleteAccountHandler
} = require('../functions/delete-account');

/**
 * @route   GET /api/auth/me
 * @desc    Get current user information
 * @access  Private (requires JWT token)
 * @headers Authorization: Bearer <token>
 */
router.get('/me', authenticateToken, getCurrentUserHandler);

/**
 * @route   GET /api/auth/users
 * @desc    Get all users (admin only)
 * @access  Private (admin only)
 * @query   page?: number, limit?: number
 */
router.get('/users', authenticateToken, requireAdmin, getAllUsersHandler);

/**
 * @route   POST /api/auth/logout
 * @desc    Logout user
 * @access  Private (requires JWT token)
 * @headers Authorization: Bearer <token>
 */
router.post('/logout', authenticateToken, logoutHandler);

/**
 * @route   POST /api/auth/send-welcome-email
 * @desc    Send welcome email to a user based on their profession
 * @access  Private (requires JWT token)
 * @headers Authorization: Bearer <token>
 */
router.post('/send-welcome-email', authenticateToken, sendWelcomeEmailHandler);

/**
 * @route   DELETE /api/auth/delete-account
 * @desc    Self-service account deletion is DISABLED.
 *          Self-delete used to wipe the user's workflows + smart-apps,
 *          which silently destroyed resources that teammates depended
 *          on. Under SA-only ownership, deletion is admin-driven and
 *          goes through the per-resource picker at
 *          POST /api/admin/users/:userId/preflight-delete then
 *          POST /api/admin/users/:userId/delete.
 *
 *          The endpoint still exists but only super_admin can call it
 *          (for legitimate GDPR-emergency cleanup); regular users get
 *          410 Gone with the admin contact path.
 * @access  super_admin only
 */
router.delete('/delete-account', authenticateToken, (req, res, next) => {
  const roles = (req.user && req.user.roles) || [];
  if (!roles.includes('super_admin')) {
    return res.status(410).json({
      success: false,
      error: 'self_delete_disabled',
      message:
        'Self-service account deletion has been removed. Contact your org_admin '
        + 'or dept_admin who can run the admin-driven delete flow at '
        + '/admin/users — that flow lets them choose what happens to each '
        + 'workflow and smart-app you own before the account is removed.',
    });
  }
  return deleteAccountHandler(req, res, next);
});

module.exports = router;
