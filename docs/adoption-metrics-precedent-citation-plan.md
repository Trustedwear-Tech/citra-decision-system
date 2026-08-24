<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Success-Rate Card, Learning Report & Precedent Citation — Implementation Plan

> Status: PLAN (2026-07-19) · Owner: rohit@trustedweartech.com
> Executes wedge-strengthening-plan.md §A.1–A.3 + §B against the code as it
> actually is. Companions: wedge-strengthening-plan.md (strategy),
> [citra-self-improving-loop-plan.md](citra-self-improving-loop-plan.md) (the loop this renders),
> [fraud-screening-admin-panel-plan.md](fraud-screening-admin-panel-plan.md) (the sibling admin card — same UI pattern).

---

## 0. What ALREADY exists (scouted 2026-07-19 — build on it, don't duplicate)

The wedge doc's "Build" steps assumed more greenfield than there is:

| Wedge-doc assumption | Reality |
|---|---|
| "Finish the decision_records unification (Step 1)" | **DONE.** `decision_records` is written on all four modes — `human_approved` (main.py:7725), `human_rejected` (:7520), `human_direct` (:6976), `auto_process` (:8438) — with `overrides[]` from→to deltas, `decision_reason`, `record_keys`, and `outcome` stamped by the Stage-4 poller. |
| "Per-app weekly buckets endpoint" | **MOSTLY DONE.** `compute_loop_metrics()` (main.py:9242) already computes by_mode counts, override_rate, good_rate, weekly `trend`, per-model breakdown; exposed at `GET /apps/{slug}/loop-metrics` (:9324) which also merges a memory block (rubric inventory + grounding freshness). |
| "Stamp retrieval_count at /run" | **ONE LINE AWAY.** `_prefetch_few_shot_blocks` (runtime.py:675) already returns `few_shot_refs` (one entry per retrieved sample, with similarity + decision); `len()` is the count — it just isn't stamped onto the response/audit. |
| "Embed decision_records into a per-deployment Milvus collection" | **PIPELINE EXISTS.** The grounding write-back already embeds closed decisions (good + bad) into the shared `Historical_Refresh` collection (`loop_decision_to_sample`, grounding_refresh.py:153; delta upsert :619), and /run already retrieves them. What's missing is only CITABILITY: samples don't carry decision_id/disposition/outcome, and nothing in the output contract cites them. |
| "Officer overrides feed the rubric" | **DONE.** `fold_decision_feedback` (analysis_rubrics.py:91) folds `corrected <field>: <from> → <to> — <reason>` into the decision rubric on approve-with-overrides and reject. |

So the real build is: **one org endpoint + one stamp + two derived metrics + citability + two UI surfaces.**

## 1. A.1 — "Success Rate" HomePanel card (org-wide)

### Bucket definitions (exact — these lie in a demo if fudged)

| Bucket | Definition (from `decision_records` unless noted) |
|---|---|
| **Accepted** | `mode="human_approved"` AND `overrides == []` |
| **Accepted with changes** | `mode="human_approved"` AND `overrides` non-empty. NEVER collapsed into accepted or rejected. |
| **Rejected** | `mode="human_rejected"` |
| **Pending** | `smartapp_workflow_staging` rows with `status` starting `pending_` (decision_records only holds COMMITTED dispositions; cancel is deliberately unrecorded — count `status="cancelled"` there too if we ever show withdrawals) |
| **Automated** (secondary line) | `mode="auto_process"` — shown transparently, never mixed into the human acceptance rate (kill-switch doctrine: automation is configured, not graduated) |
| Excluded | `human_direct` (no AI recommendation existed — counting it would distort the "do they trust the AI" question) |

**Acceptance % = accepted ÷ (accepted + accepted_with_changes + rejected).**

### Build

1. **Endpoint** `GET /org/decision-stats?period=week|month|all` in smart-app-service:
   - Gating + period parsing identical to `org_screening_stats` (reuse
     `_screening_period_start`; admin roles; both-claims tenant matching —
     the same tenant-key lesson the screening review just taught us: verify
     which tenant key `decision_records` rows carry and match accordingly).
   - One aggregation over `decision_records` grouped by `slug` × mode ×
     overrides-emptiness, plus one `smartapp_workflow_staging` count for
     pending. App display names via the existing `$in` slug lookup.
   - **New index required**: `[(tenant_id, 1), (created_at, -1)]` on
     decision_records — the existing compounds lead with app_id/agent_id and
     cannot serve a tenant-wide time window (same gap the screening review
     found on its collection).
2. **UI**: "Success Rate" FeatureCard in the HomePanel Admin section opening a
   modal — the exact `ScreeningHealthScreen` pattern (service client +
   modal screen + period selector + stat tiles + per-app rows). Tiles:
   Accepted (green) · Accepted with changes (amber) · Rejected (red) ·
   Pending (grey) · Acceptance % (trend arrow vs prior period). Per-app row:
   name · accepted · with-changes · rejected · acceptance % · attention dot
   when acceptance % is low or falling. Tapping a row opens the A.3 Learning
   Report for that app (one modal, two levels — same as Screening Health).
   Mirror the card in MobileHomeScreen.js.

