// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Decision API — TypeScript types.
 *
 * These mirror the smart-app-service response models. They are intentionally
 * permissive (extra fields tolerated) so the SDK keeps working as the API adds
 * fields. Where the server returns an open `Record<string, unknown>`, so do we.
 */

/** A JSON Schema object (as used for `inputs` / editable-field option contracts). */
export type JsonSchema = Record<string, unknown>;

// ── Discovery ───────────────────────────────────────────────────────────────
export interface AppSummary {
  app_id: string;
  slug: string;
  title: string;
  [k: string]: unknown;
}
export interface AppListResponse {
  apps: AppSummary[];
  total: number;
}
export interface AppDetailResponse {
  app_spec: Record<string, unknown>;
  agent_spec?: Record<string, unknown> | null;
  [k: string]: unknown;
}

/**
 * Self-describing headless contract (GET /apps/{slug}/decision-contract).
 * The authoritative, per-app answer to "what do I call, with what body, and
 * what are the governance rules" — read this once and drive the app from it.
 */
/**
 * One item of evidence the agent must READ before a write is staged (a /run
 * fails loud otherwise). `kind`:
 *   - `record`  — the anchor record the id names.
 *   - `image_analyze` / `doc_extract` — a bound media artifact.
 *   - `lookup`  — a policy-required data lookup (bureau / KYC / sanctions —
 *                 the CIBIL-style gate); carries `dataset_id`.
 */
export interface RequiredEvidence {
  kind: "record" | "image_analyze" | "doc_extract" | "lookup" | string;
  tool?: string;
  key_field?: string | null;
  dataset_id?: string;
  prefetched?: boolean;
  note?: string;
}

export interface DecisionContract {
  slug: string;
  agent?: string | null;
  headless: boolean;
  /**
   * Per-item review gate: `"hard"` (each non-fraud item_finding must be
   * dispositioned via feedback before /approve — SERVER-ENFORCED: an approve
   * with unreviewed findings returns 409), `"soft"` (warn), or `"none"`.
   * Fraud `case` items are evidence-only and never gate.
   */
  item_review_gate?: "hard" | "soft" | "none";
  /** Values for the `action` field of `recommend()`. */
  run_actions: string[];
  /** Governed SoR writes: table, payload schema, and human-overridable fields. */
  write_actions: Array<{
    name?: string;
    dataset_id?: string;
    action_id?: string;
    source_id?: string;
    input_schema: JsonSchema;
    editable_fields: string[];
  }>;
  /**
   * Evidence the agent MUST read before staging a write — the anchor record,
   * bound-required media, and policy-required lookups (bureau/KYC). A /run FAILS
   * loud rather than stage a decision without it.
   */
  required_evidence?: RequiredEvidence[];
  endpoints: Record<string, { method: string; path: string; note?: string }>;
  /** JSON Schema for the `inputs` you pass to `recommend()`. */
  request_schema: JsonSchema;
  response_shape: Record<string, string>;
  approve_request: Record<string, string>;
  example: {
    recommend_request: { action: string; inputs: Record<string, unknown> };
    approve_request: { decision: string; overrides: unknown[]; note: string };
  };
  auth: string;
  governance: string;
}

// ── Decision loop ────────────────────────────────────────────────────────────
export type RunStatus = "completed" | "pending_approval" | "failed";

/**
 * The modality of a per-item review card:
 *   - `image`    — a photo/scan analysed by an image rubric.
 *   - `document` — an extracted document (invoice, ID, form).
 *   - `api`      — a data-lookup check (e.g. a CIBIL/bureau `check_evaluate`:
 *                  score bands, hit/no-hit, KYC/sanctions match).
 *   - `case`     — a fraud-synthesis card. EVIDENCE ONLY: it highlights signals
 *                  for the officer to weigh and NEVER gates Apply or auto-rejects.
 */
export type ItemModality = "image" | "document" | "api" | "case";

/**
 * A per-item review card the officer (or a headless integrator) disposes of via
 * `submitFeedback()`. One per analysed artifact / lookup / fraud case. For
 * `image`/`document`/`api` items the app's `item_review_gate` may REQUIRE a
 * disposition before `/approve`; `case` (fraud) items never gate.
 */
export interface ItemFinding {
  item_id: string;
  item_type: string;
  modality: ItemModality;
  subject?: string | null;
  /** Render-friendly summary the rubric produced (e.g. {score_band, signals}). */
  fields: Record<string, unknown>;
  recommendation?: string | null;
  confidence: number;
  rationale: string;
  citations?: unknown[];
  rubric_version?: string | null;
  artifact_flags?: unknown;
  content_sha256?: string | null;
  media_ref?: string | null;
  precedents_used?: number;
  [k: string]: unknown;
}

