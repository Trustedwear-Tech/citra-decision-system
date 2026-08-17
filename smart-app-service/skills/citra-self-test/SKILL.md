---
name: citra-self-test
description: Run synthetic test cases against a drafted AgentSpec and score them
metadata:
  category: citra
  tools: [exec]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra Self Test

## Purpose
Phase 2 (Expertise) — validate that the drafted agent actually does the job before publishing. Find prompt gaps, missing tools, and ambiguous instructions early.

## When to Use
- After drafting or editing `AgentSpec`.
- Before calling `citra-app-publish`.
- After the BA corrects a sample classification — re-run to confirm the fix sticks.

## When to SKIP (the one exception)
A **pure dashboard app** — every page is a read-only dashboard (narrator + chart/KPI panels, **no action tools / no writes**) — has no agent decisions to exercise, so there is nothing to self-test; skip it (AGENTS.md Hard Rule 7). The moment the app has **any** action tool (`mcp_action`, a write, a queue decision), self-test is mandatory again — no publish without a green run.

## Safety rules (citations)

Refer to [citra-safety-rules](../citra-safety-rules/SKILL.md) for the canonical rule list.

## Workflow

Narrate per [`AGENTS.md`](../../AGENTS.md). Self-tests can take 30–60s; the BA needs to see progress.

## Loop discipline — STOP and escalate; never fix the unfixable forever

Every step below is a *fix → re-run* loop. That loop is a trap when the failure is **not yours to fix** — a missing platform capability, an unimplemented feature, a 5xx, an MCP that has no place to store a file. Re-editing the spec then produces the **identical** failure, forever (the 90-turn hang). Govern every loop by these rules:

1. **Classify the failure before re-trying.**
   - **Fixable (spec)** — a bad `ref`, wrong column/field/filter, missing required input, prompt wording. Edit the spec, re-run.
   - **NOT fixable (requirements / platform)** — `ocr_not_configured`, "no write action", a write that exposes no column for the value, a 5xx / `app not found` / MCP or fallback error. These need an **IT/platform** change. **Do not edit-and-retry — escalate immediately.** (A `format:"file"` field on a plain string column is **fixable automatically** — the platform S3 fallback stores it — so it is **not** in this list; don't escalate file uploads.)
2. **Same error ≤ 3 attempts.** Count attempts per *distinct* failure. If the **same** error survives 3 fix attempts, treat it as unfixable from the spec — stop and escalate. (The `/builder/preview-smoke` gate enforces this server-side and returns `action`, `escalations[]`, and per-issue `class`/`fixable`/`attempt`/`escalate` — obey them.)
3. **Escalate = `requirements_unmet` + a plain BA message → contact IT.** Name the feature, the reason, and the specific IT ask; then **build and ship the rest of the app**. Do not let one unfixable gap block the whole build. Template:
   > ⚠️ One feature can't be completed from here: **<feature>**. <plain reason>. That's an IT/MCP change, not something I can fix in the builder. **Please ask IT to <specific ask>.** Everything else is built and ready in test.

### Steps 0a–0e — Pre-flight (BEFORE the synthetic test loop)

Run these FIRST. Each is cheap and catches a different class of bug that would otherwise only surface at runtime — when the officer clicks and gets a confusing error. If any of them fails, fix the spec and re-run from the start of step 0; do not proceed to the synthetic LLM tests until 0a–0d are green and 0e probes report ok=true.

> 🧪 Running pre-flight cross-spec checks before any LLM tokens...

**Step 0a-0d — Static cross-spec checks (Layer A of the test plan).** Invoke the harness once; it runs all four checks in one process:

```bash
python /workspace/.openclaw/workspace/builder-workspace/static_checks.py \
  > /workspace/build/static_check_results.json
```

**FAIL LOUD — do not skip this gate.** If `static_checks.py` is missing, errors,
or the run is non-zero, STOP and surface it to the BA ("my static-check harness
isn't available — I won't publish unvalidated"). Do **not** silently fall back to
"manual checks" and proceed: the static checks are a required publish gate, and
skipping them is how a hallucinated `mcp_action` or a dangling data source reaches
runtime. Same for schema validation in `citra-app-spec`/`citra-dashboard-spec`: a
missing schema file is a hard stop, never a reason to publish unvalidated.

The harness reads `app_spec.json`, `agent_spec.json`, and `catalogue.json` from `/workspace/build/`. It returns `{passed: bool, tool_catalogue_match: [...], form_validator_match: [...], panel_data_source_match: [...], workflow_staging_wiring: [...], neighbor_samples_collection_match: [...], code_exec_ocr_preflight: [...], record_passing_review: [...]}`. `record_passing_review` is **ADVISORY** — it does NOT flip `passed` (the `/builder/smoke-run` gate is the hard block); review and act on its warnings before publishing.

| Check | What it catches |
|---|---|
| `tool_catalogue_match` | Hallucinated `mcp_action` (source_id, dataset_id, action_id) triples — agent would 404 at runtime |
| `form_validator_match` | `validate_form` tools whose `schema_ref` doesn't resolve to a real FormPanel, or whose required fields are missing from the panel |
| `panel_data_source_match` | Panels referencing a data source that doesn't exist in `AppSpec.data_sources`, or using a panel type the runtime doesn't render |
| `workflow_staging_wiring` | AppSpec declares a `workflow_staging` data source (officer inbox) but nothing feeds it — no `agent_id` (on-demand recommendation) and no app trigger (precompute) — so the queue panel would render empty forever |
| `neighbor_samples_collection_match` | A `neighbor_samples` tool's `collection` isn't `Historical_Refresh` (the one shared grounding collection for all agents; rows isolated by `agent_id`) — agent retrieves from an empty/foreign collection and silently loses its grounding |
| `code_exec_ocr_preflight` | A `code_exec` tool missing its `description` prescription or `allowed_outputs` gate; or `vision_ocr` declared without a preceding `validate_form` tool / no `validate_form` mention in the system prompt (cost-gate ordering) |
| `record_passing_review` (advisory) | An agent action fired from a queue button reads the SAME source the queue already passes into its `inputs` — risk of re-fetching the provided record (slow/looping runs). Use the record from `inputs`; read that source only for OTHER rows. Doesn't block; the `smoke-run` gate enforces. |

If any check returns a non-empty list, surface the offenders to the BA in plain language and stop:
> ❌ Pre-flight failed: the action `dispatch_crew` references `outage_management.outages.update_outage_status_v2` but the catalogue only has `update_outage_status`. Likely a stale action id — re-run discovery to refresh.

**Step 0e — Live tool probe (Layer B of the test plan).** For every entry in `agent_spec.tools_v2` whose kind is one of `mcp`, `rag`, or `mcp_action`, hit `POST $SMART_APP_SERVICE_URL/builder/probe` with the tool's catalogue-pinned identifiers. The endpoint runs the real call shape — read-only for `mcp`/`rag`, `dry_run=true` for `mcp_action` (never commits to source).

**For `mcp_action` probes you MUST supply a `payload` field that satisfies the action's `input_schema`.** The MCP runs full schema validation even under `dry_run=true`; an empty `{}` returns "field required" and looks like a tool failure when it's really a probe-input failure. Construct the synthetic payload from the catalogued `input_schema` (`catalogue.json`), **respecting each field's column `type`/length**: for a string field use a SHORT token that fits the column (a `varchar(20)` must be ≤20 chars — prefer `PRB001` over `PROBE-<field>-001`, which can overflow and 500 on a real write); for an enum/status field use a **real value from the column's `distinct_values`** (not an invented token); for numbers use `1`, for booleans use `false`. The MCP's authz + schema run on this synthetic payload identically to a real officer click.

```bash
# EXAMPLE shape only — substitute YOUR action's source_id / dataset_id / action_id
# and payload columns+values from /builder/catalogue + /builder/sample (the
# outage_management / status:"restored" values below are one app's catalogue, not a standard).
curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/probe" \
  -H "Authorization: Bearer $CITRA_JWT" \
  -H "Content-Type: application/json" \
  -d '{"kind":"mcp_action","source_id":"<source_id>","dataset_id":"<source_id>.<dataset>","action_id":"<action_id>","payload":{"<key column>":"<a real id>","<status column>":"<a real value from distinct_values>"}}'
```

Expected: `{"ok": true, "elapsed_ms": <int>, "sample_result": {...}}`. Anything else means the tool will not work at runtime — same fix-and-rerun loop as 0a-0d:
> ❌ Probe failed for tool `dispatch_crew`: 403 role denied. The action declares `roles_allowed_write=[dept_admin]` but the build pod's JWT carries only `[user]`. Add a probe-only system role or wait until preview deploy to exercise this path.

Narrate each probe attempt + outcome — the BA should see the build proving each tool works, not just trusting the spec.

### Step 0f — Write-validation (Layer D — execute writes against TEST data)

A `dry_run=true` probe (0e) proves a write's SHAPE (authz + schema), not its EFFECT. Because the builder runs in the **test environment**, you can execute a declared write for real against test data and confirm the effect. But a real write hits the source DB's **constraints** (FK, UNIQUE, NOT NULL, column length, enum/CHECK) — which `dry_run` does NOT check — so HOW you validate depends on the action's verb:

**UPDATE / upsert actions → execute‑validate against a REAL sampled row (preferred).**
A synthetic PK/FK won't exist, so target a real row:
1. `POST /builder/sample` the action's dataset, take a **real primary-key value** from a returned row.
2. Build the payload from that real key + change ONE state/status field to a **real value from the column's `distinct_values`** (per `citra-mcp-discover` — never a fabricated status).
3. `POST /builder/probe` with `execute:true`. Then read the row back and assert the field changed.
```bash
# EXAMPLE shape only — substitute YOUR catalogue's source_id / dataset_id / action_id
# and payload columns+values (the field_operations / recovery_status values are one
# app's catalogue, NOT platform standards). The key MUST be a real sampled id.
curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/probe" \
  -H "Authorization: Bearer $CITRA_JWT" -H "Content-Type: application/json" \
  -d '{"kind":"mcp_action","source_id":"<source_id>","dataset_id":"<source_id>.<dataset>","action_id":"<update action id>","payload":{"<key column>":"<REAL id from /builder/sample>","<status column>":"<a real value from distinct_values>"},"execute":true}'
```

**CREATE / insert actions → dry_run only; do NOT execute‑validate with synthetic data.**
A fabricated insert almost always violates a real constraint — a foreign key to a consumer/row that doesn't exist, a UNIQUE primary key, or a value too long for the column (e.g. `PROBE-consumer_id-001` is 21 chars and overflows a `varchar(20)` column → a 500 `StringDataRightTruncation`). The 0e `dry_run` already proved the shape. So **stop at dry_run for creates**, and record in `requirements_unmet`: *"<action> create not execute-validated (would insert synthetic data violating source constraints) — shape verified via dry_run."* Don't force a real create.

**Constraint‑respecting values (both cases):** read each field's `type` from the catalogue and keep synthetic strings **within the column's length** (a `varchar(20)` value must be ≤20 chars — prefer a short `PRB001`-style token over `PROBE-<field>-001`); use a real `distinct_values` member for any enum/status field; use `1` for numbers, `false` for booleans.

- **`execute:true` is REFUSED (409) outside the test environment.** Note it to the BA and fall back to the 0e dry-run only; do NOT treat the 409 as a tool failure.
- **A failed `execute:true` is NOT edit-and-retry-forever (HARD RULE).** If a real write returns a 500 / constraint error / 4xx, retry **at most once** (e.g. with a shorter value or a real key); if it still fails, **record the gap in `requirements_unmet` and PROCEED** — `dry_run` already proved the shape, so a blocked real-write is a note, not a build-stopper. **Never loop on it**: a write-validation retry loop burns the build's LLM token budget and the whole build gets cut off mid-flight. One failed write must cost ~1 extra call, not dozens.
- **Cleanup is by reseed.** Committed test rows stay; IT/ops reseeds test data between runs. Never author a "reverse"/"undo" write to clean up. Use clearly-synthetic identifiers so a human can spot validation artefacts.

Narrate plainly: *"Ran your `update_outage_status` write for real against the TEST `outages` table — the row's status changed to `restored`. This only ever touches test data, never your real systems."*

### Step 1 — Synthetic + sample-data test cases

1. **Narrate** before generating cases:
   ```
   > 🧪 Generating synthetic test cases — happy path, edge, and negative...
   > ✅ 8 cases ready (3 happy, 3 edge, 2 negative)
   ```
   Generate **5–10 synthetic test cases** that cover:
   - Happy path (textbook claim, standard invoice, etc.).
   - Edge cases (amount near approval threshold, missing fields, unusual category).
   - Negative cases (clearly invalid, fraud signals, out-of-policy).

   **Then augment with 3–5 real source-data samples (Layer C).** Synthetic cases are sanitised; real source data has nulls, mixed encodings, vendor-specific codes that synthetic generation misses. For each of the agent's primary read datasets, pull a small sample:

   ```bash
   # Substitute YOUR source_id / dataset_id from /builder/catalogue (the
   # outage_management values are one app's catalogue, not a standard).
   curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/sample" \
     -H "Authorization: Bearer $CITRA_JWT" \
     -H "Content-Type: application/json" \
     -d '{"source_id":"<source_id>","dataset_id":"<source_id>.<dataset>","limit":3}' \
     > /workspace/build/sample-<dataset_id>.json
   ```

   Append each sample row to `tests.json` with `id: "sample-<dataset>-<n>"` and an `expected` block you derive from the row itself (e.g. for a row with `status="active"` and `assessed_amount > 1L`, expected outcome should be a high-severity classification). What the builder receives here is what the agent will see at runtime.

   Narrate explicitly: *"Tested against 8 synthetic cases plus 4 real samples from your `outages` table — every Approve in self-test is dry-run, nothing writes to source."*

2. Save them at `/workspace/build/tests.json`. The `expected` block asserts the
   **structured decision** — the machine-parsable output keys the agent emits
   (`outcome`/`status` and any decision flags like `open_case`, `file_fir`).
   Assert ONLY structured fields:
   ```json
   [
     { "id": "t1", "input": {...}, "expected": { "outcome": "approve", "open_case": true } }
   ]
   ```
   `reasons_contain` is OPTIONAL and ADVISORY only — never a pass/fail criterion
   (see Scoring). If you include it, treat a miss as a note, not a failure.
3. **Narrate** before running the suite:
   ```
   > 🧪 Running the test suite against your agent — this takes ~30s for 8 cases...
   ```
   There is **no `citra-builder` CLI** — you run the suite yourself. Write a
   short Python harness to `/workspace/build/run_selftest.py` and execute it
   with `exec`. For each case in `tests.json`, make ONE chat-completion call
   against the LLM proxy (the OpenAI-compatible endpoint is already in the
   pod env as `LLM_LARGE_BASE_URL` + `LLM_LARGE_API_KEY`; the SDK appends
   `/chat/completions`). Build the request from the AgentSpec:
   - `messages[0]` = `{"role": "system", "content": agent_spec["system_prompt"]}`
   - `messages[1]` = `{"role": "user", "content": json.dumps(case["input"])}`
   - Ask the model to reply with a strict JSON object (e.g.
     `{"status": "...", "reason": "..."}`) so the result is machine-parsable.

   **Set `max_tokens` HIGH — at least 4096, and 8192+ for a reasoning model.**
   `max_tokens` is a **ceiling, not a target**: the API bills only for the tokens
   actually generated, so a high ceiling costs nothing extra — it just prevents
   truncation. Setting it low has no upside and one big downside: a truncated
   response is invalid JSON, which raises `JSONDecodeError` / `'int' object is not
   subscriptable` and **looks like an agent failure when it's really a token‑limit
   failure** (1024 truncated real reasoning+verdict; even 2048 is tight for a
   reasoning model whose thinking tokens count against the limit). The self‑test
   prompt is small, so there's ample context room — be generous. Wrap the parse in
   `try/except` and record `verdict: "ERROR"` with the exception text rather than
   crashing the run; a truncation/parse ERROR is **not** an agent failure — raise
   `max_tokens` and re‑run, never edit the test to "fix" it.
4. **Emit a finding** with the score:
   ```
   > ✅ 8/8 passed
   ```
   or, on failure:
   ```
   > ⚠️ 6/8 passed — 2 edge cases failed; iterating on the agent...
   ```
   Score each output: pass / fail / partial. Save to `/workspace/build/test-results.json`.

   **Score on the structured DECISION, not on reasoning wording.** A case PASSES
   when its decision fields match `expected` — even if the agent worded its
   reasoning differently than you imagined. Synonyms ("repeat tampering" vs
   "multiple tamper events", "no anomaly" vs "readings normal") are **not**
   failures. Matching free-text reasoning phrases is brittle: it manufactures
   false failures that make you churn the system prompt chasing wording instead
   of behaviour. Only the decision is authoritative. (Iterating the prompt to fix
   a genuinely WRONG decision — e.g. recommending re-processing a case that's
   already closed — is correct and expected; iterating because the reasoning didn't contain
   your exact phrase is the anti-pattern to avoid.)
5. If failures > 0 **on the decision**, **iterate the AgentSpec** — adjust system prompt, add a sub-agent, refine an action's input_schema. Re-run (re-narrate the iteration so the BA sees the loop). A wrong-wording-but-right-decision case is already passing — do not "fix" it.
6. When BA-corrected samples (Phase 2 BA review) come in, append them to `tests.json` so future re-runs hold the fix.

## Pass bar
- Happy path: 100% must pass.
- Edge cases: ≥80% pass.
- Negative cases: must reject correctly with a clear reason.

## Iteration cap — HARD STOP at 3 (do not loop forever)
Self-test is a **gate, not a goal**. Each re-run is N live LLM calls, so an
unbounded loop silently burns the whole build budget and the BA never gets a
published app. Enforce a hard cap:

- **Run the suite at most 3 times.** After the 3rd run, STOP iterating —
  whatever the score is, move on.
- **The LLM is non-deterministic.** The same edge case can return `monitor` on
  one run and `auto_acknowledge` on the next. That is NORMAL — it is **not** a
  failure to chase. Do NOT keep editing `tests.json` and re-running to force a
  flaky edge case green; note it as non-deterministic and leave it.
- After 3 iterations, if **happy-path is 100%**, proceed to publish and surface
  the remaining edge/negative gaps to the BA in plain language ("2 edge cases
  are borderline — the model is split on them; I've flagged them, want me to
  tighten the rule or ship as-is?"). If **happy-path still fails**, stop and
  surface that the goal needs clarification or a new data source — do not keep
  burning the budget.
- Only edit `tests.json` to fix a WRONG expectation (you mis-derived it), never
  to chase a non-deterministic decision. Editing tests to make them pass is the
  anti-pattern this cap exists to stop.

### Do NOT rationalize failures away (anti-gaming — read this)
When a case fails, **do not default to "my test was wrong, the agent is right."**
Classify the failure honestly and act per the class — never relax a test just to
turn it green:
- **Mis-derived expectation** — the spec's own rules say X, the agent did X, and
  your test wrongly expected Y. *Then* fix the test (e.g. a rule says "clear
  tamper + medium severity → open case" and you expected acknowledge — the test
  was wrong). Cite the rule you're conforming to.
- **Real disagreement** — the agent's decision conflicts with the goal, a rule,
  or safety. **Fix the AGENT (system prompt / rules) or surface it to the BA —
  do NOT loosen the test.**
- **NEGATIVE cases are sacred.** A "should NOT act" case that the agent *acted*
  on (recommended a write / open / update) is a **real failure** — never relax
  the negative case to accept the action. If you believe the action is actually
  correct, that's a BA decision: surface it ("the agent wants to write-off this
  no-anomaly case — is that the behavior you want?"), don't silently green it.
- **A write recommended in a borderline/negative case is a RED FLAG**, not a sign
  the agent is "smart." Writes are governed + sensitive — flag them to the BA;
  do not normalize them by editing the expectation.
The BA's corrections are authoritative — your own rationalization is not.

## BA-friendly output
Show the BA a sample table of "input → agent decision → was it right?" — never raw JSON. Accept their corrections as authoritative and re-run.

## Hard Rules
- Never publish without a passing self-test.
- Never silently lower the pass bar to make tests green.
- Every BA correction becomes a permanent test case.
