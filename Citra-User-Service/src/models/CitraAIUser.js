// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');
const { ROLES, ALL_ROLES, ALL_ENTITY_TYPES } = require('../constants/roles');

// Schema for Citra AI User
const citraAIUserSchema = new mongoose.Schema({
  email: {
    type: String,
    unique: true,
    required: true
  }, // Email (unique)
  // Usage snapshot for plan enforcement (pages, audio bytes, video bytes, image bytes, queries, etc.)
  usage: {
    type: mongoose.Schema.Types.Mixed,
    default: {}
  },
  // User type for credit-based system
  user_type: {
    type: String,
    enum: ['free', 'paid'],
    default: 'free'
  },
  name: {
    type: String,
    required: false
  },
  mobile: {
    type: String,
    required: false
  },
  googleId: {
    type: String,
    unique: true,
    required: false,
    sparse: true // Allows multiple documents with null/undefined values
  }, // Google User ID
  // Authentication provider: 'google', 'local', or 'both'
  authProvider: {
    type: String,
    enum: ['google', 'local', 'both'],
    default: 'google'
  },
  // Password hash (bcrypt) — only for local auth
  passwordHash: {
    type: String,
    required: false,
    select: false // Never returned in queries by default
  },
  // Email verification
  emailVerified: {
    type: Boolean,
    default: false
  },
  emailVerificationToken: {
    type: String,
    required: false,
    select: false
  },
  emailVerificationExpiry: {
    type: Date,
    required: false
  },
  // Password reset
  passwordResetToken: {
    type: String,
    required: false,
    select: false
  },
  passwordResetExpiry: {
    type: Date,
    required: false
  },
  profilePicture: {
    type: String,
    required: false
  },
  // Device fingerprints that have been seen for this user (for abuse detection)
  device_fingerprints: [{
    type: String,
  }],
  // Referral system
  referral_code: {
    type: String,
    unique: true,
    sparse: true,
    uppercase: true,
    trim: true,
    index: true,
  },
  referred_by: {
    type: String,   // email of the referrer
    default: null,
  },
  referral_count: {
    type: Number,
    default: 0,
  },
  // Organisation / department hierarchy (on-prem enterprise)
  org_id: {
    type: String,
    required: false,
    default: null,
    index: true,
  },
  // Dept membership: a user may belong to one or more departments.
  // The singular ``dept_id`` field was removed in the canonical-shape
  // migration; readers must use ``dept_ids[0]`` if they need a primary.
  dept_ids: {
    type: [String],
    default: [],
    index: true,
  },
  // Geographic axis for state-topology deployments (district collector etc.)
  district_ids: {
    type: [String],
    default: [],
  },
  // Entity topology type — controls which multi-tenant features are available
  entity_type: {
    type: String,
    enum: ALL_ENTITY_TYPES,
    default: 'general',
  },
  // Role enum + default sourced from the shared constants (src/constants/roles.js)
  // — the single authoritative list of role names. Add a role there to extend.
  roles: {
    type: [String],
    enum: ALL_ROLES,
    default: [ROLES.USER],
  },
  // GDPR consent tracking
  gdpr_consent: {
    terms_accepted_at: { type: Date, default: null },
    privacy_accepted_at: { type: Date, default: null },
    consent_version: { type: String, default: '1.0' },
  },
  isActive: {
    type: Boolean,
    default: false // Set to false until coupon or subscription is applied
  },
  // ── Deletion lifecycle (SA-only ownership) ──
  // 'active'           — normal
  // 'pending_deletion' — admin opened a delete request; user can still
  //                       authenticate (handoff in flight)
  // 'deleted'          — handoff applied; user doc soft-marked, sessions
  //                       revoked. Hard-delete happens after handoff.
  deletion_state: {
    type: String,
    enum: ['active', 'pending_deletion', 'deleted'],
    default: 'active',
    index: true,
  },
  deleted_at: { type: Date, default: null },
  deleted_by: { type: String, default: null },
  deletion_handoff_report_id: { type: String, default: null },
  // ── Personal Service Account ──
  // Auto-created on first resource-create call. Format:
  //   svc:personal-<userid>@<org>.citra.ai
  // Used when the user creates a workflow / smart-app without picking
  // a shared SA. Single-admin (themselves), gets handled by inheritance
  // policy when they leave.
  personal_sa_id: { type: String, default: null, index: true },
  // Work SA — separate from personal because it owns durable team-portable
  // resources (skills, smart-apps, workflows). When a user is deleted, the
  // admin can re-home the work SA to another user or to the dept; the
  // personal SA goes away with the user.
  work_sa_id: { type: String, default: null, index: true },
  lastLogin: {
    type: Date,
    default: Date.now
  },
  createdAt: {
    type: Date,
    default: Date.now
  },
  updatedAt: {
    type: Date,
    default: Date.now
  }
}, {
  collection: 'users', // Explicit collection name
  timestamps: true // Automatically manage createdAt and updatedAt
});

