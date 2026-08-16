// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const googleAuthService = require('../services/googleAuthService');
const CitraAIUser = require('../models/CitraAIUser');
const { connectToMongoDB } = require('../config/mongodb');
const usageTrackingService = require('../services/usageTrackingService');
const { ensurePersonalSA } = require('../services/personalSAService');
const { ensureWorkSA } = require('../services/workSAService');
const { applyToUserDoc } = require('../services/deploymentDefaults');

/**
 * Google Authentication Handler
 * Handles Google OAuth authentication and user creation/update
 * Supports both ID token (One Tap) and Access token (OAuth popup) flows
 * Express.js handler
 */
const googleAuthHandler = async (req, res) => {
  try {
    // Ensure MongoDB connection
    const conn = await connectToMongoDB();
    try {
      const { getConnectionStatus } = require('../config/mongodb');
      console.log('MongoDB connection status:', getConnectionStatus());
    } catch (e) {
      console.log('MongoDB status check failed:', e.message);
    }

    // Get request body from Express
    const requestBody = req.body;
    
    // Log request for debugging (without sensitive token data)
    const sanitizedBody = {
      phoneNumber: requestBody.phoneNumber,
      authMode: requestBody.authMode,
      platform: requestBody.platform,
      hasIdToken: !!requestBody.idToken,
      hasAccessToken: !!requestBody.accessToken,
      hasGoogleUserInfo: !!requestBody.googleUserInfo,
      hasDeviceFingerprint: !!requestBody.deviceFingerprint
    };
    console.log('Request Body (sanitized):', sanitizedBody);

    const { idToken, accessToken, googleUserInfo, phoneNumber, deviceFingerprint, termsAcceptedAt } = requestBody;

    // Capture request metadata for fingerprint tracking
    const reqMeta = {
      ipAddress: req.ip || req.headers['x-forwarded-for'] || req.connection?.remoteAddress || null,
      userAgent: req.headers['user-agent'] || null,
      deviceFingerprint: deviceFingerprint || null,
      termsAcceptedAt: termsAcceptedAt || null,
    };
    if (reqMeta.deviceFingerprint) {
      console.log('🔑 [AUTH] Device fingerprint received:', reqMeta.deviceFingerprint.substring(0, 12) + '...');
    }

    // Validate required fields - accept either idToken OR accessToken+googleUserInfo
    if (!idToken && !(accessToken && googleUserInfo)) {
      return res.status(400).json({ 
        success: false, 
        error: 'Google ID token or access token with user info is required' 
      });
    }

    // Authenticate with Google
    let authResult;
    try {
      if (idToken) {
        // ID Token flow (One Tap, traditional OAuth)
        authResult = await googleAuthService.authenticateWithGoogle(idToken, phoneNumber, reqMeta);
      } else {
        // Access Token flow (OAuth popup) - use googleUserInfo directly
        console.log('🔐 [AUTH] Using access token flow with user info:', { email: googleUserInfo.email });
        authResult = await googleAuthService.authenticateWithAccessToken(accessToken, googleUserInfo, phoneNumber, reqMeta);
      }
      // Log successful authentication without sensitive token data
      console.log('Google authentication completed for user:', authResult.user.email);
    } catch (authErr) {
      console.error('Google authentication service error:', authErr && authErr.message ? authErr.message : authErr);
      throw authErr;
    }

    console.log(`Google authentication successful for user: ${authResult.user.email}`);

    return res.status(200).json({
      success: true,
      message: 'Authentication successful',
      data: authResult
    });

  } catch (error) {
    console.error('Google authentication error:', error);

    // Check if this is an access denial error (403)
    if (error.statusCode === 403 && error.accessStatus) {
      return res.status(403).json({ 
        success: false, 
        error: error.message || 'Access denied',
        accessStatus: error.accessStatus,
        user: error.user,
        token: error.token,
        isNewUser: error.isNewUser,
        requiresAction: error.accessStatus.requiresAction
      });
    }

    return res.status(401).json({ 
      success: false, 
      error: error.message || 'Authentication failed'
    });
  }
};

/**
 * Get Current User Handler
 * Returns current user information based on JWT token
 * Express.js handler
 */
