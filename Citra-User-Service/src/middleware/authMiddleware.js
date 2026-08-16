// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const googleAuthService = require('../services/googleAuthService');
const tokenService = require('../services/tokenService');
const envConfig = require('../config/environment');
const revocationService = require('../services/revocationService');
const { ROLES } = require('../constants/roles');

/**
 * JWT Authentication Middleware
 * Validates JWT tokens and attaches user information to request object
 */
const authenticateToken = async (req, res, next) => {
  try {
    const authHeader = req.headers['authorization'];
    const token = authHeader && authHeader.split(' ')[1]; // Bearer TOKEN

    if (!token) {
      return res.status(401).json({
        success: false,
        error: 'Access token required'
      });
    }

    // Verify and decode token (provider-agnostic)
    const decoded = tokenService.verifyToken(token);

    // Revocation check — reject tokens whose jti is on the blocklist
    // (impersonation revoke, demotion, leaked token). Cache-fronted, so this
    // is at most one Mongo read per jti per cache window.
    if (decoded.jti && (await revocationService.isRevoked(decoded.jti))) {
      return res.status(401).json({
        success: false,
        error: 'Token has been revoked',
      });
    }

    // Attach user info to request
    req.user = decoded;
    req.token = token;

    next();
  } catch (error) {
    return res.status(403).json({
      success: false,
      error: 'Invalid or expired token',
      details: error.message
    });
  }
};

/**
 * Optional JWT Authentication Middleware
 * Similar to authenticateToken but doesn't fail if no token is provided
 */
const optionalAuthenticateToken = async (req, res, next) => {
  try {
    const authHeader = req.headers['authorization'];
    const token = authHeader && authHeader.split(' ')[1]; // Bearer TOKEN

    if (token) {
      // Verify and decode token if provided (provider-agnostic)
      const decoded = tokenService.verifyToken(token);
      // A revoked token must not be honoured anywhere, including optional-auth
      // routes — treat it as "no token" rather than attaching a dead identity.
      if (decoded.jti && (await revocationService.isRevoked(decoded.jti))) {
        return res.status(401).json({
          success: false,
          error: 'Token has been revoked',
        });
      }
      req.user = decoded;
      req.token = token;
    }

    next();
  } catch (error) {
    // If token is provided but invalid, still fail
    return res.status(403).json({
      success: false,
      error: 'Invalid or expired token',
      details: error.message
    });
  }
};

/**
 * Validate Google ID Token Middleware
 * For routes that accept Google ID tokens directly
 */
const validateGoogleToken = async (req, res, next) => {
  try {
    const { idToken } = req.body;

    if (!idToken) {
      return res.status(400).json({
        success: false,
        error: 'Google ID token required'
      });
    }

    // Verify Google ID token
    const payload = await googleAuthService.verifyIdToken(idToken);

    // Attach Google user info to request
    req.googleUser = payload;

    next();
  } catch (error) {
    return res.status(401).json({
      success: false,
      error: 'Invalid Google token',
      details: error.message
    });
  }
};

/**
 * Error handler for authentication errors
 */
const authErrorHandler = (error, req, res, next) => {
  if (error.name === 'JsonWebTokenError') {
    return res.status(401).json({
      success: false,
      error: 'Invalid token'
    });
  } else if (error.name === 'TokenExpiredError') {
    return res.status(401).json({
      success: false,
      error: 'Token expired'
    });
  } else if (error.name === 'NotBeforeError') {
    return res.status(401).json({
      success: false,
      error: 'Token not active'
    });
  }

  next(error);
};

/**
 * Access Validation Middleware
 * Checks if user has valid trial or subscription to access features
 */
