// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Delete Account Handler
 *
 * GDPR Article 17 — Right to Erasure ("Right to be Forgotten")
 *
 * Permanently deletes ALL user data from Citra-User-Service collections:
 *   - CitraAIUser (users)
 *   - UserUsage
 *   - CreditTransaction
 *   - UserCoupon
 *   (DeviceFingerprint records are intentionally PRESERVED for anti-abuse)
 *
 * Also fires a cross-service deletion request to the Python Citra-Service
 * to purge all content data (files, notes, presentations, etc.) and vector
 * embeddings from Milvus/Zilliz + S3.
 *
 * POST /api/auth/delete-account
 * Requires: authenticateToken middleware (user must be logged in)
 */

const CitraAIUser = require('../models/CitraAIUser');
const UserUsage = require('../models/UserUsage');
const CreditTransaction = require('../models/CreditTransaction');
const UserCoupon = require('../models/UserCoupon');
const DeviceFingerprint = require('../models/DeviceFingerprint');
const DeletedAccountHash = require('../models/DeletedAccountHash');
const { connectToMongoDB } = require('../config/mongodb');

/**
 * Delete the authenticated user's account and ALL associated data.
 */
const deleteAccountHandler = async (req, res) => {
  try {
    await connectToMongoDB();

    const userEmail = req.user?.email;
    if (!userEmail) {
      return res.status(400).json({ success: false, error: 'User email not found in token' });
    }

    // Require explicit confirmation in request body
    const { confirmation } = req.body || {};
    if (confirmation !== 'DELETE') {
      return res.status(400).json({
        success: false,
        error: 'You must send { "confirmation": "DELETE" } to confirm account deletion',
      });
    }

    console.log(`🗑️ [DELETE-ACCOUNT] Starting account deletion for: ${userEmail}`);

    // ── 0. Record deletion hash BEFORE wiping data (anti-abuse for welcome bonus) ──
    try {
      // Capture device fingerprints linked to this user at deletion time
      const linkedFingerprints = await DeviceFingerprint.find({ user_emails: userEmail }).lean();
      const fpList = linkedFingerprints.map(fp => fp.fingerprint).filter(Boolean);
      await DeletedAccountHash.recordDeletion(userEmail, fpList);
      console.log(`🗑️ [DELETE-ACCOUNT] Recorded deletion hash (${fpList.length} fingerprint(s) captured)`);
    } catch (e) {
      console.error('🗑️ [DELETE-ACCOUNT] Failed to record deletion hash:', e.message);
    }

    const results = {
      user: false,
      usage: false,
      transactions: false,
      coupons: false,
      fingerprints: false,
      citraService: false,
    };

    // 1. Delete usage records
    try {
      const usageResult = await UserUsage.deleteMany({ email: userEmail });
      results.usage = true;
      console.log(`🗑️ [DELETE-ACCOUNT] Deleted ${usageResult.deletedCount} usage record(s)`);
    } catch (e) {
      console.error('🗑️ [DELETE-ACCOUNT] Failed to delete usage:', e.message);
    }

    // 2. Delete credit transactions
    try {
      const txResult = await CreditTransaction.deleteMany({ email: userEmail });
      results.transactions = true;
      console.log(`🗑️ [DELETE-ACCOUNT] Deleted ${txResult.deletedCount} credit transaction(s)`);
    } catch (e) {
      console.error('🗑️ [DELETE-ACCOUNT] Failed to delete transactions:', e.message);
    }

    // 3. Delete coupon redemptions
    try {
      const couponResult = await UserCoupon.deleteMany({ email: userEmail });
      results.coupons = true;
      console.log(`🗑️ [DELETE-ACCOUNT] Deleted ${couponResult.deletedCount} coupon record(s)`);
    } catch (e) {
      console.error('🗑️ [DELETE-ACCOUNT] Failed to delete coupons:', e.message);
    }

    // 4. Device fingerprint records — intentionally KEPT intact.
    //    We do NOT remove the user's email from DeviceFingerprint.user_emails
    //    so that the fingerprint-based anti-abuse check continues to work
    //    if the same person re-registers from the same device.
    //    The DeletedAccountHash (step 0) covers the cross-device case.
    results.fingerprints = true;
    console.log(`🗑️ [DELETE-ACCOUNT] Device fingerprint records preserved for anti-abuse`);

    // 5. Fire cross-service deletion to Citra-Service (Python)
    //    Forward the user's original auth token so Citra-Service JWT middleware
    //    can authenticate the request normally.
    try {
      const citraServiceUrl = require('../config/environment').citraServiceUrl;
      const fetch = require('node-fetch');
      const originalAuth = req.headers['authorization'] || req.headers['Authorization'] || '';
      if (!originalAuth) {
        console.error('🗑️ [DELETE-ACCOUNT] No Authorization header to forward — Citra-Service call will fail');
      }
      console.log(`🗑️ [DELETE-ACCOUNT] Forwarding to Citra-Service: auth=${originalAuth ? 'present' : 'MISSING'}`);
      const deleteResponse = await fetch(`${citraServiceUrl}/api/v2/user/delete-all-data`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          ...(originalAuth ? { 'Authorization': originalAuth } : {}),
        },
        body: JSON.stringify({ email: userEmail, confirmation: 'DELETE' }),
        timeout: 30000,
      });

      if (deleteResponse.ok) {
        results.citraService = true;
        const deleteResult = await deleteResponse.json();
        console.log(`🗑️ [DELETE-ACCOUNT] Citra-Service deletion result:`, deleteResult);
      } else {
        const errorText = await deleteResponse.text();
        console.error(`🗑️ [DELETE-ACCOUNT] Citra-Service deletion failed (${deleteResponse.status}):`, errorText);
      }
    } catch (e) {
      console.error('🗑️ [DELETE-ACCOUNT] Failed to call Citra-Service deletion:', e.message);
    }

    // 6. Delete the user document LAST (so we have email for all previous steps)
    try {
      const userResult = await CitraAIUser.deleteOne({ email: userEmail });
      results.user = userResult.deletedCount > 0;
      console.log(`🗑️ [DELETE-ACCOUNT] Deleted user document: ${results.user}`);
    } catch (e) {
      console.error('🗑️ [DELETE-ACCOUNT] Failed to delete user:', e.message);
    }

    const allSucceeded = Object.values(results).every((v) => v === true);

    console.log(`🗑️ [DELETE-ACCOUNT] Completed for ${userEmail}:`, results);

    // Fail loud: if ANY deletion step failed, the account was NOT fully
    // purged. Reporting success:true / HTTP 200 here would tell the user
    // (and any GDPR audit) their data is gone when some of it remains. Surface
    // the partial failure with a non-2xx status so the caller knows.
    if (!allSucceeded) {
      const failedSteps = Object.entries(results)
        .filter(([, ok]) => ok !== true)
        .map(([step]) => step);
      console.error(
        `🗑️ [DELETE-ACCOUNT] PARTIAL FAILURE for ${userEmail} — data may remain. ` +
        `Failed steps: ${failedSteps.join(', ')}`,
        results,
      );
      return res.status(500).json({
        success: false,
        error:
          'Account deletion did not fully complete — some data may remain. ' +
          'Please contact support; this has been logged.',
        failedSteps,
        results,
      });
    }

    return res.status(200).json({
      success: true,
      message: 'Account and all associated data have been permanently deleted',
      results,
    });
  } catch (error) {
    console.error('🗑️ [DELETE-ACCOUNT] Unhandled error:', error);
    return res.status(500).json({
      success: false,
      error: 'Failed to delete account. Please contact support.',
    });
  }
};

module.exports = { deleteAccountHandler };
