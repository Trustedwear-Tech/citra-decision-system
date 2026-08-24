// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * ImpersonationAudit — record of every super_admin "login as user" action.
 *
 * Written when POST /api/admin/users/:userId/impersonation-token mints a
 * token. The audit row is the source of truth for "who impersonated whom,
 * when, and why" — the minted JWT itself is short-lived and gone in an
 * hour, but this row persists for compliance review.
 *
 * `revoked_at` is set by the revoke endpoint, which ALSO adds the token's
 * `jti` to the `revoked_tokens` blocklist — so revoke is now a real
 * mid-flight kill-switch (every auth path rejects the jti), not just an
 * audit flag. `jti` is the minted token's id, stored so revoke can target
 * this exact token.
 */

const mongoose = require('mongoose');

const ImpersonationAuditSchema = new mongoose.Schema(
  {
    impersonation_id:  { type: String, required: true, unique: true, index: true },
    jti:               { type: String, default: null, index: true },
    admin_email:       { type: String, required: true, index: true },
    admin_user_id:     { type: String, default: null },
    target_email:      { type: String, required: true, index: true },
    target_org_id:     { type: String, default: null },
    target_dept_ids:   { type: [String], default: [] },
    target_roles:      { type: [String], default: [] },
    issued_at:         { type: Date, required: true },
    expires_at:        { type: Date, required: true },
    revoked_at:        { type: Date, default: null },
    ip:                { type: String, default: null },
    user_agent:        { type: String, default: null },
    reason:            { type: String, default: '' },
  },
  {
    collection: 'impersonation_audit',
    timestamps: true,
  }
);

ImpersonationAuditSchema.index({ issued_at: -1 });

module.exports = mongoose.model('ImpersonationAudit', ImpersonationAuditSchema);
