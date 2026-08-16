// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const mongoose = require('mongoose');

/**
 * RevokedToken — the JWT revocation blocklist.
 *
 * The platform's only runtime kill-switch for an already-issued token. A token
 * is identified by its `jti` claim (added by tokenService + citra-auth mint_*).
 * When a session must be cut short of its natural `exp` — impersonation revoke,
 * a demotion, a leaked token — its `jti` is inserted here and every auth path
 * rejects it on the next request.
 *
 * `expires_at` carries the token's OWN exp; the TTL index deletes the row once
 * the token would have expired anyway, so the blocklist only ever holds
 * still-valid revoked tokens (stays small, self-purging — no cron).
 */
const revokedTokenSchema = new mongoose.Schema(
  {
    jti: { type: String, required: true, unique: true, index: true },
    reason: { type: String, default: '' },
    revoked_by: { type: String, default: null },
    // Token's own exp. TTL index removes the row at this time.
    expires_at: { type: Date, required: true, index: { expires: 0 } },
    revoked_at: { type: Date, default: Date.now },
  },
  { collection: 'revoked_tokens' }
);

module.exports = mongoose.model('RevokedToken', revokedTokenSchema);
