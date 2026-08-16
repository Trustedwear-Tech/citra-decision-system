// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * DeletionRequest — admin-driven user-deletion preflight + apply state.
 *
 * Flow:
 *   1. dept_admin / org_admin opens "Delete user X" → POST /preflight-delete
 *      creates a DeletionRequest in `draft` state and enumerates the
 *      target user's resources where they're sole admin / owner-equivalent.
 *   2. Admin opens the picker UI, chooses an action per resource.
 *      The UI re-submits the picker via POST /delete (status → `submitted`).
 *   3. server enqueues `user.delete_applied` to Citra-Worker, which
 *      applies each chosen action atomically, writes a HandoffReport,
 *      and bumps the DeletionRequest to `applied`.
 *   4. After Worker completes, the user document is hard-deleted (or
 *      soft-marked with deletion_state='deleted' depending on env).
 *
 * Audit: every DeletionRequest survives the user's deletion so admins
 * can look back at "who deleted alice, when, and what happened to her
 * stuff" via the /admin/departures screen.
 */
const mongoose = require('mongoose');

const resourceActionSchema = new mongoose.Schema(
  {
    kind: { type: String, enum: ['workflow', 'smart_app'], required: true },
    resource_id: { type: String, required: true },
    name: { type: String, default: '' },
    current_sa: { type: String, default: null },
    sole_admin: { type: Boolean, default: false },
    // Action picked by the admin during the picker UX
    action: {
      type: String,
      enum: ['keep', 'transfer_to_sa', 'transfer_to_dept', 'make_me_admin', 'archive', 'delete'],
      default: 'keep',
    },
    // Target for transfer actions:
    //   transfer_to_sa  → service_account_id
    //   transfer_to_dept → dept_id
    action_target: { type: String, default: null },
    applied_at: { type: Date, default: null },
    applied_error: { type: String, default: null },
  },
  { _id: false }
);

const deletionRequestSchema = new mongoose.Schema(
  {
    request_id: { type: String, unique: true, required: true, index: true },

    // Target
    target_user_id: { type: String, required: true, index: true },
    target_email: { type: String, required: true, index: true },
    target_personal_sa_id: { type: String, default: null },

    // Initiator
    requested_by: { type: String, required: true, index: true },
    requested_by_role: {
      type: String,
      enum: ['dept_admin', 'org_admin', 'super_admin'],
      required: true,
    },
    org_id: { type: String, required: true, index: true },
    dept_ids: { type: [String], default: [] },

    // State machine
    status: {
      type: String,
      enum: ['draft', 'submitted', 'applied', 'cancelled', 'failed'],
      default: 'draft',
      index: true,
    },
    resources: { type: [resourceActionSchema], default: [] },

    // ── Phase H: personal content (presentations, notes, etc.) ──
    // Bulk policy per content bucket; missing buckets default to 'keep'.
    // Set from the picker UI and forwarded to Citra-Service's
    // /api/v2/admin/user-content-apply at delete time.
    personal_content_policies: {
      type: Map,
      of: String, // 'keep' | 'transfer_to_admin' | 'delete'
      default: {},
    },
    personal_content_summary: {
      type: mongoose.Schema.Types.Mixed,
      default: null, // snapshot of counts at preflight time (audit)
    },

    // Outcome
    applied_at: { type: Date, default: null },
    applied_handoff_report_id: { type: String, default: null },
    failure_reason: { type: String, default: null },

    created_at: { type: Date, default: Date.now },
    updated_at: { type: Date, default: Date.now },
  },
  {
    collection: 'DeletionRequests',
    timestamps: { createdAt: 'created_at', updatedAt: 'updated_at' },
  }
);

deletionRequestSchema.index({ target_email: 1, status: 1 });
deletionRequestSchema.index({ org_id: 1, status: 1, created_at: -1 });

module.exports = mongoose.model('DeletionRequest', deletionRequestSchema);
