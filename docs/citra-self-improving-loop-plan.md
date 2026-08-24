<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra SmartApp — Self-Improving Decision Loop (Implementation Plan)

> Status: ready to build · Owner: rohit@trustedweartech.com · Last updated: 2026-06-16
> Companion to citra-core-open-source-thesis.md. Decisions locked: outcome signal = **read-back poll via MCP**; sequence = **loop first, then carve-out**.

## Goal

Close the five-stage loop so SmartApp decisions get measurably better over time **without retraining or changing the model** — the learning lives in memory (the decision ledger + grounding corpus), not in weights.

**The one health metric:** override-rate ↓ and outcome-quality ↑ while the model is fixed → then prove it survives a model swap.

All work lands in `smart-app-service/` and `citra-app-runtime/`, which are in the open set — so building the loop *is* building Citra Core.

## Current state (≈70% there)

| Stage | Status | Where |
|---|---|---|
| 1. Recommend | ✅ | `runtime.py`; `RunResponse.planned_writes` (models.py:2620); audit → `smartapp_run_audit` |
| 2. Decide (approve/override) | ✅ | `main.py:5520` approve; **override from→to delta already captured** at `main.py:5822-5900`, audited at `:6033` |
| 3. Act (governed write) | ✅ | `call_dept_mcp_execute_action()` (`proxy_clients.py:213`); idempotency + dry-run re-validate on override |
| 4. **Observe outcome** | ❌ **MISSING** | — |
| 5. **Write-back to memory** | ◐ partial | few-shots from *static* `decision_dataset` → Milvus `samples_<agent_id>` (`grounding_refresh.py`); manual refresh; human approvals **not** fed back |
| Model swappability | ✅ | `llm_client.py:36-78` — OpenAI-compatible, per-tier base URL |

