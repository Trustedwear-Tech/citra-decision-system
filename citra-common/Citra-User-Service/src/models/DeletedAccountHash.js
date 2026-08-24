// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

const mongoose = require('mongoose');
const crypto = require('crypto');

/**
 * DeletedAccountHash Model
 * ========================
 * GDPR-compliant abuse prevention: stores only a one-way SHA-256 hash of
 * deleted users' emails — never the plain-text email. This allows us to
 * detect re-registrations by previously-deleted accounts and prevent
 * duplicate welcome bonuses, without retaining any personal data.
 *
 * A SHA-256 hash is irreversible, so this record does NOT constitute
 * personal data under GDPR Article 4(1).
 */
const deletedAccountHashSchema = new mongoose.Schema({
  email_hash: {
    type: String,
    required: true,
    unique: true,
    index: true,
  },
  deleted_at: {
    type: Date,
    default: Date.now,
  },
  // Fingerprints associated with the account at time of deletion (for extra anti-abuse)
  device_fingerprints: [{
    type: String,
  }],
});

/**
 * Hash an email address with SHA-256
 * @param {string} email - plain-text email
 * @returns {string} hex-encoded SHA-256 hash
 */
deletedAccountHashSchema.statics.hashEmail = function (email) {
  return crypto.createHash('sha256').update(email.toLowerCase().trim()).digest('hex');
};

/**
 * Record a deleted account (stores only the hash)
 * @param {string} email - plain-text email to hash and store
 * @param {string[]} fingerprints - device fingerprints linked at deletion time
 */
deletedAccountHashSchema.statics.recordDeletion = async function (email, fingerprints = []) {
  const emailHash = this.hashEmail(email);
  try {
    await this.findOneAndUpdate(
      { email_hash: emailHash },
      {
        email_hash: emailHash,
        deleted_at: new Date(),
        device_fingerprints: fingerprints,
      },
      { upsert: true, new: true }
    );
    console.log(`🔒 [DELETED_HASH] Recorded deletion hash for account`);
  } catch (err) {
    // Duplicate key on concurrent deletion is fine — ignore
    if (err.code !== 11000) throw err;
  }
};

/**
 * Check whether an email belongs to a previously-deleted account
 * @param {string} email - plain-text email to check
 * @returns {Promise<{wasDeleted: boolean, deletedAt: Date|null}>}
 */
deletedAccountHashSchema.statics.wasAccountDeleted = async function (email) {
  const emailHash = this.hashEmail(email);
  const record = await this.findOne({ email_hash: emailHash }).lean();
  return {
    wasDeleted: !!record,
    deletedAt: record?.deleted_at || null,
  };
};

module.exports = mongoose.model('DeletedAccountHash', deletedAccountHashSchema, 'deleted_account_hashes');
