<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Plan — SmartApp audit as a CHANGE LEDGER (what changed + who), not a recommendation log

**Status:** PHASE 1 SHIPPED 2026-06-12 (quick version + fail-loud). Phase 2 (materialized hash-chained log) still optional/pending.
**Raised:** 2026-06-12
**Supersedes the framing in:** `docs/smart-app-unified-activity-audit.md` (that doc merged *all* run trails; this one sharpens the goal to *changes*).

> **DONE 2026-06-12 (decisions: quick version · fail-loud · hide unused recommendations · manual edits with old→new):**
> - **Fail-loud auto-commit** — `_record_auto_process_pending` writes the DecisionRecord *before* the write (raises if it can't → no unaudited commit); `_finalize_auto_process_decision` stamps the outcome (raises on audit gap). Replaces the old best-effort/swallow path.
> - **Old → new on manual edits** — `write_app_record` (data_tools.py) now reads prior field values and returns a `delta` ({field:{from,to}}) that rides through the existing write-event audit.
> - **Change Ledger API** — `GET /apps/{slug}/changes` merges `auto_process_decisions` + `app_run_audit` rows where `write_count>0`, **excludes `write_count=0` recommendations**, paginated, `actor`/`outcome` filters, fail-loud on read error.
> - **UI** — `SmartAppAuditScreen.js` rewritten to a single **Changes** ledger (recommendations hidden entirely): rows show 🤖 auto-process / 👤 person · what changed · old→new delta · result; Who + Result filters; pagination.
> Remaining: **Phase 2** (materialized hash-chained `smartapp_change_log`); direct out-of-band overlay edits that never go through a `/run` (no audit row) — covered only if routed through the audited write path.

---

## Principle (the reframe)

> A SmartApp audit should record **what changed and who caused it** — not what
> the AI recommended. A recommendation that no one acted on changed nothing, so
> it has no place in the governance ledger (it's debug/explainability at most).

The AI is invoked in **three modes**:
1. **Auto-process** (trigger, `execution_mode=auto_process`) — AI **auto-commits a change**. → **AUDIT (AI-caused change).**
2. **Auto-recommend** (trigger, `execution_mode=recommend`) — AI **stages a recommendation**; nothing changes until a human acts. → **NOT a ledger entry by itself.**
3. **On-demand recommend** (user asks from the UI) — same: a recommendation; nothing changes until a human acts. → **NOT a ledger entry by itself.**

The two things that ARE changes and MUST be audited:
- **A — AI auto-process commits** (mode 1): the AI changed a source system automatically. Highest scrutiny (no human).
- **B — Human-caused changes**: a user approved a recommendation, clicked a queue action, assigned a case, or edited a record → a write committed. Capture **who** + **what changed** (the delta).

Everything else (a recommendation that led to no action) is noise for a change ledger.

---

## Current state (what we have, and why it's misaligned)

The audit is organized around `app_run_audit` = **one row per AI run** — including
recommend runs that committed nothing. The actual changes exist, but are
scattered and buried among no-change rows:

| Change surface | Where it's recorded today | Has who? | Has what-changed? |
|---|---|---|---|
| **Auto-process commit** (mode 1) | `auto_process_decisions` (policy rule, payload, ok/fail) | AI/policy | ✅ payload |
| **Approval-apply** (human approves a staged rec → commits) | `app_run_audit` row, `action=workflow_staging_apply`: `write_events` + `approver_id` + `override` (from→to) | ✅ approver | ✅ write_events + override delta |
| **Queue action / assign / close** (supervisor fires) | `app_run_audit` `write_events` (overlay `True` for app-local) | ✅ requested_by | ✅ write_events |
| **On-demand write via /run** | `app_run_audit` `write_events` | ✅ requested_by | ✅ write_events |
| **Direct overlay edit / comment** (no /run) | `smart_app_records` provenance (`author_user_id`) | ✅ author | ⚠️ no before/after delta; **not in `app_run_audit`** — confirm in Phase 0 |
| **Recommendation, no action taken** (modes 2 & 3) | `app_run_audit` row, `write_count=0` | n/a | **nothing changed** — should NOT be a ledger entry |

**Misalignment:** the audit UI lists *runs* (incl. `write_count=0` recommendations).
A reviewer asking "what changed and who did it" must read past the noise. The
data is mostly there; the **organizing principle is wrong**.

---

## Target model — a Change Ledger

The ledger's unit is a **committed change**, not an AI invocation. Each entry:

```
change_id, app_id, tenant_id, ts
actor:      { type: "ai_auto" | "user", id, display }   // AI policy OR the human
authority:  "auto_process_policy"(+rule) | "human_approval"(approver)
            | "direct_action"(button/assign/edit)
change_type:"source_write" | "overlay_write" | "assignment" | "status_change"
target:     { source_id, dataset_id, record_id }
what:       payload / delta (from→to where available)
outcome:    "applied" | "failed"
informed_by: correlation_id?        // OPTIONAL link to the AI run that suggested it
content_hash / prev_hash             // tamper-evident chain (like app_run_audit)
```

Recommendations (modes 2 & 3) are **not** ledger entries. They are
explainability context, reachable via `informed_by` from a change, or in a
separate (TTL'd) "AI activity" view — never the headline.

---

## Phases

### Phase 0 — inventory + close capture gaps (prereq)
- Confirm every change surface records **actor + target + delta + outcome + authority**.
- Known gap to fix: **direct overlay edits/comments/assignments** that don't go
  through `/run` land in `smart_app_records` with `author_user_id` but **no
  before/after delta and no ledger entry**. Decide: emit a change record at that
  write point (preferred) — see Phase 2.
- Confirm recommend-mode runs are reliably `write_count=0` (so the "no change"
  filter is exact), and that approval-apply / queue actions always carry
  `write_events` + the acting user.

### Phase 1 — read-time Change Ledger (fast, uses existing data)
A read endpoint `GET /apps/{slug}/changes` that MERGES, newest-first, gated like `/runs`:
- `auto_process_decisions` → `actor=ai_auto`, `authority=auto_process_policy`.
- `app_run_audit` rows **WHERE `write_count > 0`** → `actor=user` (requested_by/approver), `authority=human_approval|direct_action`, `what=write_events (+override delta)`.
- (optional) `smart_app_records` create/update provenance → overlay changes.
- **Excludes** `write_count=0` recommendation runs entirely.
- Paginated (`limit`/`offset`/`total`), with an `actor`/`change_type`/`outcome` filter.
- **Fail loud**: a source-read error surfaces; never a partial ledger that looks complete.

This delivers the user's ask without a write-path rewrite. Recommendations simply don't appear.

### Phase 2 — materialized, hash-chained change_log (authoritative, optional)
- Write a `smartapp_change_log` record at **every commit point** (auto-process,
  approval-apply, queue action, overlay edit) — one tamper-evident, env-routed,
  hash-chained ledger; single source for the UI; no read-time merge.
- Bring the auto-process DecisionRecord audit to **fail-loud parity** with the
  `/run` write audit (today it is best-effort — see open decision).
- Demote `app_run_audit` recommendation detail (reasoning, timeline, evidence)
  to a **TTL explainability store** (the `app_run_audit_preview` retention
  pattern already exists), keeping the permanent record lean = the change ledger.

---

## Audit UI plan (`Citra-UI/screens/SmartAppAuditScreen.js`)

Reorganize around changes (builds on the 2026-06-12 work — modes toggle,
business-language labels, pagination, server-side filters, auto-approved view):

- **Primary tab: "Changes"** — the ledger. Each row reads as **who · what changed · authority · when · applied/failed**:
  - `🤖 Auto-process` — "AI auto-applied <action> on <dataset> — by rule '<policy_reason>'"
  - `👤 <user>` — "<user> approved & applied <action>" / "<user> assigned case to <dept>" / "<user> edited <field>"
  - delta shown (from→to) where available; payload behind "show details".
- **Filter:** actor (Auto-process / Human), change_type, outcome (applied/failed).
- **Secondary tab: "AI activity"** (optional, collapsed/debug) — the full
  recommendation/decision runs incl. no-change ones, for explainability only;
  TTL-eligible. (This is today's "AI decisions" view, demoted.)
- A change can link to "informed by AI recommendation →" (drill-down), not the reverse.

Net: the front-and-center answer to "who did what / what changed" is the
Changes tab; AI recommendations are context, not the record.

---

## Retention
- **Change ledger** — permanent, immutable, hash-chained (compliance).
- **Recommendations / AI deliberation** (reasoning, timeline, evidence, no-change runs) — TTL'd explainability tier.

## Open decisions (need a call before/with build)
1. **Phase 1 only, or push to Phase 2 materialized ledger?** Phase 1 ships fast on
   existing data; Phase 2 is the authoritative tamper-evident record.
2. **Auto-commit audit: best-effort vs fail-loud.** `_record_auto_process_decision`
   currently never blocks the commit (logs on failure); the `/run` write audit
   is fail-loud (`AuditPersistError` aborts). For a change ledger, recommend
   **fail-loud parity** — but it can block a commit on a DB hiccup, so it's a
   deliberate availability-vs-governance call.
3. **Keep recommendation runs at all?** If pure recommendations have no debug
   value, drop them entirely rather than TTL them.
4. **Overlay direct edits** — emit change records (Phase 2) or accept
   `smart_app_records` provenance as the record (no from→to delta)?

## References
- `smart-app-service/main.py` — `auto_process_decisions` (`_record_auto_process_decision` ~6353), `_approve_workflow_staging` (~5089, `workflow_staging_apply`), `_assemble` audit row (~4209), `/runs` + `/auto-commits` read APIs.
- `smart-app-service/models.py` — `execution_mode: recommend|auto_process` (~1719).
- `Citra-UI/screens/SmartAppAuditScreen.js` — current Audit UI (AI decisions / Auto-approved).
- `docs/smart-app-unified-activity-audit.md` — earlier (broader) framing this refines.
