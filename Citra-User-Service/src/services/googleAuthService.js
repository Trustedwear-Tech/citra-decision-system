// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const { OAuth2Client } = require('google-auth-library');
const jwt = require('jsonwebtoken');
const envConfig = require('../config/environment');
const CitraAIUser = require('../models/CitraAIUser');
const tokenService = require('./tokenService');
const { ensurePersonalSA } = require('./personalSAService');
const { ensureWorkSA } = require('./workSAService');
const { applyToUserData } = require('./deploymentDefaults');

class GoogleAuthService {
  constructor() {
    // Support multiple client IDs for different platforms
    this.clientIds = envConfig.googleClientIds || [];
    this.client = new OAuth2Client();
  }

  /**
   * Verify Google ID token against multiple client IDs
   * @param {string} idToken - Google ID token
   * @returns {Promise<Object>} - Verified payload
   */
  async verifyIdToken(idToken) {
    // Try to verify the token against each client ID
    for (const clientId of this.clientIds) {
      try {
        const ticket = await this.client.verifyIdToken({
          idToken,
          audience: clientId,
        });

        const payload = ticket.getPayload();

        // Add the client ID that was used for verification to the payload
        payload.verifiedClientId = clientId;

        return payload;
      } catch (error) {
        // Continue to next client ID if this one fails
        continue;
      }
    }

    // If we get here, none of the client IDs worked
    throw new Error('Invalid Google token: Token not valid for any configured client ID');
  }

  /**
   * Generate JWT token (delegates to tokenService).
   * Now async because the token embeds service-account membership
   * which requires a DB lookup at mint time.
   * @param {Object} user - User object
   * @returns {Promise<string>} - JWT token
   */
  async generateJwtToken(user) {
    return tokenService.generateToken(user);
  }

  /**
   * Verify JWT token (delegates to tokenService)
   * @param {string} token - JWT token
   * @returns {Promise<Object>} - Decoded token payload
   */
  async verifyJwtToken(token) {
    return tokenService.verifyToken(token);
  }

