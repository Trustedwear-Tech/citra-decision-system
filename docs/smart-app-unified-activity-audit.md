<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Future work — Smart App "Activity" view (unified governance audit)

**Status:** PARTIALLY DONE — auto-approve visibility shipped 2026-06-12; lifecycle + overlay still pending
**Raised:** 2026-06-12

> **DONE 2026-06-12 — auto-approve (auto-process) is now visible in the UI.**
> - Backend: `GET /apps/{slug}/auto-commits` (paginated, `committed` filter, gated like `/runs`) reads `auto_process_decisions`; `GET /runs/{cid}/audit` now also returns `auto_commits` for that run.
> - UI (`SmartAppAuditScreen.js`): an **"Auto-approved"** mode toggle lists every auto-commit with the **policy rule** that allowed it, the payload, and applied/failed outcome; opening an AI run also shows the auto-approved writes made under it.
> Remaining below: **lifecycle events** and **manual overlay edits** are still not surfaced. The audit-write asymmetry (best-effort vs fail-loud) is still an open decision.
**Owner area:** smart-app-service (read API) + Citra-UI (`screens/SmartAppAuditScreen.js`)

---

## Problem

The Smart App **Audit** screen (`SmartAppAuditScreen.js`, backed by
`GET /apps/{slug}/runs`) shows **only the AI run trail** — the `app_run_audit`
collection, one row per AI `/run` invocation. Human-in-the-loop actions *on a
run* are captured (who triggered it, who approved, the officer's field-override
`from→to` deltas), but several **purely human / governed actions are not
surfaced here** even though they are recorded elsewhere.

A business reviewer who opens "Audit" reasonably expects a complete governance
log of *who did what to this app and its data* — not just the AI decisions.

### What is shown today (AI-run-centric)
Per `app_run_audit` row (`main.py` `/run` persist, content-hash chained):
- `requested_by` — user who triggered the run
- `approver_id` / `actor` / `applied_by` — human who approved a pending decision (appends a 2nd row)
- `override` (`from→to`) — officer field-overrides on the AI-recommended write (editable plan-then-apply)
- `write_events`, `citations`, `references`, `timeline`, `model`, `agent_spec_version`, `content_hash`

### What is NOT shown (recorded elsewhere, or not at all)
| Governed user action | Where it lives today | Surfaced in Audit? |
|---|---|---|
| Archive / restore / publish / audience change / transfer | `lifecycle_audit[]` on the **app document** (`main.py` `_lifecycle_audit_entry`, lines ~6742+) | ❌ |
| Manual overlay edits / comments / review threads (writes to `smart_app_records`) | per-record provenance (`author_user_id`) + thread/history rows | ❌ |
| Inheritance-policy changes, owner transfer | `lifecycle_audit[]` | ❌ |
| **Auto-process (auto-approve) commit DecisionRecord** | **`auto_process_decisions`** (`main.py` `_record_auto_process_decision`, ~6353) | ❌ **no read endpoint at all** |
| Trigger firings (history + failures) | `trigger_runs`, read by `GET /apps/{slug}/ai-triggers/{trigger_id}/runs` | ⚠️ separate endpoint, not in the Audit screen |
| A purely manual action that never invokes the agent | — (may be unaudited) | ❌ |

### Auto-approve (auto-process) — the most important gap
When a trigger auto-commits a write with **no human in the loop**, three trails
are written: the agent run (`app_run_audit`, visible in `/runs`), the trigger
firing (`trigger_runs`), and the **authoritative per-write DecisionRecord**
(`auto_process_decisions` — `action_id`/`dataset_id`/`source_id`, `payload`,
`policy_reason` = the rule that allowed it, `committed` ok/fail, result/error).

Two problems for a reviewer:
1. **The `auto_process_decisions` ledger has no read endpoint and no UI.** It is
   consumed only internally by `auto_process_guard_check` (rate-limit / circuit
   breaker). The Audit screen shows *that the AI decided*, but the **proof of
   what was auto-written and under which policy rule** is invisible. This is the
   highest-scrutiny path (no human), yet its key record is the least visible.
   The Activity view MUST surface `auto_process_decisions` (kind: `auto_commit`)
   with the policy rule, payload, and ok/fail outcome.
