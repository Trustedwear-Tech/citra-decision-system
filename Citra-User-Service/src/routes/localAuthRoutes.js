// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const express = require('express');
const router = express.Router();
const localAuthService = require('../services/localAuthService');
const { authenticateToken } = require('../middleware/authMiddleware');
const { connectToMongoDB } = require('../config/mongodb');

// ── Input validation helpers ───────────────────────────────────────

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 8;

function validateEmail(email) {
  return typeof email === 'string' && EMAIL_RE.test(email.trim());
}

function validatePassword(password) {
  return typeof password === 'string' && password.length >= MIN_PASSWORD_LENGTH;
}

// ── Routes ─────────────────────────────────────────────────────────

/**
 * POST /api/auth/local/register
 * Register a new user with email + password
 */
router.post('/register', async (req, res) => {
  try {
    await connectToMongoDB();

    const { email, password, name, termsAcceptedAt } = req.body;

    if (!validateEmail(email)) {
      return res.status(400).json({ success: false, error: 'Valid email is required.' });
    }
    if (!validatePassword(password)) {
      return res.status(400).json({ success: false, error: `Password must be at least ${MIN_PASSWORD_LENGTH} characters.` });
    }

    const result = await localAuthService.register({ email, password, name, termsAcceptedAt });

    return res.status(201).json({ success: true, message: 'Registration successful. Please check your email to verify your account.', data: result });
  } catch (error) {
    const status = error.statusCode || 500;
    return res.status(status).json({ success: false, error: error.message, code: error.code });
  }
});

/**
 * POST /api/auth/local/login
 * Login with email + password
 */
router.post('/login', async (req, res) => {
  try {
    await connectToMongoDB();

    const { email, password } = req.body;

    if (!validateEmail(email)) {
      return res.status(400).json({ success: false, error: 'Valid email is required.' });
    }
    if (!password) {
      return res.status(400).json({ success: false, error: 'Password is required.' });
    }

    const result = await localAuthService.login({ email, password });

    return res.status(200).json({ success: true, message: 'Login successful.', data: result });
  } catch (error) {
    const status = error.statusCode || 500;
    return res.status(status).json({ success: false, error: error.message, code: error.code });
  }
});

/**
 * GET /api/auth/local/verify-email?token=...
 * Verify email address
 */
router.get('/verify-email', async (req, res) => {
  try {
    await connectToMongoDB();

    const { token } = req.query;
    if (!token) {
      return res.status(400).json({ success: false, error: 'Verification token is required.' });
    }

    const result = await localAuthService.verifyEmail(token);

    return res.status(200).json({ success: true, ...result });
  } catch (error) {
    const status = error.statusCode || 500;
    return res.status(status).json({ success: false, error: error.message });
  }
});

/**
 * POST /api/auth/local/resend-verification
 * Resend verification email
 */
router.post('/resend-verification', async (req, res) => {
  try {
    await connectToMongoDB();

    const { email } = req.body;
    if (!validateEmail(email)) {
      return res.status(400).json({ success: false, error: 'Valid email is required.' });
    }

    const result = await localAuthService.resendVerification(email);

    return res.status(200).json({ success: true, ...result });
  } catch (error) {
    return res.status(500).json({ success: false, error: error.message });
  }
});

/**
 * POST /api/auth/local/forgot-password
 * Request password reset email
 */
router.post('/forgot-password', async (req, res) => {
  try {
    await connectToMongoDB();

    const { email } = req.body;
    if (!validateEmail(email)) {
      return res.status(400).json({ success: false, error: 'Valid email is required.' });
    }

    const result = await localAuthService.forgotPassword(email);

    return res.status(200).json({ success: true, ...result });
  } catch (error) {
    return res.status(500).json({ success: false, error: error.message });
  }
});

/**
 * POST /api/auth/local/reset-password
 * Reset password with token
 */
router.post('/reset-password', async (req, res) => {
  try {
    await connectToMongoDB();

    const { token, password } = req.body;
    if (!token) {
      return res.status(400).json({ success: false, error: 'Reset token is required.' });
    }
    if (!validatePassword(password)) {
      return res.status(400).json({ success: false, error: `Password must be at least ${MIN_PASSWORD_LENGTH} characters.` });
    }

    const result = await localAuthService.resetPassword(token, password);

    return res.status(200).json({ success: true, ...result });
  } catch (error) {
    const status = error.statusCode || 500;
    return res.status(status).json({ success: false, error: error.message });
  }
});

/**
 * POST /api/auth/local/change-password
 * Change password (authenticated)
 */
router.post('/change-password', authenticateToken, async (req, res) => {
  try {
    await connectToMongoDB();

    const { currentPassword, newPassword } = req.body;
    if (!currentPassword) {
      return res.status(400).json({ success: false, error: 'Current password is required.' });
    }
    if (!validatePassword(newPassword)) {
      return res.status(400).json({ success: false, error: `New password must be at least ${MIN_PASSWORD_LENGTH} characters.` });
    }

    const result = await localAuthService.changePassword(req.user.email, currentPassword, newPassword);

    return res.status(200).json({ success: true, ...result });
  } catch (error) {
    const status = error.statusCode || 500;
    return res.status(status).json({ success: false, error: error.message, code: error.code });
  }
});

module.exports = router;