  /**
   * Authenticate user with Google
   * @param {string} idToken - Google ID token
   * @param {string} phoneNumber - Optional phone number
   * @returns {Promise<Object>} - User and JWT token
   */
  async authenticateWithGoogle(idToken, phoneNumber = null, reqMeta = {}) {
    try {
      // Verify Google ID token
      const payload = await this.verifyIdToken(idToken);
      console.log('Google ID token verified for user:', payload.email);

      // Create comprehensive user data object
      const userData = {
        email: payload.email,
        name: payload.name || '',
        mobile: phoneNumber || '',
        googleId: payload.sub,
        profilePicture: payload.picture || '',
        authProvider: 'google',
        emailVerified: true, // Google emails are pre-verified
        lastLogin: new Date(),
        isActive: true
      };

      // Deployment defaults (org_id, dept_ids, entity_type) are applied
      // below once we know whether this is a new or existing user. Demo
      // personas seeded by demo-data scripts have their fields pre-set so
      // applyToUserData treats them as no-ops.

      // GDPR: store terms/privacy consent timestamp if provided
      if (reqMeta.termsAcceptedAt) {
        userData.gdpr_consent = {
          terms_accepted_at: new Date(reqMeta.termsAcceptedAt),
          privacy_accepted_at: new Date(reqMeta.termsAcceptedAt),
          consent_version: '1.0',
        };
      }

      // Enhanced user lookup - search by email first, then by googleId
      console.log('Searching for existing user by email:', payload.email);
      let existingUser = await CitraAIUser.findOne({ email: payload.email });

      if (!existingUser && payload.sub) {
        console.log('User not found by email, searching by googleId:', payload.sub);
        existingUser = await CitraAIUser.findByGoogleId(payload.sub);

        // If found by googleId but email is different, update the email
        if (existingUser && existingUser.email !== payload.email) {
          console.log(`Updating user email from ${existingUser.email} to ${payload.email}`);
          userData.email = payload.email; // This will be handled in createOrUpdate
        }
      }

      const isNewUser = !existingUser;
      console.log(`User status: ${isNewUser ? 'NEW' : 'EXISTING'} - Processing ${isNewUser ? 'creation' : 'update'}`);

      // Stamp deployment defaults onto userData: org_id, dept_ids, entity_type.
      // Idempotent — only fills slots that are currently empty, so demo
      // personas (which have their own values pre-set elsewhere) are
      // never affected by this path.
      applyToUserData(userData, existingUser, isNewUser);
      if (!isNewUser && !existingUser.org_id && userData.org_id) {
        console.log(`[org-backfill] Setting org_id=${userData.org_id} for ${existingUser.email}`);
      }

      // Use enhanced createOrUpdate for robust user creation/updating
      const user = await this.createOrUpdateUser(userData, existingUser);

      // CRITICAL FIX: Re-verify isNewUser status
      // If DB glitch caused findOne to fail (isNewUser=true) but createOrUpdateUser found an existing user (via Duplicate Key),
      // Initialize usage tracking for new users
      if (isNewUser) {
        try {
          const usageTrackingService = require('../services/usageTrackingService');
          await usageTrackingService.initializeNewUser(user.email, user.email, 0);
          console.log(`[AUTH] New user ${user.email} initialized`);
        } catch (err) {
          console.warn('Failed to initialize usage tracking:', err.message || err);
        }
      }

      // Idempotently ensure BOTH Service Accounts exist:
      //  - Personal SA: owns ephemeral personal-output resources
      //    (presentations, printables, reports, diagrams).
      //  - Work SA:     owns durable team-portable resources
      //    (skills, smart-apps, workflows). Transferable on user deletion.
      // Safe to call on every login; idempotent.
      try {
        await ensurePersonalSA(user);
      } catch (err) {
        console.warn('[AUTH] ensurePersonalSA failed (continuing):', err.message || err);
      }
      try {
        await ensureWorkSA(user);
      } catch (err) {
        console.warn('[AUTH] ensureWorkSA failed (continuing):', err.message || err);
      }

      // Generate JWT token
      const token = await this.generateJwtToken(user);

      const response = {
        user: user.toJSON(),
        token,
        tokenType: 'Bearer',
        expiresIn: envConfig.jwtExpiresIn || '7d',
        isNewUser,
        accessStatus: { hasAccess: true, user_type: user.user_type || 'paid' }
      };

      return response;

    } catch (error) {
      // If it's our custom access error, re-throw as-is
      if (error.statusCode === 403 && error.accessStatus) {
        throw error;
      }
      throw new Error(`Google authentication failed: ${error.message}`);
    }
  }