// Pre-save middleware to update the updatedAt field
citraAIUserSchema.pre('save', function (next) {
  this.updatedAt = Date.now();
  next();
});

// Instance methods
citraAIUserSchema.methods.toJSON = function () {
  const user = this.toObject();
  delete user.__v;
  delete user.passwordHash;
  delete user.emailVerificationToken;
  delete user.passwordResetToken;
  return user;
};

citraAIUserSchema.methods.updateLastLogin = function () {
  this.lastLogin = Date.now();
  return this.save();
};

// Static methods
citraAIUserSchema.statics.findByDeviceId = function (deviceId) {
  return this.findOne({ email: deviceId });
};

citraAIUserSchema.statics.findByGoogleId = function (googleId) {
  return this.findOne({ googleId: googleId });
};

citraAIUserSchema.statics.findByEmailWithPassword = function (email) {
  return this.findOne({ email }).select('+passwordHash');
};

citraAIUserSchema.statics.createOrUpdate = async function (userData) {
  try {
    // First, try to find user by email (primary identifier)
    let user = await this.findOne({ email: userData.email });

    // If not found by email and googleId is provided, try finding by googleId
    if (!user && userData.googleId) {
      user = await this.findOne({ googleId: userData.googleId });

      // If found by googleId but email is different, update the email
      if (user && user.email !== userData.email) {
        console.log(`Updating user email from ${user.email} to ${userData.email}`);
      }
    }

    if (user) {
      // Update existing user, preserving important existing data
      const updateData = {
        ...userData,
        // Preserve existing usage data if not provided in userData
        usage: userData.usage || user.usage || {},
        // Always update lastLogin and updatedAt
        lastLogin: new Date(),
        updatedAt: new Date(),
        // Preserve isActive status unless explicitly provided
        isActive: userData.hasOwnProperty('isActive') ? userData.isActive : user.isActive
      };

      // Update the user document
      Object.assign(user, updateData);
      await user.save();

      console.log('User updated successfully:', user._id);
      return user;
    } else {
      // Create new user with proper defaults
      const newUserData = {
        ...userData,
        usage: userData.usage || {},
        isActive: userData.hasOwnProperty('isActive') ? userData.isActive : true,
        lastLogin: new Date(),
        createdAt: new Date(),
        updatedAt: new Date()
      };

      const newUser = await this.create(newUserData);
      console.log('New user created successfully:', newUser._id);
      return newUser;
    }
  } catch (error) {
    // Handle duplicate key errors gracefully
    if (error.code === 11000) {
      console.log('Duplicate key error detected, attempting resolution...');

      // Extract the field that caused the duplicate
      const field = Object.keys(error.keyValue)[0];
      const value = error.keyValue[field];

      console.log(`Duplicate detected for ${field}: ${value}`);

      // Try to find the existing document and update it
      const existingUser = await this.findOne({ [field]: value });
      if (existingUser) {
        console.log('Found existing user, updating instead of creating');

        // Update the existing user
        const updateData = {
          ...userData,
          usage: userData.usage || existingUser.usage || {},
          lastLogin: new Date(),
          updatedAt: new Date(),
          isActive: userData.hasOwnProperty('isActive') ? userData.isActive : existingUser.isActive
        };

        Object.assign(existingUser, updateData);
        await existingUser.save();

        console.log('Duplicate resolved by updating existing user:', existingUser._id);
        return existingUser;
      }
    }

    console.error('Error in createOrUpdate:', error);
    throw new Error(`User creation/update failed: ${error.message}`);
  }
};

// Indexes
citraAIUserSchema.index({ createdAt: -1 });

const CitraAIUser = mongoose.model('CitraAIUser', citraAIUserSchema);

module.exports = CitraAIUser;