export interface RunResponse {
  correlation_id: string;
  status: RunStatus;
  outputs: Record<string, unknown>;
  timeline: Array<Record<string, unknown>>;
  error?: string | null;
  /** Present on many apps: the AI's decision + why, and the writes staged for approval. */
  decision?: string | null;
  reasoning?: string | null;
  planned_writes?: Array<{
    dataset_id?: string;
    action_id?: string;
    source_id?: string;
    payload?: Record<string, unknown>;
    editable_fields?: unknown[];
  }>;
  /**
   * Per-item review cards: analysed media, bureau/KYC `api` checks, and fraud
   * `case` cards. Disposition each via `submitFeedback()` — the app's
   * `item_review_gate` governs which must be reviewed before `/approve`.
   */
  item_findings?: ItemFinding[];
  citations?: unknown[];
  /**
   * Hash of `planned_writes` as displayed. Echo it back as
   * `ApproveRequest.expected_plan_hash` so a plan that changed between display
   * and approval is rejected (409) instead of silently committed — the
   * display==commit integrity guard.
   */
  plan_hash?: string | null;
  [k: string]: unknown;
}

/**
 * Body for `submitFeedback()` — dispositions one `ItemFinding`.
 * `decision`: `accept` (confirm the finding), `reject` (dismiss it), or
 * `cancel` (skip / undecided). For a fraud `case` item this is the officer
 * confirming/dismissing the flagged concern — it records feedback for learning,
 * it does not itself reject the overall decision.
 */
export interface FeedbackRequest {
  modality: ItemModality;
  task_type: string;
  decision: "accept" | "reject" | "cancel";
  /**
   * REQUIRED when `decision === "reject"` (the server returns 422
   * `reason_required` without it) and capped at 500 chars (422
   * `reason_too_long`). The reason becomes a learned rubric criterion —
   * write one crisp sentence about WHY the finding is wrong.
   */
  reason?: string;
  subject?: string;
}

/**
 * Report from `calibrateFraud()` — read-only. Replays past decisions to show how
 * often each fraud signal coincided with an officer reject. Changes NOTHING
 * automatically; it is a transparency view over historical outcomes.
 */
export interface FraudCalibrationReport {
  app_slug: string;
  screenings_considered: number;
  matched_to_decisions: number;
  flagged_and_matched: number;
  per_signal_hit_rate: Record<
    string,
    { cases: number; officer_rejected: number; rejection_rate: number | null }
  >;
  notes: string;
  generated_at: string;
  [k: string]: unknown;
}

export type ApproveDecision = "approve" | "reject" | "cancel";

export interface ApproveRequest {
  decision: ApproveDecision;
  /** Officer overrides aligned to planned_writes[i]: overrides[i] = { field: value }. */
  overrides?: Array<Record<string, unknown>>;
  /**
   * The `plan_hash` you displayed to the approver (from the run response).
   * If the staged plan changed since display, the server rejects with 409
   * instead of committing different values. Strongly recommended for any
   * UI that shows the plan before approving.
   */
  expected_plan_hash?: string;
  /** Recorded on the decision ledger alongside the approve/reject. */
  decision_reason?: string;
  note?: string;
}

export interface RunRequest {
  action: string;
  inputs?: Record<string, unknown>;
  /** Supply to make the run idempotent / correlate a retry; else the server mints one. */
  correlation_id?: string;
  /**
   * Origin label recorded on the run: "queue_action" (default) | "chat".
   * (Earlier SDKs sent a `surface` field — the server has no such field and
   * ignores it; the real server-side field is `mode`.)
   */
  mode?: "queue_action" | "chat";
}

export interface ToolInvokeResponse {
  correlation_id: string;
  tool_name: string;
  /** `null` for headless direct-decide (a headless app has no panels). */
  panel_id: string | null;
  result: Record<string, unknown>;
}

export interface ChatResponse {
  reply: string; // markdown
  blocks?: unknown[];
  tool_calls?: number;
  [k: string]: unknown;
}

// ── Data (render your own UI) ────────────────────────────────────────────────
export interface PanelDataResponse {
  rows?: Array<Record<string, unknown>>;
  columns?: unknown[];
  total?: number;
  truncated?: boolean;
  note?: string;
  [k: string]: unknown;
}
export interface DetailDataResponse {
  panel_id: string;
  linked_to: string;
  record_id?: string | null;
  record?: Record<string, unknown> | null;
  record_columns?: string[];
  sections: Array<Record<string, unknown>>;
  note?: string;
  [k: string]: unknown;
}

// ── Governance / learning ────────────────────────────────────────────────────
export interface AuditRunListResponse {
  slug: string;
  total: number;
  limit: number;
  offset: number;
  runs: Array<Record<string, unknown>>;
}
export interface SelfLearningResponse {
  [k: string]: unknown;
}
export interface RuntimeTokenResponse {
  token: string;
  [k: string]: unknown;
}

// ── Errors ───────────────────────────────────────────────────────────────────
export class DecisionApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly path: string;
  constructor(status: number, detail: unknown, path: string) {
    const msg =
      typeof detail === "string"
        ? detail
        : (detail as { message?: string })?.message ||
          JSON.stringify(detail)?.slice(0, 300);
    super(`Decision API ${status} on ${path}: ${msg}`);
    this.name = "DecisionApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
}