2. **Audit-write asymmetry vs RULE #1.** `_record_auto_process_decision` is
   *best-effort* ("never blocks the commit", swallows exceptions with a log),
   whereas the synchronous `/run` write path is `fatal_on_write` (raises
   `AuditPersistError` and aborts if a write can't be audited). So a manual write
   that can't be audited fails loud, but an auto-committed write whose
   DecisionRecord can't be persisted still commits (audit logged-only).
   **Decision needed:** make the auto-commit DecisionRecord fail-loud/abort like
   the `/run` path (preferred for governance parity), or consciously accept the
   best-effort posture and document why. Either way, do not leave it implicit.

---

## Proposed solution — an "Activity" view

Make the Audit screen (or a second **Activity** tab) a **true unified
governance log** that merges three already-existing sources, newest-first, with
a `kind` discriminator and consistent filtering/pagination:

1. **AI runs** — `app_run_audit` (existing `/runs`).
2. **Auto-process commits** — `auto_process_decisions` (kind `auto_commit`):
   the policy rule, payload, and ok/fail per auto-approved write. **Highest
   priority — this trail has no UI today.**
3. **Lifecycle events** — `lifecycle_audit[]` on the app doc (archive/restore/
   publish/audience/transfer/inheritance).
4. **Overlay / app-local data writes** — `smart_app_records` provenance
   (`author_user_id`, created/updated, comment & review-thread rows).
5. (Optional) **Trigger firings** — `trigger_runs` (already has its own
   per-trigger endpoint; fold in for a single timeline if useful).

### Backend
- New read endpoint `GET /apps/{slug}/activity` (or extend `/runs` with a
  `kinds=` param). Same access gate as `/runs` (`_user_can_access`), same
  test↔prod `_bind_app_env` resolution.
- Merge + sort by timestamp; paginate with `limit`/`offset` and return `total`
  (mirror the `/runs` contract — see the pagination/`flagged` work done
  2026-06-12).
- Normalise each source into a common summary shape:
  `{ kind: 'run'|'lifecycle'|'overlay', ts, actor, action, summary, ref }`,
  with a per-kind detail fetch (reuse `/runs/{cid}/audit` for runs).
- **Fail loud** (no silent omission): if any source read errors, surface it —
  do not return a partial log that *looks* complete. (Same principle the
  `flagged` server-filter fix enforced for "needs review".)

### Frontend (`SmartAppAuditScreen.js`)
- Add a `kind` filter row (All / AI decisions / Lifecycle / Edits) alongside the
  existing status + action filters.
- Render lifecycle + overlay rows with the same business-language vocabulary
  introduced 2026-06-12 (e.g. "Made a change in a system", "Sent for human
  approval"); add labels for lifecycle (e.g. "Published the app", "Changed who
  can access", "Archived the app") and overlay (e.g. "Edited a record",
  "Added a comment").
- Keep the IT "Copy as ticket" payload **technical** (unchanged); only the
  display layer speaks business language.

---

## Acceptance criteria
- Opening "Audit"/"Activity" for an app shows AI runs **and** lifecycle changes
  **and** manual record edits in one chronological, paginated, filterable list.
- A reviewer can answer "who archived / published this app", "who changed the
  audience", and "who manually edited record X" without leaving the screen.
- `total` and "needs review" remain truthful across the full history (no
  page-1-only filtering).
- A source-read failure is surfaced, never silently dropped.

## Notes / dependencies
- Builds directly on the 2026-06-12 audit work (server-side `flagged`,
  pagination, business-language relabel) — extend, don't rewrite.
- `lifecycle_audit` and `smart_app_records` provenance already exist; this is
  primarily a **read/merge + presentation** feature, not new write-side audit.
- Scope decision still open: second tab vs. unify into one list. Recommend a
  `kind` filter on a single list (less UI surface, one mental model).
- Related: `docs/smart-app-architecture.md` §7 (audit), the app-owned data plane
  plan (`docs/app-owned-data-plane.md`).