  /**
   * Authenticate user with Google Access Token (OAuth popup flow)
   * This is used when ID token is not available (e.g., Google OAuth2 popup flow)
   * @param {string} accessToken - Google access token
   * @param {Object} googleUserInfo - User info from Google userinfo endpoint
   * @param {string} phoneNumber - Optional phone number
   * @returns {Promise<Object>} - User and JWT token
   */
  async authenticateWithAccessToken(accessToken, googleUserInfo, phoneNumber = null, reqMeta = {}) {
    try {
      // Verify access token by making a tokeninfo request to Google
      const tokenInfoUrl = `https://oauth2.googleapis.com/tokeninfo?access_token=${accessToken}`;
      const fetch = require('node-fetch');
      
      const tokenInfoResponse = await fetch(tokenInfoUrl);
      if (!tokenInfoResponse.ok) {
        throw new Error('Invalid access token');
      }
      
      const tokenInfo = await tokenInfoResponse.json();
      console.log('🔐 [AUTH] Access token verified for:', tokenInfo.email);
      
      // Verify email matches
      if (tokenInfo.email !== googleUserInfo.email) {
        throw new Error('Token email does not match user info email');
      }

      // Verify token is for our app (check aud/azp)
      const validClientId = this.clientIds.includes(tokenInfo.aud) || this.clientIds.includes(tokenInfo.azp);
      if (!validClientId) {
        console.warn('⚠️ [AUTH] Token client ID not in allowed list, proceeding anyway:', tokenInfo.aud);
        // Don't throw - some OAuth flows may have different aud values
      }

      // Create user data from googleUserInfo
      const userData = {
        email: googleUserInfo.email,
        name: googleUserInfo.name || '',
        mobile: phoneNumber || '',
        googleId: googleUserInfo.sub,
        profilePicture: googleUserInfo.picture || '',
        authProvider: 'google',
        emailVerified: true, // Google emails are pre-verified
        lastLogin: new Date(),
        isActive: true
      };

      // GDPR: store terms/privacy consent timestamp if provided
      if (reqMeta.termsAcceptedAt) {
        userData.gdpr_consent = {
          terms_accepted_at: new Date(reqMeta.termsAcceptedAt),
          privacy_accepted_at: new Date(reqMeta.termsAcceptedAt),
          consent_version: '1.0',
        };
      }

      console.log('🔐 [AUTH] Processing user with access token flow:', userData.email);

      // Reuse the same user lookup and creation logic as authenticateWithGoogle
      let existingUser = await CitraAIUser.findOne({ email: userData.email });

      if (!existingUser && userData.googleId) {
        console.log('User not found by email, searching by googleId:', userData.googleId);
        existingUser = await CitraAIUser.findByGoogleId(userData.googleId);
      }

      const isNewUser = !existingUser;
      console.log(`User status: ${isNewUser ? 'NEW' : 'EXISTING'} - Processing ${isNewUser ? 'creation' : 'update'}`);

      // Stamp deployment defaults (org_id, dept_ids, entity_type) — see
      // services/deploymentDefaults.js. Mirrors the ID-token flow above.
      applyToUserData(userData, existingUser, isNewUser);

      // Use enhanced createOrUpdate for robust user creation/updating
      const user = await this.createOrUpdateUser(userData, existingUser);

      // Initialize usage tracking for new users
      if (isNewUser) {
        try {
          const usageTrackingService = require('../services/usageTrackingService');
          await usageTrackingService.initializeNewUser(user.email, user.email, 0);
          console.log(`[AUTH] New user ${user.email} initialized`);
        } catch (err) {
          console.warn('Failed to initialize usage tracking:', err.message || err);
        }
      }

      // Idempotently ensure both Service Accounts (see signup path).
      try {
        await ensurePersonalSA(user);
      } catch (err) {
        console.warn('[AUTH] ensurePersonalSA failed (continuing):', err.message || err);
      }
      try {
        await ensureWorkSA(user);
      } catch (err) {
        console.warn('[AUTH] ensureWorkSA failed (continuing):', err.message || err);
      }

      // Generate JWT token
      const token = await this.generateJwtToken(user);

      return {
        user: user.toJSON(),
        token,
        tokenType: 'Bearer',
        expiresIn: envConfig.jwtExpiresIn || '7d',
        isNewUser,
        accessStatus: { hasAccess: true, user_type: user.user_type || 'paid' }
      };

    } catch (error) {
      if (error.statusCode === 403 && error.accessStatus) {
        throw error;
      }
      throw new Error(`Google authentication (access token) failed: ${error.message}`);
    }
  }

  /**
   * Refresh user data from Google
   * @param {string} userId - User MongoDB _id (not user_id which is now email)
   * @param {string} idToken - New Google ID token
   * @returns {Promise<Object>} - Updated user data
   */
  async refreshUserData(userId, idToken) {
    try {
      // Verify Google ID token
      const payload = await this.verifyIdToken(idToken);

      // Find user by MongoDB _id
      const user = await CitraAIUser.findById(userId);
      if (!user) {
        throw new Error('User not found');
      }

      // Update user data
      user.name = payload.name || user.name;
      user.profilePicture = payload.picture || user.profilePicture;
      user.lastLogin = new Date();

      await user.save();

      return user.toJSON();

    } catch (error) {
      throw new Error(`Failed to refresh user data: ${error.message}`);
    }
  }

  /**
   * Get user by JWT token
   * @param {string} token - JWT token
   * @returns {Promise<Object>} - User data
   */
  async getUserByToken(token) {
    try {
      const decoded = await this.verifyJwtToken(token);
      // Use user_id (email) to find user
      const user = await CitraAIUser.findOne({ email: decoded.user_id });

      if (!user) {
        // User has been deleted from database - force re-authentication
        throw new Error('User account not found. Please re-authenticate with Google.');
      }

      // Update last login time when token is used
      user.lastLogin = new Date();
      await user.save();

      return user.toJSON();

    } catch (error) {
      // If JWT is invalid or user not found, both require re-authentication
      throw new Error(`Authentication required: ${error.message}`);
    }
  }