const validateAccess = async (req, res, next) => {
  try {
    const CitraAIUser = require('../models/CitraAIUser');
    const CitraAISubscription = require('../models/CitraAISubscription');

    // User should be authenticated first (use authenticateToken before this middleware)
    if (!req.user || !req.user.email) {
      return res.status(401).json({
        success: false,
        error: 'Authentication required',
        requiresAction: 'login'
      });
    }

    // Find user
    const user = await CitraAIUser.findOne({ email: req.user.email });

    if (!user) {
      return res.status(404).json({
        success: false,
        error: 'User not found',
        requiresAction: 'signup'
      });
    }

    // Check if trial has expired
    if (user.user_type === 'free_trial' && !user.hasActiveTrial()) {
      await user.expireTrial();
    }

    // Check for active subscription if user is paid type
    if (user.user_type === 'paid') {
      const activeSubscription = await CitraAISubscription.findActiveByuser_id(user._id.toString());

      if (!activeSubscription || !activeSubscription.isValidSubscription()) {
        user.user_type = 'free';
        user.has_active_subscription = false;
        await user.save();
      }
    }

    // Check if user has valid access
    const hasAccess = user.hasValidAccess();

    if (!hasAccess) {
      let requiresAction = 'no_access';
      let message = 'No active trial or subscription';

      if (user.user_type === 'free_trial') {
        requiresAction = 'trial_expired';
        message = 'Your free trial has expired. Please subscribe to continue.';
      } else if (user.user_type === 'paid') {
        requiresAction = 'subscription_expired';
        message = 'Your subscription has expired. Please renew to continue.';
      } else {
        requiresAction = 'no_access';
        message = 'Please subscribe or use a coupon code to access this feature.';
      }

      return res.status(403).json({
        success: false,
        error: message,
        requiresAction,
        hasAccess: false,
        user: {
          id: user._id,
          email: user.email,
          user_type: user.user_type
        }
      });
    }

    // User has valid access, attach to request
    req.userWithAccess = user;
    next();

  } catch (error) {
    console.error('Access validation error:', error);
    return res.status(500).json({
      success: false,
      error: 'Failed to validate access',
      details: error.message
    });
  }
};

/**
 * Admin Authorization Middleware
 * Must be used AFTER authenticateToken. The user qualifies as an admin
 * if EITHER:
 *   (a) their email is listed in the ADMIN_EMAILS env var (legacy gate),
 *       OR
 *   (b) their JWT roles claim contains super_admin / org_admin / dept_admin
 *       (the modern role-based gate, populated by /api/admin/users invite
 *       or by create-admin.js bootstrap).
 *
 * This unification keeps backward-compat with deployments that still
 * rely on ADMIN_EMAILS while the new role-based onboarding fills in.
 */
const ADMIN_ROLES = new Set([ROLES.SUPER_ADMIN, ROLES.ORG_ADMIN, ROLES.DEPT_ADMIN]);

const requireAdmin = (req, res, next) => {
  if (!req.user || !req.user.email) {
    return res.status(401).json({
      success: false,
      error: 'Authentication required'
    });
  }

  const byEnv = envConfig.isAdmin(req.user.email);
  const roles = req.user.roles || [];
  const byRole = roles.some((r) => ADMIN_ROLES.has(r));

  if (!(byEnv || byRole)) {
    return res.status(403).json({
      success: false,
      error: 'Admin access required',
    });
  }

  next();
};

/**
 * Workflow Authorization Middleware
 * Must be used AFTER authenticateToken. The user qualifies for the
 * workflow surface (build / list / run / edit) if their JWT roles claim
 * contains ANY of:
 *   - super_admin
 *   - org_admin
 *   - IT-workflow                              (the dedicated access flag)
 *   - dept_admin AND dept_ids includes IT_DEPT_ID
 *
 * IT_DEPT_ID is the well-known slug for the IT department; defaults to
 * "it" and is overridable via WORKFLOW_IT_DEPT_ID env var so multi-IT-dept
 * deployments can rename it. Per design: every customer has at most one IT
 * dept that admins workflows.
 */
const WORKFLOW_ROLES = new Set([ROLES.SUPER_ADMIN, ROLES.ORG_ADMIN, ROLES.IT_WORKFLOW]);
const IT_DEPT_ID = (process.env.WORKFLOW_IT_DEPT_ID || 'it').toLowerCase();

const hasWorkflowAccess = (user) => {
  if (!user) return false;
  const roles = user.roles || [];
  if (roles.some((r) => WORKFLOW_ROLES.has(r))) return true;
  if (roles.includes(ROLES.DEPT_ADMIN)) {
    const depts = (user.dept_ids || []).map((d) => String(d).toLowerCase());
    if (depts.includes(IT_DEPT_ID)) return true;
  }
  return false;
};

const requireWorkflowRole = (req, res, next) => {
  if (!req.user || !req.user.email) {
    return res.status(401).json({
      success: false,
      error: 'Authentication required',
    });
  }
  if (!hasWorkflowAccess(req.user)) {
    return res.status(403).json({
      success: false,
      error: 'Workflow access requires IT-workflow, org_admin, super_admin, or IT-dept admin role',
    });
  }
  next();
};

module.exports = {
  authenticateToken,
  optionalAuthenticateToken,
  requireAdmin,
  requireWorkflowRole,
  hasWorkflowAccess,
  IT_DEPT_ID,
  validateGoogleToken,
  authErrorHandler,
  validateAccess
};
