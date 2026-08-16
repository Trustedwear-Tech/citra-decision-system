// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const RevokedToken = require('../models/RevokedToken');

/**
 * revocationService — revoke a token by `jti` and check the blocklist.
 *
 * Backed by the `revoked_tokens` collection, fronted by a small in-process TTL
 * cache so the auth hot-path does at most one Mongo read per jti per cache
 * window (not one per request). "Revoked" is cached long (it never un-revokes);
 * "not revoked" is cached briefly so a fresh revoke takes effect quickly.
 */
const _cache = new Map(); // jti -> { revoked: boolean, until: epochMs }
const NEG_TTL_MS = 30 * 1000; // cache a "not revoked" answer for 30s
const POS_TTL_MS = 60 * 60 * 1000; // cache a "revoked" answer for 1h

function _now() {
  return Date.now();
}

/**
 * Add a token's jti to the blocklist.
 * @param {string} jti
 * @param {number} [expEpochSeconds] token's exp claim (seconds). Drives TTL
 *   purge; defaults to 24h from now when unknown.
 * @param {{reason?: string, revokedBy?: string}} [opts]
 */
async function revoke(jti, expEpochSeconds, { reason = '', revokedBy = null } = {}) {
  if (!jti) return;
  const expMs = expEpochSeconds ? expEpochSeconds * 1000 : _now() + 24 * 60 * 60 * 1000;
  await RevokedToken.updateOne(
    { jti },
    {
      $setOnInsert: {
        jti,
        reason,
        revoked_by: revokedBy,
        expires_at: new Date(expMs),
        revoked_at: new Date(),
      },
    },
    { upsert: true }
  );
  _cache.set(jti, { revoked: true, until: _now() + POS_TTL_MS });
}

/**
 * Is this jti revoked? Cache-fronted.
 * @param {string} jti
 * @returns {Promise<boolean>}
 */
async function isRevoked(jti) {
  if (!jti) return false;
  const cached = _cache.get(jti);
  if (cached && cached.until > _now()) return cached.revoked;
  const doc = await RevokedToken.findOne({ jti }).select({ _id: 1 }).lean();
  const revoked = !!doc;
  _cache.set(jti, { revoked, until: _now() + (revoked ? POS_TTL_MS : NEG_TTL_MS) });
  return revoked;
}

/** Test/ops helper — drop the in-process cache. */
function _clearCache() {
  _cache.clear();
}

module.exports = { revoke, isRevoked, _clearCache };