  /**
   * Logout user (invalidate token - in a real app, you'd maintain a blacklist)
   * @param {string} userId - User MongoDB _id (not user_id which is now email)
   * @returns {Promise<Object>} - Success message
   */
  async logout(userId) {
    try {
      // In a production app, you might want to blacklist the token
      // or store logout timestamp to validate against
      const user = await CitraAIUser.findById(userId);
      if (user) {
        // Could update last logout time if needed
        // user.lastLogout = new Date();
        // await user.save();
      }

      return { message: 'Logged out successfully' };

    } catch (error) {
      throw new Error(`Logout failed: ${error.message}`);
    }
  }

  /**
   * Enhanced user creation/update with comprehensive validation
   * @param {Object} userData - User data to create or update
   * @param {Object} existingUser - Existing user document if found
   * @returns {Promise<Object>} - Created or updated user
   */
  async createOrUpdateUser(userData, existingUser = null) {
    try {
      if (existingUser) {
        console.log('Updating existing user:', existingUser._id);

        // If existing user was local-only and now signing in with Google, upgrade to 'both'
        if (existingUser.authProvider === 'local' && userData.authProvider === 'google') {
          userData.authProvider = 'both';
          userData.emailVerified = true;
        } else if (existingUser.authProvider === 'both') {
          userData.authProvider = 'both'; // preserve 'both' status
        }

        // Update existing user with new data, preserving important existing fields
        const updateData = {
          ...userData,
          // Preserve existing plan and usage data
          plan_type: existingUser.plan_type || userData.plan_type || 'free',
          usage: existingUser.usage || userData.usage || {},
          usage_reset_date: existingUser.usage_reset_date || userData.usage_reset_date || new Date(),
          // Update timestamps
          lastLogin: new Date(),
          updatedAt: new Date()
        };

        // Use Object.assign to update the existing document
        Object.assign(existingUser, updateData);
        const savedUser = await existingUser.save();

        console.log('User updated successfully:', savedUser._id);
        return savedUser;
      } else {
        console.log('Creating new user with email:', userData.email);

        // Create new user with proper defaults
        const newUserData = {
          ...userData,
          plan_type: userData.plan_type || 'free',
          usage: userData.usage || {},
          usage_reset_date: userData.usage_reset_date || new Date(),
          isActive: true,
          createdAt: new Date(),
          updatedAt: new Date(),
          lastLogin: new Date()
        };

        const newUser = await CitraAIUser.create(newUserData);
        console.log('New user created successfully:', newUser._id);
        return newUser;
      }
    } catch (error) {
      // Handle duplicate key errors
      if (error.code === 11000) {
        console.log('Duplicate key error, attempting to find and update existing user');
        const field = Object.keys(error.keyValue)[0];
        const value = error.keyValue[field];

        const existingUser = await CitraAIUser.findOne({ [field]: value });
        if (existingUser) {
          return await this.createOrUpdateUser(userData, existingUser);
        }
      }

      console.error('Error in createOrUpdateUser:', error);
      throw new Error(`User creation/update failed: ${error.message}`);
    }
  }

  /**
   * Send welcome email to new user
   * @param {Object} user - User object from database
   * @param {string} profession - User's selected profession
   * @returns {Promise<void>}
   */
  async sendWelcomeEmail(user, profession = 'General Professional') {
    try {
      const sendEmail = require('../shared/sendEmail');
      const { getWelcomeEmailTemplate } = require('../shared/emailTemplates');

      // Generate welcome email content with profession-specific template
      const emailTemplate = getWelcomeEmailTemplate(user, profession);

      // Send welcome email
      const emailOptions = {
        to: user.email,
        subject: emailTemplate.subject,
        text: emailTemplate.text,
        html: emailTemplate.html
      };

      await sendEmail(emailOptions);
      console.log(`✅ Welcome email sent successfully to: ${user.email} (Profession: ${profession})`);

    } catch (error) {
      // Log error but don't re-throw to prevent blocking registration
      console.error(`❌ Welcome email failed for ${user.email}:`, {
        error: error.message || error,
        code: error.code,
        statusCode: error.statusCode
      });
      // Note: Error is logged but not thrown - email failure should not prevent user registration
    }
  }
}

module.exports = new GoogleAuthService();