const getCurrentUserHandler = async (req, res) => {
  try {
    // Ensure MongoDB connection
    await connectToMongoDB();

    // Extract token from Authorization header
    const authHeader = req.headers.authorization || req.headers.Authorization;
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      return res.status(401).json({ 
        success: false, 
        error: 'Access token required',
        requiresReauth: true
      });
    }

    try {
      // Get user by token
      const user = await googleAuthService.getUserByToken(token);

      // Self-heal missing Service Accounts. If a user lands on /me with a
      // stale JWT (work_sa_id null because the original ensure ran before
      // they had an org_id, or the SA doc was deleted out-of-band), repair
      // the user doc here. /me cannot remint the JWT, so the next login
      // will pick up the fresh ids and the banner clears without admin
      // intervention. Also backfill org_id from the single-tenant env tag
      // since ensureWorkSA bails silently when org_id is missing.
      try {
        const dbUser = await CitraAIUser.findOne({ email: user.email });
        if (dbUser) {
          const changed = applyToUserDoc(dbUser);
          if (changed) {
            await dbUser.save();
            console.log(`[AUTH/ME][defaults-backfill] org_id=${dbUser.org_id} dept_ids=${JSON.stringify(dbUser.dept_ids)} for ${dbUser.email}`);
          }
          await ensurePersonalSA(dbUser);
          await ensureWorkSA(dbUser);
          if (dbUser.personal_sa_id) user.personal_sa_id = dbUser.personal_sa_id;
          if (dbUser.work_sa_id)     user.work_sa_id     = dbUser.work_sa_id;
        }
      } catch (saErr) {
        console.warn('[AUTH/ME] SA self-heal failed (continuing):', saErr.message || saErr);
      }

      console.log(`Retrieved user data for: ${user.email}`);

      // Fetch credit balance from usage tracking service
      let credits_balance = null;
      try {
        const usageStats = await usageTrackingService.getUsageStats(user.email, user.email);
        credits_balance = usageStats.credit_balance;
        console.log(`✅ [AUTH/ME] Retrieved credit balance for ${user.email}: ${credits_balance} credits`);
      } catch (creditsError) {
        console.error('❌ [AUTH/ME] Failed to fetch credit balance:', creditsError.message);
        // Don't fail the request if credits can't be fetched
      }

      const normalizedUser = typeof user?.toObject === 'function' ? user.toObject() : user;
      const enhancedUser = {
        ...normalizedUser,
        credits_balance,
        credits: typeof credits_balance === 'number'
          ? {
              balance: credits_balance,
              available: credits_balance,
              remaining: credits_balance
            }
          : normalizedUser?.credits
      };

      const responseData = {
        success: true,
        data: {
          user: enhancedUser
        }
      };
      
      console.log(`📤 [AUTH/ME] Sending response for ${user.email}:`, JSON.stringify({
        email: user.email,
        credits_balance,
        hasCredits: credits_balance !== null
      }));

      return res.status(200).json(responseData);

    } catch (userError) {
      // Check if this is a "user not found" error (deleted user)
      if (userError.message.includes('User account not found') || 
          userError.message.includes('User not found')) {
        
        console.log('User account was deleted from database, requiring re-authentication');

        return res.status(401).json({ 
          success: false, 
          error: 'User account not found. Please re-authenticate with Google.',
          requiresReauth: true,
          reason: 'user_deleted'
        });
      }

      // For other authentication errors (invalid token, etc.)
      console.error('Token verification failed:', userError.message);

      return res.status(401).json({ 
        success: false, 
        error: userError.message || 'Invalid or expired token',
        requiresReauth: true,
        reason: 'token_invalid'
      });
    }

  } catch (error) {
    console.error('Get current user error:', error);

    return res.status(500).json({ 
      success: false, 
      error: error.message || 'Internal server error'
    });
  }
};

/**
 * Get All Users Handler
 * Returns all Citra AI users (utility endpoint)
 * Express.js handler
 */
const getAllUsersHandler = async (req, res) => {
  try {
    // Ensure MongoDB connection
    await connectToMongoDB();

    // Get query parameters for pagination
    const page = parseInt(req.query.page || '1');
    const limit = parseInt(req.query.limit || '10');
    const skip = (page - 1) * limit;

    // Get users with pagination
    const users = await CitraAIUser.find({})
      .sort({ createdAt: -1 })
      .skip(skip)
      .limit(limit)
      .select('-__v');

    // Get total count
    const totalUsers = await CitraAIUser.countDocuments({});

    console.log(`Retrieved ${users.length} users (page ${page})`);

    return res.status(200).json({
      success: true,
      data: {
        users,
        pagination: {
          currentPage: page,
          totalPages: Math.ceil(totalUsers / limit),
          totalUsers,
          usersPerPage: limit
        }
      }
    });

  } catch (error) {
    console.error('Get all users error:', error);

    return res.status(500).json({ 
      success: false, 
      error: error.message || 'Failed to retrieve users'
    });
  }
};