## 2. A.2 — Memory-impact metrics

### 2.1 `retrieval_count` stamp (the prerequisite, ~half a day)
- In `execute_run`: `retrieval_count = len(few_shot_refs)` after the prefetch
  chain (runtime.py:2679/:2711); add to `RunResponse` and thread into
  `_build_audit_doc` (main.py:5865) AND `_build_decision_record` (:5996) so
  the lift is computable from the ledger alone.
- Backfill: none. The metric starts accumulating at deploy; the endpoint
  reports `lift: null` with a "not enough data" note until both cohorts have
  ≥N (default 10) disposed decisions — never fake a number.

### 2.2 Memory lift (headline)
`acceptance%(retrieval_count > 0) − acceptance%(retrieval_count == 0)` per
app + org-wide, same period, human modes only. Computed inside the A.1/A.3
aggregations (one extra group key). Buyer sentence rendered verbatim on the
card when positive: *"Recommendations backed by your own past decisions are
accepted N points more often."*

### 2.3 Correction absorption
Both halves already persist:
- **Taught**: rubric `corrections[]` entries with `at` timestamps
  (analysis_rubrics upsert) — entries parse as `corrected <field>: …`.
- **Recurred?**: `decision_records.overrides[].override` keys are the field
  names, with `created_at`.
Metric: for each corrected field F with correction time T, did another
override on F occur after T (+settling window, default 7d)? Report
`taught: N, stopped_recurring: M` + the per-field list. Pure Python over two
bounded queries in the A.3 endpoint — no new storage. (Parse the field name
from the correction text OR — cleaner — stamp a structured `field` key onto
the correction entry in `fold_decision_feedback` going forward; do both:
stamp new, parse old.)

### 2.4 Learning curve
Already computed: `compute_loop_metrics().trend` (weekly buckets) + `by_model`
(proves the fixed-model claim). A.3 just renders it; extend trend buckets to
carry acceptance% and good% per week if not already split by mode.

## 3. A.3 — Per-app Learning Report (deep view)

**Server**: extend `GET /apps/{slug}/loop-metrics` (do NOT add a parallel
endpoint — one source of truth) with: time-to-decision median/p90 (staging
`created_at` → `resolved_at`), top-overridden fields (group `overrides[]`
keys), memory lift (2.2), correction absorption (2.3), and a `period` param
consistent with A.1. Everything else (acceptance, override rate, outcome-good
rate, weekly trend, grounding freshness, rubric inventory) is already in the
response.

**UI**: the drill-down level of the Success Rate modal (§1) — sections in
reading order: the five headline tiles → learning curve (weekly bars: simple
inline bar rows, no chart lib) → memory lift sentence → correction absorption
("14 corrections taught; 11 never needed again" + per-field list) → top
overridden fields → time-to-decision. PDF export: defer to the report
pipeline later (out of scope for v1 — the screen is the deliverable).

## 4. B — Precedent citation (as part of the recommendation)

**Design decision: enrich the EXISTING grounding pipeline rather than build a
second precedent index.** The loop write-back already embeds closed decisions
into `Historical_Refresh` and /run already retrieves them as few-shots — but
anonymously. Making them CITABLE needs four small changes, not a new
retrieval system (also avoids a new Milvus collection — the shared-collection
pattern exists precisely because of the collection cap):

1. **Citable samples** — `loop_decision_to_sample` (grounding_refresh.py:153)
   stamps `decision_id`, `mode`, `outcome_label`, and (for overrides/rejects)
   a one-line `decision_reason` onto the sample payload; `_ensure_grounding_collection`
   gains the scalar fields (additive schema change → one-time reindex via the
   existing refresh path). `_query_neighbor_samples` returns them in refs.
2. **Prompt** — `_prefetch_few_shot_blocks`' block header labels each sample:
   `[case dr_… — approved, outcome good]` / `[case dr_… — overridden: valid
   tenant change]`, and instructs: *when your recommendation follows or
   deviates from a cited case, say so.* Overrides-with-reasons render first
   (they are the highest-value "differs from" precedents).
3. **Output contract** — extend `_AUDIT_INSTRUCTION` (runtime.py:90) with
   `cited_precedents: [{decision_id, relation: "similar"|"differs", note}]`;
   parse in `_extract_audit_block`; add `cited_precedents` to `RunResponse`,
   `_build_audit_doc`, `_build_decision_record`, and the `response_shape`
   contract (main.py:9845). Keep it separate from `references` (retrieved) —
   cited-vs-retrieved cross-check is the existing design pattern, and
   "citation rate" (cited ÷ retrieved) becomes a free A.3 metric.