**So the only real build is Stage 4 + closing Stage 5.** The gold label (the officer's override) is already on disk; we just don't feed it back.

## Pilot app vs. open-source demo (two different things)

- **Pilot** = the ONE app we wire the loop onto FIRST, internally, to validate it end-to-end on *real* decisions before generalizing across the other apps. It needs per-app config (window `N`, reversal rule, key filter), so we tighten the design on one app first.
- **Open-source demo** = a fictional tenant with synthetic data. Demo data is
  synthetic throughout; nothing published here is drawn from a live deployment.

**Confirmed + wired pilot config** (live in the `route_complaint` AgentSpec, validated):
- source = Postgres → `kind="sql"`; table `complaints`, key `complaint_id`, status field `status`.
- `window_days = 7`.
- `good = ["resolved"]`, `bad = ["new"]` (reset/re-triage = reversal), `neutral = ["escalated"]` (legitimate path, no routing signal → stamped neutral, not re-polled, ignored by write-back).
- `hold_field = "assigned_to"` → if the complaint was re-routed away from who we assigned, the decision was overturned → bad.
- Statuses `routed` / `in_progress` → unknown → re-poll next tick. (Edge case: a complaint stuck `in_progress` forever re-polls forever — add a max-age "unknown_timeout" later.)

## Pilot app (confirm)

Recommendation: **the complaint-routing / `route_complaint` reassign app** (the utility deployment). It already has the editable plan-then-apply override path built (2026-06-01), it's a clean decision (reassign → resolve), and its source-of-record exposes a status field the dept-MCP can re-query — which is exactly what read-back polling needs.

> Read-back polling is a **direct, structured read-by-key** — NOT the NL→query planner. We already store the record's natural/idempotency key with the decision, so the poll is a point lookup (`status of record X`), which must be deterministic, cheap, and auditable. Putting the GLM NL planner in the verdict path would be fragile (content=null bugs, self-correction retries), non-deterministic, and could mislabel outcomes → poison the grounding corpus. The pilot must be an app whose dept-MCP can return a record by key. Confirm `route_complaint` qualifies, or name an alternative.

## Outcome mechanism — read-back poll via MCP

A background worker, some time after a decision commits, re-queries the source-of-record through the dept-MCP and classifies the outcome:

- **Good** — the action is still in effect (e.g. complaint `resolved`/`closed`, not reopened, assignee unchanged).
- **Bad** — reversed / reopened / re-routed / disputed within the window.
- **Unknown** — can't determine yet (re-poll later) or source unreachable (fail loud, log, leave unstamped — never default to "good").

Two app-specific parameters (the only domain inputs needed):
1. **Polling window `N`** — how long to wait before judging (e.g. 7 days for complaint resolution).
2. **Reversal definition** — which source field(s)/values mean "bad" (e.g. `status` returns to `open`, or `assigned_to` changed again).

## Build steps

### Step 1 — Decision Record (the open ledger + open schema)
Unify today's split ledgers (`smartapp_run_audit` for human, `auto_process_decisions` for autonomous) into one `decision_records` collection, written on every committed decision (both paths).

Record shape (this is the **open schema spec** we publish):
```
decision_id, app_id, agent_id, correlation_id,
context: { input_fields → values }      # the "question"
recommendation: { action, payload, reasoning, citations }
override: { field: {from, to} } | null  # the gold label (already captured at main.py:6033)
action_result: { status, idempotency_key }
outcome: { label: good|bad|unknown, signal: "mcp_readback", observed_at, evidence } | null
model: { id, base_url_tier }            # for the swap proof
created_at, content_hash                # chain integrity (reuse existing pattern)
```
- Write from the approve path (`main.py` `_approve_run_impl`) and the auto-process path (`main.py:6619-6705`).
- Keep `smartapp_run_audit` as-is (don't break audit); `decision_records` is the loop-facing projection.

### Step 2 — Outcome poll worker (direct read-back via MCP)
- **Reuse the existing structured read plane** — no new MCP endpoint, no dept-MCP redeploy. The dept-MCP already exposes `POST /run_query` (distinct from the NL `/query`): a structured, SELECT-only, PDP-gated, audited read whose `query` shape is per-`kind` (`sql` SELECT-by-key, `odata` `{entity,$filter}`, `soql`, `rest`). **DONE:** added `call_dept_mcp_read()` in `proxy_clients.py` that POSTs to `/run_query` and **refuses `kind="semantic"`** (so the verdict can never fall back to the NL planner).
- **DONE:** `poll_decision_outcomes()` in main.py — selects committed `decision_records` with `outcome=null` past their window, mints the app's system bearer, does the **structured read-by-key** via `call_dept_mcp_read`, classifies good/bad/unknown, stamps via `_update_decision_record`. Registered in the (prod-only) scheduler loop; self-gates by `window_days`. Per-app rule is the declarative `OutcomePollConfig` on `AgentSpec.outcome_poll` (good/bad status sets + `hold_field` for "was it overturned"). Fail-loud + non-fatal per record. Verdict is never via the NL planner.
- **Index needed (IaC):** `decision_records` on `(outcome, action_result.committed, created_at)` for the poller scan, plus unique `decision_id`.
- **Remaining:** enable the pilot by adding the `outcome_poll` block to the `route_complaint` AgentSpec (values below), and confirm `window_days` + good/bad sets.
- Per-app config: `{ outcome_poll: { window_days, source, key_field, reversal_rule } }` on the AgentSpec/AppSpec.
- **Fail loud** (per house rule): source unreachable → log + leave `unknown` + re-queue; never silently mark good.

### Rejections as immediate negative labels (DONE for the queue-action path)
A **reject** ("the recommendation is wrong") is the fastest, cleanest negative
signal — no commit, no poll. On reject we now write a DecisionRecord with
`outcome` stamped immediately `{label: "bad", signal: "human_rejected"}`
(`mode="human_rejected"`). A **cancel** ("not actioning now") is NOT treated as a
label. Write-back (below) should weight these as strong negatives. **DONE:** the
queue-action / plan-then-apply reject path. **Follow-up:** the workflow-staging
review reject path (row-based, same pattern).

### Step 3 — Write-back (close Stage 5) — DONE (v1)
- **DONE:** `loop_decision_to_sample()` converts a validated-GOOD DecisionRecord (incl. the officer's *corrected* output, since the committed payload reflects the override) into a grounding sample; `refresh_grounding(extra_samples=…)` merges them into the corpus alongside the seed history (deduped, only grows the pool so the shrink-floor guard is safe). `_fetch_loop_samples()` reads PROD `decision_records` with `outcome.label="good"` and feeds the refresh. Verified: converter unit-tested, all files compile.
- **Behavior:** good decisions are reinforced as positive few-shots; bad/rejected are simply NOT added (not reinforced). Recommend-time retrieval already reads the shared corpus → loop closes once a refresh runs.

**Done (this round):**
- **Continuous DELTA write-back + fixed-size memory — DONE.** A good outcome no longer rebuilds the whole corpus. The poller embeds **only that one** validated decision and **upserts** it into the agent's vector rows (`upsert_decision_sample`): embed×1 → dedup by `source_id` → insert → **evict oldest non-canonical beyond `GROUNDING_MAX_ROWS_PER_AGENT` (default 300)**. So the memory is **bounded** and the per-outcome cost is ~1 embedding, not a full re-pull/re-embed. Gated by `AUTO_REFRESH_ON_OUTCOME` (default true). **Fast write-back is `human_approved` ONLY** — see "Memory = human judgment" below. The full `refresh_grounding` rebuild is now the **backstop** (re-pull seed, re-curate canonicals, guard) and is **also capped** to the same size (canonical + ≤max_rows non-canonical, favouring validated loop decisions), cutting its embed cost too.
- **Periodic full rebuild — DONE (weekly).** A lifespan loop (`_rebuild_all_grounded`) enqueues a full rebuild for every grounded app every `GROUNDING_FULL_REFRESH_DAYS` (**default 7**), prod-gated (runs only with `SCHEDULER_ENABLED`). Waits one interval before the first pass so a restart doesn't stampede. This is what re-curates canonicals + folds **direct-human decisions** (seed-table) into memory on a cadence; the delta path carries everything in between. Caveat: restart-relative timer (no persisted last-run) and it enqueues one job per grounded app — fine at demo scale, throttle if many apps.
- **Fast capture of direct human decisions — DONE.** The direct (no-LLM) tool-invocation write path now emits a `mode="human_direct"` DecisionRecord (action payload as `record_keys`, no recommendation) so the poller outcome-validates it and it joins the ledger + can trigger auto-refresh. **Grounding write-back excludes `human_direct`** (it carries the action payload, not rich case context) — direct decisions are grounded via the periodic decision-history **table pull**, which has full context. So: direct decisions now get fast *outcome tracking*; their *grounding* still comes from the table pull (correct, by design).
- **Memory = human judgment; auto-process excluded from the fast loop — DONE.** The fast write-back (delta + `_fetch_loop_samples`) now feeds on `mode == "human_approved"` ONLY. Rationale: auto-process fires only when the policy gate's bounding conditions are met — i.e. the model's *own confident, typical* decisions — so fast-refeeding them risks an **echo chamber / homogenization**, even when outcome-validated. New signal comes from human judgment (override/direct/reject), not the model repeating itself. Auto-process is **not discarded**: its good outcomes still reach grounding via the **periodic resolved-table pull** (slow, deduped), and its outcomes still drive metrics + the circuit breaker. Net: fast loop = human-mediated; auto = applied at scale, grounded slowly via the table.

**Done (this round — the two things that make the loop categorically ≠ "re-ground on the table"):**
- **Override as a contrastive correction — DONE.** When the officer overrode the AI, `loop_decision_to_sample` now folds the correction into the few-shot's `reasoning` (e.g. *"Officer CORRECTED the AI recommendation - assigned_to: 'B' -> 'C'. Prefer the corrected decision."*). The reader already returns `reasoning_trace`, so **no migration/reader change**. This is the signal a static table (which stores only the final value) cannot give. ASCII-only (cp1252-safe).
- **Negatives / the "outcome flow" — DONE (v1).** Rejected or bad-outcome decisions (`outcome.label == "bad"`, ANY mode) now become **anti-pattern few-shots**: decision label prefixed `AVOID:` + `reasoning` marked `ANTI-PATTERN: ... do NOT repeat`, `polarity: "bad"`. Fed via both the periodic pull (`_fetch_loop_samples` now `$or` positives `good+human_approved` / negatives `bad`-any-mode) and the poller delta (fires on positive **or** negative; sets `rec["outcome"]=verdict` so polarity is detected). Positives stay echo-chamber-safe (human_approved only); negatives are safe for all modes (learning to avoid is always valid). This is the other thing the table literally cannot do (rejected/bad decisions never appear as resolved rows).
- **Refresh defaults to MANUAL; auto-run is per-app opt-in — DONE.** New per-app flag `OutcomePollConfig.auto_refresh` (**default False**). Outcomes are still *tracked* (the poller stamps the ledger), but memory updates only on a **manual** refresh until the user enables auto-run for the app. The flag gates BOTH the delta write-back (poller) and the periodic full rebuild (`_rebuild_all_grounded` skips apps where it's off). The manual `/grounding/refresh` endpoint always works. Pilot left at default (tracks; manual).
- **In-app auto-run toggle — DONE.** Backend: `GET`/`POST /apps/{slug}/self-learning` (edit-rights gated; 409 if the app isn't grounded / has no outcome tracking; turning auto ON also sets `outcome_poll.enabled=true`). Client: `SmartAppService.get/setSelfLearning`. UI: an **"Auto-learn: on/off"** toggle per app on the Decision Apps screen (next to "Refresh grounding"), loads current state for grounded apps and flips it. So the user controls per-app auto vs manual from the app list. (Backend verified; RN UI verified by syntax/tag-balance — not metro-run.)
- **Builder grounds ONLY on catalogue-MARKED decision datasets — DONE (skill).** `citra-fewshot-from-history` no longer auto-selects/judges an arbitrary table: a dataset is eligible only when the catalogue marks `decision_history.is_decision_record == true` (set by IT/data in dept_sources / catalogue). Unmarked → grounding OFF (tell BA to ask IT to mark it). Removed the "observed/unflagged" judging path + the unflagged Gate-A variant; skill also states auto-run stays off in the build.
- **Outcome config is CATALOGUE-DERIVED — DONE (steps 1+2).** (1) `DecisionHistory` (source-mcp-template) extended with the OUTCOME signal IT declares: `outcome_field`, `good_values`/`bad_values`/`neutral_values`, `outcome_hold_field`, `key_field`, `settling_window_days`. (2) `/builder/history-quality` now returns a derived **`suggested_outcome_poll`** built from those fields (auto_refresh forced False = manual; emits a note if an outcome column is declared but `key_field` is missing). (3, skill) the builder copies `suggested_outcome_poll` **verbatim** into `agent_spec.outcome_poll` — never hand-authoring outcome columns/values. So the loop's outcome semantics now flow IT → catalogue → builder, not per-app guesswork. **Architecture note:** IT **authors** `decision_history` (incl. the outcome fields) in dept_sources / source-mcp — that is the source of truth. The Data Discovery Service is a **derived** pipeline that builds/serves the catalogue *from* what IT authored; it does NOT invent or auto-suggest outcome semantics. (Earlier "discovery auto-suggest" idea dropped — wrong data-flow direction.) **DONE:** the demo `complaints` dataset's `decision_history` in `sources.json` now declares the outcome signal (`outcome_field=status`, good=[resolved], bad=[new], neutral=[escalated], `key_field=complaint_id`, `outcome_hold_field=assigned_to`, `settling_window_days=7`, `decision_column=assigned_to` for grounding). Verified end-to-end: the endpoint's derivation over this real declaration yields an `OutcomePollConfig` **identical to the hand-authored pilot config** — so the authored→derived path is proven. (The pilot app json still carries its hand-authored `outcome_poll`; a freshly *built* app over this source would now derive the same thing automatically.)

**Still deferred:**
- **Harden negatives (structural polarity).** v1 marks anti-patterns *textually* (`AVOID:` + reasoning) in the shared corpus — a weak model could still follow one. The robust version adds a Milvus `polarity` field + a `neighbor_samples` reader that presents negatives in a separate "avoid" block (and balances positive/negative quotas in the cap). Needs a collection migration (corpus is rebuildable, so droppable). Also: reject-path negatives currently flow only via the periodic pull (not the fast delta — the poller skips already-stamped records); add a delta-upsert at reject time for instant negative learning.
- **Dedup loop-vs-seed.** An LLM-routed complaint can appear twice — as a DecisionRecord (`decision_id`) and a seed table row (`complaint_id`). Dedup by the underlying record key.

### Pilot prerequisite: `route_complaint` grounding — DONE
Added to `04_complaint_auto_routing.json` (validated): a `neighbor_samples` tool (`past_routings`), a GroundingContract grounding on **`decision_field=assigned_to`** (who to route to), pulling **resolved** complaints (`filters.status=resolved`), `input_fields=[complaint_text, category, division]`, `output_fields=[assigned_to, priority]`, demo-sized guard thresholds (`min_samples=4`, `min_canonical=2`, `target_count=6`), and a system-prompt clause telling the agent to consult `past_routings` before choosing the officer. The agent now reads the corpus at recommend-time and write-back feeds validated-good routings into it.

## To run the loop end-to-end (live, not yet done)
1. Re-seed the updated `04_complaint_auto_routing.json` into Mongo (the agent_spec now carries grounding + outcome_poll).
2. `POST /apps/{slug}/grounding/refresh` once to build the initial corpus from resolved complaints (then write-back augments it).
3. `SCHEDULER_ENABLED=1` so the outcome poller ticks (prod-only).
4. Make routing decisions → after `window_days` the poller stamps good/bad → re-refresh grounding folds good ones back in. (For testing, drop `window_days` to a small value.)
5. Provision `decision_records` indexes (IaC): unique `decision_id`, plus `(outcome, action_result.committed, created_at)`.

### Step 4 — Proof — DONE (metrics surface)
- **DONE:** `compute_loop_metrics()` (pure) + `GET /apps/{slug}/loop-metrics` (read-only, view-gated) compute, from `decision_records`: **override_rate** (down = learning), **good_rate** (up = improving), **automation_rate**, a **weekly trend** (`trend_weekly` — the curves), and a **per-model breakdown** (`by_model` good_rate — the model-swap-invariance view). Verified on a synthetic improving scenario: trend W23→W24 override_rate 0.75→0.25, good_rate 0.33→0.67; by_model {glm, claude}. This is the chart that *shows* the loop works rather than asserting it.
- **Per-app UI — DONE.** `SmartAppService.getLoopMetrics(slug, days)` + a **"Learning"** button on each grounded app (Decision Apps screen) opens a modal showing the headline rates, the weekly trend, and good-rate by model. So measurement is **per-app, in the UI**, not just an endpoint. (Backend compiled + unit-tested; RN UI verified by JSX parse — not metro-run.)

### Review hardening — DONE (the two HIGH findings)
- **HIGH-1 indexes — DONE.** `decision_records` indexes provisioned at startup (`_ensure_index`, best-effort like the rest): unique `decision_id`; `(action_result.committed, outcome, created_at)` for the poller scan; `(agent_id, tenant_id, outcome.label, created_at)` for write-back; `(app_id, tenant_id, created_at)` for loop-metrics; + unique `decision_id` on the test-prefixed collection. No more collection scans on the hot paths.
- **HIGH-2 poller starvation — DONE.** A never-settling record (stuck `in_progress`, or an unreadable/unauthorized source whose read-back keeps erroring) is **retired** after `window×3` (min `window+7d`) past commit: stamped a terminal `outcome.label="unknown_timeout"` (`signal=poll_timeout`). It stops re-polling and no longer blocks the oldest-first queue; write-back + metrics treat it as `unknown` (never good/bad → never pollutes memory). Counted in the poller log. Verified.
- **Multi-instance duplication — DONE (leader election).** The scheduler tick, **outcome poller**, periodic grounding rebuild, and the preview/idle sweeps are SINGLETON work — running them in every replica double-fires triggers, double-stamps outcomes, and runs N rebuilds. Fixed with Redis leader election (`_leader_election_loop`: `SET nx px` acquire + `xx` renew at ⅓ TTL; key `smartapp:scheduler:leader`, TTL 30s): every instance runs the election; only the lock holder executes the four singleton loops (each checks `_is_leader()` per iteration, so leadership **fails over** automatically when the holder dies). Fail-closed on a Redis error (relinquish, never split-brain); releases the lock on graceful shutdown. The trigger-QUEUE consumer is intentionally **not** gated — it's a competing consumer (Redis Streams `XAUTOCLAIM`), safe and desired on every instance. Verified: helper present, all 4 loops gated. **Next:** decide on Temporal for the broader job control-plane (visibility / pause / cancel across many apps) — leader election removes the correctness bug; Temporal would be the management upgrade.

### Headless / decision-API mode — DONE
A Decision App can now be **agent-only** — the decision engine with no Citra UI, exposed via the decision API for an external/custom front-end (internal ops with their own console). Built across three pieces:
- **`AppSpec.headless` flag** + validator tolerance (a `headless` app may have no `panels`/`pages`; non-headless still must). Surfaced on `AppSummary` (+ the list build) so the UI knows. Verified: headless AppSpec validates, non-headless empty still rejected.
- **`GET /apps/{slug}/decision-contract`** — self-describing contract (request `input_schema`, response shape, `/run` + `/approve` + token endpoints, auth + the governance rule). Works for any app.
- **UI card variant** — headless apps show as a normal manageable card on the Decision Apps screen, badged **"API · headless"**, with **"Copy API URL"** (`…/apps/{slug}/run`) + **"Contract"** (Alert from `/decision-contract`) instead of "Open". All other controls (edit/audit/refresh/auto-learn/Learning/archive) identical. `SmartAppService.decisionApiUrl` + `getDecisionContract` added.
- **Builder skill** (`citra-app-spec`) — a "Headless mode" section: emit AgentSpec + stub AppSpec (`headless:true`, no panels/pages), skip the UI phases, hand over the decision API + contract.
- **Full disposition parity for the external UI — DONE.** The custom UI can do everything the SmartApp UI does: **approve / override / reject / cancel** via `/run` + `/approve` (override is constrained to the agent's `editable_fields`, produces the gold-label DecisionRecord), AND a true **direct assign with NO AI** via `POST /apps/{slug}/tool/{tool_name}` — which is now **headless-aware** (gated by the agent's tool dispatch table instead of panels, since headless has none) and emits the `human_direct` DecisionRecord. `/decision-contract` lists the action tool names + the `direct_assign` endpoint.
- **Integration contract + in-card test playground — DONE.** `/decision-contract` now returns the full integration spec: `request_schema` (the `/run` inputs), `run_actions` (values for the `action` field), `write_actions` (per action: `dataset_id`/table, `action_id`, `input_schema` payload, `editable_fields`), the `response_shape`, `approve_request` shape, and a worked `example`. The card's **"Test"** button opens a playground (`runDecision`/`approveDecision` + `getDecisionContract`): it surfaces the **API URL** + a **Copy-contract-JSON** button (the full integration spec), then edit the inputs JSON → **Run** (`/run`) → see decision + reasoning + `planned_writes` → **Approve / override (overrides JSON) / Reject / Cancel** (`/approve`) → see the committed result. A note states **Run is read-only (plan); Approve COMMITS**. So the BA tests the headless agent with no code, and the same screen is the developer's live I/O reference.

**Review (ease-of-use pass):** verified **publish accepts headless** (no `panels`/`pages`) — the AppSpec model validator was the only gate (fixed); `publish_validators` + `validators.py` just iterate empty panel lists (no-ops). Fixed in the test modal: contract+URL now copyable (M1), and the Run-is-plan / Approve-commits warning (M3). **Deferred:** a schema-driven *form* instead of raw-JSON inputs would make the tester truly BA-friendly (M2); Test is currently headless-only though the API works for any app (L1).
- **Governance invariant:** commit ONLY through these endpoints — `/run`+`/approve` or `direct_assign` — both run the governed write path (policy gate, idempotent SoR write, audit, DecisionRecord, outcome loop). The UI must NEVER write the SoR directly outside them. **Caveat:** an actual headless *build run* needs the live builder to confirm the LLM emits the stub correctly; the test playground is verified by compile/JSX-parse, not metro-run.
- **Model-swap demo (still to run live):** run the pilot against two `LLM_LARGE_BASE_URL` configs on the same grounding corpus and read `by_model` — decision agreement + good_rate parity across models is the headline result. The metric is built; the experiment is a live run. Nearly free given `llm_client.py`.

## Files to touch (grounded)
- `smart-app-service/main.py` — write `decision_records` on approve + auto-process; new outcome-poll endpoints/worker hook.
- `smart-app-service/models.py` — `DecisionRecord` model + `outcome_poll` config on AgentSpec.
- `smart-app-service/grounding_refresh.py` — ingest + weight decision records; incremental upsert.
- `smart-app-service/proxy_clients.py` — **DONE:** `call_dept_mcp_read()` structured read-by-key helper over the existing `POST /run_query`; refuses `kind="semantic"`. Does **not** use the NL `call_dept_mcp_query`.
- source-mcp-template — **no change needed**; `/run_query` already provides the structured, SELECT-only, audited read plane. (Earlier plan to add `read_record` dropped — would have forced a redeploy of every dept-MCP for no gain.)
- New: `smart-app-service/outcome_poller.py` (worker) + scheduler registration.
- `citra-app-runtime` — (optional, later) surface the override-rate/outcome metric on the dashboard panel.

## Effort & phasing
- **Step 1** (decision record + dual-path write): ~3–4 days.
- **Step 2** (outcome poller): ~4–5 days (most of the app-specific design).
- **Step 3** (write-back + weighting + auto-refresh): ~4–5 days.
- **Step 4** (proof + model-swap demo): ~2–3 days.
- Total ≈ 4 weeks on the pilot, single surface.

## Open design decisions (need pilot domain input before Step 2)
1. Confirm pilot = `route_complaint` (or name alternative).
2. Polling window `N` for the pilot.
3. Reversal definition — which source field/value = "bad".
4. Bad-outcome handling in grounding: hard-scrub vs. down-weight (recommend: down-weight + keep for "what not to do" later).
