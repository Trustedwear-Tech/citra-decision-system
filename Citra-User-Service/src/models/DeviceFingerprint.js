// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');

/**
 * DeviceFingerprint Model
 * =======================
 * Maps device/browser fingerprints to user emails.
 * Used to detect when multiple Google accounts are being created
 * from the same device to abuse welcome bonus credits.
 * 
 * Each fingerprint record tracks all user emails that have logged in
 * from that device, along with supplementary signals (IP, user-agent).
 */
const deviceFingerprintSchema = new mongoose.Schema({
  // The browser/device fingerprint string (from FingerprintJS or fallback UUID)
  fingerprint: {
    type: String,
    required: true,
    unique: true,
    index: true,
  },

  // All user emails that have authenticated from this device
  user_emails: [{
    type: String,
  }],

  // Supplementary: IP addresses seen from this device
  ip_addresses: [{
    type: String,
  }],

  // Supplementary: User-Agent strings seen from this device
  user_agents: [{
    type: String,
  }],

  // When this fingerprint was first seen
  first_seen: {
    type: Date,
    default: Date.now,
  },

  // When this fingerprint was last seen
  last_seen: {
    type: Date,
    default: Date.now,
  },

  // Total login count from this device
  login_count: {
    type: Number,
    default: 0,
  },

  // Whether this device has been flagged for abuse
  flagged: {
    type: Boolean,
    default: false,
  },

  // Optional admin notes
  notes: {
    type: String,
    default: '',
  },
}, {
  collection: 'device_fingerprints',
  timestamps: true,
});

// Compound indexes
deviceFingerprintSchema.index({ user_emails: 1 });
deviceFingerprintSchema.index({ flagged: 1, last_seen: -1 });

/**
 * Record a login from a device.
 * Upserts the fingerprint record, adds the email if not already present,
 * and updates supplementary signals.
 * 
 * @param {string} fingerprint - Device fingerprint string
 * @param {string} email - User email
 * @param {string} ipAddress - IP address (optional)
 * @param {string} userAgent - User-Agent string (optional)
 * @returns {Promise<Object>} The updated fingerprint record
 */
deviceFingerprintSchema.statics.recordLogin = async function (fingerprint, email, ipAddress = null, userAgent = null) {
  if (!fingerprint || !email) {
    console.warn('[DEVICE_FP] Missing fingerprint or email for recordLogin');
    return null;
  }

  const updateOps = {
    $addToSet: { user_emails: email },
    $set: { last_seen: new Date() },
    $inc: { login_count: 1 },
    $setOnInsert: { first_seen: new Date() },
  };

  // Only add IP/UA if provided and not already tracked (limit stored entries to prevent bloat)
  if (ipAddress) {
    updateOps.$addToSet.ip_addresses = ipAddress;
  }
  if (userAgent) {
    // Store truncated user-agent to save space
    const truncatedUA = userAgent.substring(0, 256);
    updateOps.$addToSet.user_agents = truncatedUA;
  }

  try {
    const record = await this.findOneAndUpdate(
      { fingerprint },
      updateOps,
      { upsert: true, new: true, setDefaultsOnInsert: true }
    );

    // Auto-flag if 2+ unique emails from same device
    if (record.user_emails.length >= 2 && !record.flagged) {
      record.flagged = true;
      record.notes = `Auto-flagged: ${record.user_emails.length} accounts from same device`;
      await record.save();
      console.warn(`🚩 [DEVICE_FP] Device ${fingerprint.substring(0, 8)}... auto-flagged with ${record.user_emails.length} accounts`);
    }

    return record;
  } catch (err) {
    console.error('[DEVICE_FP] Error recording login:', err.message);
    return null;
  }
};

/**
 * Check if a device fingerprint is already associated with any user.
 * 
 * @param {string} fingerprint - Device fingerprint string
 * @returns {Promise<{known: boolean, existingEmails: string[], flagged: boolean}>}
 */
deviceFingerprintSchema.statics.isKnownDevice = async function (fingerprint) {
  if (!fingerprint) {
    return { known: false, existingEmails: [], flagged: false };
  }

  try {
    const record = await this.findOne({ fingerprint });
    if (!record || record.user_emails.length === 0) {
      return { known: false, existingEmails: [], flagged: false };
    }

    return {
      known: true,
      existingEmails: record.user_emails,
      accountCount: record.user_emails.length,
      flagged: record.flagged || false,
      loginCount: record.login_count || 0,
    };
  } catch (err) {
    console.error('[DEVICE_FP] Error checking device:', err.message);
    return { known: false, existingEmails: [], flagged: false };
  }
};

/**
 * Get all fingerprints associated with a user email.
 * 
 * @param {string} email - User email
 * @returns {Promise<Array>} Array of fingerprint records
 */
deviceFingerprintSchema.statics.getFingerprintsForUser = async function (email) {
  try {
    return await this.find({ user_emails: email }).sort({ last_seen: -1 });
  } catch (err) {
    console.error('[DEVICE_FP] Error getting fingerprints for user:', err.message);
    return [];
  }
};

/**
 * Count distinct new-user signups from a given IP within a time window.
 * Used to detect IP-based signup farms.
 *
 * @param {string} ipAddress - IP address to check
 * @param {number} windowHours - Lookback window in hours (default 24)
 * @returns {Promise<number>} Number of distinct emails that signed up from this IP
 */
deviceFingerprintSchema.statics.recentSignupsFromIP = async function (ipAddress, windowHours = 24) {
  if (!ipAddress) return 0;
  try {
    const since = new Date(Date.now() - windowHours * 60 * 60 * 1000);
    const records = await this.find({
      ip_addresses: ipAddress,
      first_seen: { $gte: since },
    });
    const emails = new Set();
    for (const r of records) {
      for (const e of r.user_emails) emails.add(e);
    }
    return emails.size;
  } catch (err) {
    console.error('[DEVICE_FP] Error checking IP signups:', err.message);
    return 0;
  }
};

const DeviceFingerprint = mongoose.model('DeviceFingerprint', deviceFingerprintSchema);

module.exports = DeviceFingerprint;
