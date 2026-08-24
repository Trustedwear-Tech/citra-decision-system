// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * ServiceAccount — a named group with explicit membership that owns Citra
 * resources (workflows, smart apps, connections, etc.).
 *
 * Mental model: a peer of dept/org, not a sub-type of user.
 *
 *   org_id / dept_id   = passive demographic attributes on a user
 *   service_account    = curated team with admins + members listed by email
 *
 * Resources owned by a service account are visible to:
 *   - admins   (can manage SA + admins/members + rotate keys + edit owned resources)
 *   - members  (can read + run SA-owned resources, but NOT edit them)
 *   - org_admin / super_admin (audit override)
 *
 * The service account has a machine identity — short-lived JWTs are minted
 * with sub = service_account_id so workflows / cron / scheduled jobs run
 * under the SA instead of any human user. This is the answer to:
 *
 *   "what happens to my dept's ingestion workflow if the dept_admin who
 *    authored it leaves the company?"
 *
 *   → reassign to a service account; survives any human turnover.
 */

const mongoose = require('mongoose');

const serviceAccountSchema = new mongoose.Schema({
  // ── Identity ──
  service_account_id: {
    type: String,
    unique: true,
    required: true,
    // svc:<purpose-slug>@<org-id>.citra.ai
    // e.g. svc:plant-ops-ingestion@acme-cement.citra.ai
    validate: {
      validator: (v) => /^svc:[a-z0-9-]+@[a-z0-9-]+\.citra\.ai$/.test(v),
      message: 'service_account_id must match svc:<slug>@<org-id>.citra.ai',
    },
    index: true,
  },
  display_name: {
    type: String,
    required: true,
    minlength: 3,
    maxlength: 120,
  },
  description: {
    type: String,
    maxlength: 500,
  },

  // ── Scope (the SA can act inside this org / on these depts) ──
  org_id: {
    type: String,
    required: true,
    index: true,
  },
  dept_ids: {
    type: [String],
    default: [],
    index: true,
  },

  // ── Membership lists (the heart of the model) ──
  admins: {
    type: [String],  // user emails — can manage SA + manage members + edit owned resources
    default: [],
    validate: {
      validator: (v) => Array.isArray(v) && v.length >= 1,
      message: 'A service account must have at least one admin',
    },
  },
  members: {
    type: [String],  // user emails — can read/run owned resources
    default: [],
  },

  // ── What the SA can DO at runtime ──
  // Citra-Service / smart-app-service / citra-workflow
  // recognize these roles in the minted JWT and gate accordingly.
  roles: {
    type: [String],
    default: ['service:workflow_runner'],
    validate: {
      validator: (v) => v.every((r) => [
        'service:workflow_runner',
        'service:mcp_bridge',
        'service:demo_ingestion',
        'service:platform_admin',
      ].includes(r)),
      message: 'unknown service role',
    },
  },

  // ── Lifecycle ──
  is_active: {
    type: Boolean,
    default: true,
    index: true,
  },
  // True for SAs auto-created by /api/auth/me/personal-sa. Personal SAs
  // have a single admin (the owning user) and get special handling in
  // the admin-delete picker (resources need an explicit reassignment).
  is_personal: {
    type: Boolean,
    default: false,
    index: true,
  },
  created_by_user_id: {
    type: String,
    required: true,
  },
  created_at: {
    type: Date,
    default: Date.now,
  },
  // Last credential rotation. Tokens minted before this point are
  // considered revoked. Citra-Service's auth_middleware checks
  // `sa_minted_at < last_rotated_at` to enforce.
  last_rotated_at: {
    type: Date,
    default: null,
  },
  // Soft-delete marker. Disabled SAs cannot mint new tokens; in-flight
  // tokens expire on their natural TTL (≤ 1 hour by default).
  disabled_at: {
    type: Date,
    default: null,
  },
  disabled_reason: {
    type: String,
    default: null,
  },
}, {
  timestamps: { createdAt: 'created_at', updatedAt: 'updated_at' },
});

// Compound index for the visibility filter:
//   "all SAs this user (by email) belongs to as admin OR member"
serviceAccountSchema.index({ admins: 1, is_active: 1 });
serviceAccountSchema.index({ members: 1, is_active: 1 });
serviceAccountSchema.index({ org_id: 1, is_active: 1 });

// ── Instance helpers ─────────────────────────────────────────────

serviceAccountSchema.methods.isAdmin = function (userEmail) {
  return this.admins.includes(String(userEmail || '').toLowerCase());
};

serviceAccountSchema.methods.isMember = function (userEmail) {
  const email = String(userEmail || '').toLowerCase();
  return this.admins.includes(email) || this.members.includes(email);
};

serviceAccountSchema.methods.canMintToken = function () {
  if (!this.is_active) return false;
  if (this.disabled_at) return false;
  return true;
};

// ── Static helpers ──────────────────────────────────────────────

/**
 * Find all SAs a given user (by email) belongs to as admin OR member.
 * Used by the JWT minter so user JWTs carry a `service_account_member_of`
 * claim listing every SA they can act on resources for.
 */
serviceAccountSchema.statics.findByUserMembership = function (userEmail) {
  const email = String(userEmail || '').toLowerCase();
  return this.find({
    is_active: true,
    $or: [{ admins: email }, { members: email }],
  });
};

/**
 * Find all SAs a user is ADMIN of (can manage). Used for endpoints like
 * "list service accounts I can manage".
 */
serviceAccountSchema.statics.findAdminOf = function (userEmail) {
  const email = String(userEmail || '').toLowerCase();
  return this.find({ is_active: true, admins: email });
};

module.exports = mongoose.model('ServiceAccount', serviceAccountSchema);