/**
 * Refresh User Data Handler
 * Refreshes user data from Google
 * Express.js handler
 */
const refreshUserDataHandler = async (req, res) => {
  try {
    // Ensure MongoDB connection
    await connectToMongoDB();

    const { idToken } = req.body;

    // Extract token from Authorization header
    const authHeader = req.headers.authorization || req.headers.Authorization;
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      return res.status(401).json({ 
        success: false, 
        error: 'Access token required' 
      });
    }

    if (!idToken) {
      return res.status(400).json({ 
        success: false, 
        error: 'Google ID token is required' 
      });
    }

    // Verify current JWT token to get user ID
    const decoded = await googleAuthService.verifyJwtToken(token);
    
    // Use user_id (MongoDB _id) - fail if not available to prevent auth confusion
    const userId = decoded.user_id;
    if (!userId) {
      throw new Error('User ID not found in token. Please re-authenticate.');
    }
    
    const updatedUser = await googleAuthService.refreshUserData(userId, idToken);

    console.log(`Refreshed user data for: ${updatedUser.email}`);

    return res.status(200).json({
      success: true,
      message: 'User data refreshed successfully',
      data: { user: updatedUser }
    });

  } catch (error) {
    console.error('Refresh user data error:', error);

    return res.status(400).json({ 
      success: false, 
      error: error.message || 'Failed to refresh user data'
    });
  }
};

/**
 * Logout Handler
 * Handles user logout
 * Express.js handler
 */
const logoutHandler = async (req, res) => {
  try {
    // Ensure MongoDB connection
    await connectToMongoDB();

    // Extract token from Authorization header
    const authHeader = req.headers.authorization || req.headers.Authorization;
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      return res.status(401).json({ 
        success: false, 
        error: 'Access token required' 
      });
    }

    // Verify current JWT token to get user ID
    const decoded = await googleAuthService.verifyJwtToken(token);
    
    // Use user_id (MongoDB _id) - fail if not available to prevent auth confusion
    const userId = decoded.user_id;
    if (!userId) {
      throw new Error('User ID not found in token. Please re-authenticate.');
    }
    
    const result = await googleAuthService.logout(userId);

    console.log(`User logged out: ${decoded.email}`);

    return res.status(200).json({
      success: true,
      message: result.message
    });

  } catch (error) {
    console.error('Logout error:', error);

    return res.status(400).json({ 
      success: false, 
      error: error.message || 'Logout failed'
    });
  }
};

/**
 * Send Welcome Email Handler
 * Sends profession-specific welcome email to a user
 * Express.js handler
 */
const sendWelcomeEmailHandler = async (req, res) => {
  try {
    // Ensure MongoDB connection
    await connectToMongoDB();

    // Extract token from Authorization header
    const authHeader = req.headers.authorization || req.headers.Authorization;
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      return res.status(401).json({ 
        success: false, 
        error: 'Access token required'
      });
    }

    const { profession } = req.body;

    // SECURITY: Get authenticated user from JWT token, never trust req.body.email
    const authenticatedUser = await googleAuthService.getUserByToken(token);
    if (!authenticatedUser || !authenticatedUser.email) {
      return res.status(401).json({ 
        success: false, 
        error: 'Invalid or expired token'
      });
    }

    // Use authenticated email — users can only send welcome emails to themselves
    const email = authenticatedUser.email;

    // Find the target user
    const targetUser = await CitraAIUser.findOne({ email: email });
    if (!targetUser) {
      return res.status(404).json({ 
        success: false, 
        error: 'User not found'
      });
    }

    // Send welcome email with profession-specific content
    await googleAuthService.sendWelcomeEmail(targetUser, profession);

    console.log(`Welcome email sent successfully to: ${email} (General Professional)`);

    return res.status(200).json({
      success: true,
      message: 'Welcome email sent successfully'
    });

  } catch (error) {
    console.error('Send welcome email error:', error);

    return res.status(500).json({ 
      success: false, 
      error: error.message || 'Failed to send welcome email'
    });
  }
};

module.exports = {
  googleAuthHandler,
  getCurrentUserHandler,
  getAllUsersHandler,
  refreshUserDataHandler,
  logoutHandler,
  sendWelcomeEmailHandler
};