4. **UI chips** — citra-app-runtime: add `citedPrecedents` to the `RunResult`
   type (PanelRenderer.tsx:1254), map it beside decision/reasoning
   (:1563/:1625), render a chip row in ActionResultModal after Reasoning
   (:2479) with the existing `.chip` styling: `≈ dr_2211 approved · good` /
   `≠ dr_3021 overridden — valid tenant change`. Chip tap → the ledger record
   (read-only detail; v1 can show a modal with the DecisionRecord context/
   reasoning via a small `GET /apps/{slug}/decisions/{decision_id}` reader).

**Measurement is free**: with `retrieval_count` (A.2) and `cited_precedents`
on the ledger, "acceptance with ≥1 citation vs without" is one more group key
in the A.3 aggregation — the proof metric from day one.

## 5. Sequencing & effort

| Step | What | Effort | Depends on |
|---|---|---|---|
| 1 | `retrieval_count` stamp (RunResponse + audit + DecisionRecord) + `(tenant_id, created_at)` index | ✅ BUILT 2026-07-19 — captured pre-RAG in execute_run (`run_references.retrieval_count`), carried via a new `WorkflowStagingRow.retrieval_count` to `DecisionRecord.retrieval_count` on approve/reject (None = pre-stamp row, kept distinct from a real 0); index added | — |
| 2 | `GET /org/decision-stats` + Success Rate card (org level) | ✅ BUILT 2026-07-19 — one Mongo group (slug × mode × overridden × memory-cohort) + staging pending backlog; exact buckets enforced server-side; memory-lift published only past `MEMORY_LIFT_MIN_COHORT` (default 10) per cohort, else an honest note; both-claims tenant matching. UI: `SuccessRateScreen.js` + `DecisionStatsService.js` + Admin FeatureCard. Live-smoked on local acme-power (real July-9 loop decisions: 1/2/2 + 2 pending; 403 non-admin) | 1 (index) |
| 3 | Extend loop-metrics (time-to-decision, top-overridden, lift, absorption) + structured `field` stamp in fold_decision_feedback | ✅ BUILT 2026-07-19 — compute_loop_metrics gains memory_lift cohorts + top_overridden_fields; endpoint adds time_to_decision (staging created_at→resolved_at median/p90) + correction_absorption (structured `fields` on new corrections, prose-parse fallback for old; 7-day settling window) | 1 |
| 4 | Learning Report drill-down UI | ✅ BUILT 2026-07-19 — tap an app row in SuccessRateScreen → per-app report (headline tiles, weekly override-rate bars, memory-lift sentence, corrections-absorbed list, most-corrected fields); period never ejects the drill-down | 2, 3 |
| 5 | Citable samples + prompt block (B.1–B.2) | ✅ ALREADY EXISTED — loop samples' `source_id` IS the decision_id, override notes + AVOID polarity ride decision/reasoning, and the neighbors prompt header already instructs citing `source_id`. No schema change, no reindex needed | — |
| 6 | `cited_precedents` output contract + ledger stamp (B.3) | ✅ BUILT 2026-07-19 — _AUDIT_INSTRUCTION emits `cited_precedents[{decision_id, relation: similar\|differs, note}]`; _extract_audit_block returns a validated 5th element (id-less entries dropped, bad relations coerced); threaded RunResponse → audit → WorkflowStagingRow → DecisionRecord.recommendation → response_shape | 5 |
| 7 | Precedent chips UI + decision reader endpoint (B.4) | ✅ BUILT 2026-07-19 — PanelRenderer RunResult.citedPrecedents (live-run + staged `_recommendation` paths) → "Based on past cases" chip row after Reasoning (≈ green / ≠ amber, note as tooltip); `GET /apps/{slug}/decisions/{decision_id}` reader (audience-gated, slug-scoped). tsc clean | 6 |
| 8 | Live-verify on acme-power: one approve/override/reject cycle → card + report + chips | ✅ PASSED 2026-07-19 — real LLM run on complaint-auto-routing: retrieval_count in references→staging→ledger (cold cohort, correctly isolated from the 1 RAG ref); cited_precedents=[] contract-verified on a cold run; loop-metrics shows absorption 1/1 (`assigned_to` taught July-9, never recurred) + top-overridden + 1.3s median; org cohorts picked the fresh decision up as cold=1; reader 200 | all |

Total ≈ 1.5–2 weeks, matching the wedge doc's estimate but with materially
less new surface (no new collection, no new per-app endpoint, no cron).

## 6. Exactness rules (carried from the wedge doc + this session's reviews)

- **Accepted-with-changes is its own bucket** — never folded either way.
- **Human and automated rates never mix**; `human_direct` is excluded.
- **Tenant keys**: verify which tenant the ledger rows actually carry and
  match the admin's claims accordingly (the screening panel's tenant-key bug,
  found in review, must not be re-introduced here).
- **No fabricated numbers**: lift/absorption return null + "insufficient
  data" below cohort minimums; truncation is always surfaced.
- **No cron, no new collections** (the one Milvus schema change is additive
  on the existing shared collection).
- The card answers one question in plain words; everything analytical lives
  in the drill-down.
