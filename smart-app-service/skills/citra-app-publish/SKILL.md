---
name: citra-app-publish
description: Validate and publish AppSpec + AgentSpec to smart-app-service
metadata:
  category: citra
  tools: [bash]
---

# Citra App Publish

## Purpose
Phase 4 (Deploy) — hand off the validated AppSpec + AgentSpec to `smart-app-service`, which persists them, returns the runtime URL, and (in later phases) triggers a runtime pod warm-up.

## When to Use
- After `citra-self-test` passes.
- After the BA gives final approval in plain language ("looks good, ship it").

## You build and ship in TEST — that is the whole job

You are **always running in the test environment** — smart-app-service points source
reads/writes at the test MCPs and routes your work to the `test_`-prefixed collections
automatically. You do **not** know or set the environment; it is server-side. So:

- **Publish lands in test.** A `/publish` persists the app + agent and resolves data against the
  **test MCPs**. The BA's clicks, the queue, and the audit are all in test.
- **Write-validation runs in test.** `citra-self-test` step 0f executes each declared write for
  real against test data (`/builder/probe` with `execute:true`).
- **Your job ends at the test URL.** Build → validate in test → hand the BA the test URL → STOP.
  There is nothing beyond test for the builder to do.

## Endpoint
```
POST ${SMART_APP_SERVICE_URL}/publish
```
Body:
```json
{
  "session_id": "<build-session-id>",
  "app_spec":       <object — full AppSpec, REQUIRED>,
  "agent_spec":     <object — full AgentSpec>
}
```

`app_spec` + `agent_spec` are what you publish. Any automation is an **app
trigger** declared inside `app_spec.triggers[]` (see `citra-app-spec` → Triggers),
and it publishes **deactivated** with the app (the officer activates it in the app's
**Auto-Recommend** panel).

### Narrate WHAT you are publishing — before and after

**Before** the POST, tell the BA in one or two plain sentences exactly what is
going live — the app, its pages, and any AI triggers (with what each does). Example:
> 📦 Publishing **Daily Operations Briefing** — a 4-page app (a dashboard page
> with KPIs + charts, an Operations queue page, a policy library, and an
> assistant), plus 1 **AI trigger** *Intake Triage* that polls new records and
> stages a recommendation for the officer to approve. The trigger goes live
> **inactive** — you activate it in the app's **Auto-Recommend** panel after review.

**After** a 2xx, the response describes what went live (pages, panels, and any AI
triggers). Triggers are **published INACTIVE** — relay that to the BA along with the
URL, so they know to activate a trigger in the **Auto-Recommend** panel before it runs.

## Safety rules (citations)

The `/publish` endpoint validates server-side and, on rejection, returns HTTP **422** in one of two body shapes:

```json
// (a) safety-rule pack — one rule, one pointer
{ "detail": { "code": "<RULE_ID>", "message": "<human reason>", "path": "<json.pointer>" } }

// (b) data-binding / spec-resolution validators — a top-level code + a list
{ "detail": { "code": "<GROUP_CODE>", "message": "<human reason>",
              "errors": [ { "code": "E_…", "action": "data_source=ds_x", "expected": "…", "hint": "…" } ] } }
```

For BOTH shapes the recovery is the same: narrate the block to the BA in plain language, **re-author the spec to fix it, and retry `/publish`**. Read `detail.errors[]` when present — each entry names the offending element (`action`) and often carries the exact fix (`expected`, `column`, `dataset_id`, `hint`). Apply those verbatim.

```
> ❌ Publish blocked ({code}): {message}. {one-line fix}
> 🔧 Re-authoring the spec to fix it...
```

Coverage includes (non-exhaustive — refer to citra-safety-rules for the authoritative list):

- **W-01 / W-02 / W-03 / W-05** — write-action catalogue-pinning + severity
- queue-action `plan_then_apply` defaults (see citra-safety-rules for the authoritative rule ids — the A-rule numbering lives there)
- **H-01 / H-02 / H-04** — universal approval (every write pends; no per-action `approval_required` knob — `hitl_policy.approvers` only restricts WHO may Approve); chat-surface tool-kind restrictions; `allow_writes_in_chat` forbidden
- **D-01 / D-02 / D-04** — dashboard narrator tool-kind restrictions; data-source scope
- **C-03 / C-04 / C-05** — trigger cron floor, loop bounds, fan-out caps
- **L-01 / L-02** — autonomy is the BA's choice per domain (no domain is refused); writes flow through the human queue or the BA's deterministic, fail-closed auto-process policy
- **S-01 / S-03** — audience scope; no secrets in spec
- **T-03** — `admin_only` actions not exposed
- **Sub-agent tool subset (no rule-id)** — a sub-agent's `tools_v2[]` is a strict subset of the parent's (enforced by the publish validator; not a citra-safety-rules entry — X-04 there is the mcp_action allowlist rule)

**Data-binding / spec-resolution validators** (shape (b) above — these reject specs that point at data that doesn't exist; they are the most common cause of a "UI renders but no data" app, so treat them with the same narrate→fix→retry discipline):

- **`data_source_ref_unresolved`** — a `type:"mcp"` `data_source.ref` doesn't resolve to a real catalogue dataset.
  - `E_MALFORMED_DATASET_REF` — the ref has a `/` (e.g. `field_operations/field_operations.complaints`). **Fix:** set `ref` to the value in the error's `expected` field — the catalogue `dataset_id` verbatim (`<source_id>.<table>`). Never prefix it with the source_id and a slash.
  - `E_UNKNOWN_DATASET` — the ref's dataset isn't in the catalogue (wrong source or invented table). **Fix:** use a `dataset_id` returned by `/builder/catalogue` for this tenant.
- **`panel_columns_unknown`** (`E_UNKNOWN_PANEL_COLUMN`) — a dashboard/chart/queue panel references a column not on its dataset (hallucinated). **Fix:** use only the columns the catalogue lists for `dataset_id` (named in the error); correct the metric `field`, `filter` key, chart `x`/`y`, `compare`/`trend` `date_field`, or queue column.
- **data_bindings 422** — an AgentSpec action read/write references an unknown dataset/column/action. **Fix:** correct it to a catalogue dataset/column or a declared action name.

### Retry discipline (uniform, all 422s)
- Re-author and retry **at most twice per distinct error `code`** (so **3 attempts max** for the same code: the original + 2 fixes).
- If the **same `code`** comes back a **third** time, **stop and ask the BA** — narrate what's blocking and that two automated fixes didn't clear it. Never loop.
- A **different** `code` on retry is forward progress (one error cleared, another surfaced) — its own count starts fresh. Keep going until clean or a single code exhausts its 3 attempts.

Refer to [citra-safety-rules](../citra-safety-rules/SKILL.md) for the canonical safety-rule list.

## Workflow

Narrate per [`AGENTS.md`](../../AGENTS.md). Publish is the moment of truth — the BA should see every step.

> 🚨 **ONE UNBROKEN TURN — publish → smoke‑test → hand over the URL all happen in THIS SAME turn. Do NOT stop in between.**
> This is the #1 failure mode: the agent publishes, writes *"now I'll run the smoke test…"*, and **ends its turn** — leaving the BA staring at a published app with **no URL and no result**. NEVER do this.
> - After `/publish` returns 2xx, your **VERY NEXT action is the smoke‑test tool call** (step 3) — NOT a message that ends your turn. Don't narrate "next I'll smoke‑test" and stop; actually call it.
> - The URL is still **withheld until the data smoke gate passes** (a URL that doesn't render wastes the BA's time) — but the *withholding* is not a stopping point. You keep going: smoke‑test → (fix & re‑publish if needed) → visual review *if that tool is available* → then share the URL. A missing visual-review tool never blocks the share.
> - **Your turn is complete ONLY when you have either (a) handed the BA the test URL (smoke passed), or (b) escalated an unfixable gap to the BA (and shared the URL for the rest of the app).** If the app is published, your turn does **not** end until the URL — or an escalation — has been delivered. A published app with no URL handed over is a BUG you are causing.
> - **The "live at <url>" handoff is mandatory visible output.** It is a normal prose message to the BA — never suppress it, never replace it with silence or an empty/`NO_REPLY`-style turn, and never assume "nothing to say." Even if you feel the build is finished, the BA has not seen the URL until you actually type it. Emit it.

> Failure alerts are automatic: if a published app's scheduled/poll trigger ever
> fails, Citra emails the BA who published it — stamped server-side from the login.
> You do not ask the BA for an alert address, and you do not set one. If the alert
> itself can't be delivered, that's logged as an error and caught by observability.

1. **Narrate** the pre-flight:
   ```
   > 🔍 Loading the agent spec and app spec for publish...
   > ✅ Specs loaded
   > 🔍 Re-validating locally before sending to the service...
   > ✅ All validators pass
   ```
   Load both specs from `/workspace/build/`. Re-validate locally one final time (the service will reject otherwise — fail fast here).
2. **Publish to TEST.** The app + agent land in the test store (`test_` collections), `audience=owner` (only the BA sees it — the service forces this for test publishes), resolving data against the **test MCPs**. Writes COMMIT against test data (not dry-run) so the BA can exercise the app end-to-end. You do **not** pass a `mode` — the service routes you to test automatically. Narrate:
   ```
   > 🚀 Publishing your app to the test environment so you can try it on test data...
   ```
   POST to `/publish`:
   ```bash
   curl -sS -X POST "$SMART_APP_SERVICE_URL/publish" \
     -H "Authorization: Bearer $CITRA_JWT" \
     -H "Content-Type: application/json" \
     -d @<(jq -n --slurpfile a /workspace/build/app_spec.json \
                 --slurpfile g /workspace/build/agent_spec.json \
                 --arg s "$BUILD_SESSION_ID" \
                 '{session_id:$s, app_spec:$a[0], agent_spec:$g[0]}')
   ```
   The response carries the slug + URL. Do **not** announce it to the BA yet — smoke-test it first (next step). **But do NOT end your turn here:** capture the URL and immediately continue to the smoke test below. Publishing and smoke-testing are not separate turns — your next action is the `preview-smoke` call, not a "now I'll test it" message that stops.

3. **Smoke-test against test data — DO NOT share the URL until it passes.** Publishing "successfully" only means the spec is structurally valid; it does NOT mean the app renders. A bad `data_source.ref`, a null KPI, or a hallucinated chart column produces a blank/wrong app that still "publishes". Prove every panel renders against test data BEFORE handing over the URL:
   ```
   > 🧪 Smoke-testing your app against test data before I share it...
   ```
   ```bash
   curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/preview-smoke?slug=<slug>" \
     -H "Authorization: Bearer $CITRA_JWT"
   ```
   The report shape: `{passed, action, guidance, escalations:[…], checked, failed, warnings, panels:[{id, type, status, issues:[{severity, msg, likely_fix, class, fixable, attempt, escalate}], data}]}`. Each panel's **`data`** carries the ACTUAL values the API returned — for a dashboard, `metric_values:[{name,value,delta}]`; for a chart/queue, `columns` + a 3-row `sample`. **Read it and sanity-check the computation against intent** — vision only sees pixels, this is the numbers. E.g. an "Open complaints" tile showing `3` when the source clearly has ~1,300, or a "by category" chart whose `complaint_id` (the count) is `1` on every row, is a **computation bug to fix** even if no validator flagged it (wrong filter, wrong `field`, wrong `aggregation`). Treat an implausible value as a `fail`.

   **The top-level `action` tells you what to do — obey it; do NOT loop.**
   - **`action: "ok"`** (`passed: true`) → every data panel resolved and every form's submit dry-ran clean. Proceed to step 4. You MAY mention `warnings` to the BA in plain language (e.g. "the outages-by-scope chart is currently empty").
   - **`action: "fix_and_retry"`** → there are **`fixable` (`class: "spec"`)** failures — a bad `ref`, a missing chart column, all-null KPIs, a wrong filter. Fix the spec using each issue's `likely_fix`, **re-publish** (step 2 — it upserts the same slug, no version churn), and **re-run the smoke test**. The gate counts repeats per distinct issue (`attempt`); after **3 attempts** an unconverged issue auto-flips to `escalate` (below). Do **not** give the BA the URL yet.
   - **`action: "escalate_to_BA"`** → the `escalations[]` list holds issues you **CANNOT fix by editing the AppSpec** — `class: "requirements"` (a real capability gap: OCR not configured, no write action, a write that exposes no column to record the value) or `class: "platform"` (a 5xx / `app not found` / MCP or fallback error) or `class: "spec_exhausted"` (the same spec error failed 3×). **STOP. Do not edit-and-retry these — that is the infinite-loop trap.** For each escalation: move the affected feature to `requirements_unmet`, then tell the BA in plain language (see template). **Build and ship the REST of the app** — don't let one unfixable gap block everything.

   **`requirements`/`platform` issues are never yours to fix by looping.** Re-editing the spec produces the identical failure forever. Escalate the first time you see `fixable: false`. **Note — file uploads are NOT a requirements gap:** a `format:"file"` field bound to a plain `string` column is handled by the **platform S3 fallback** (it stores the blob and writes a ref string), so it succeeds — do NOT escalate it. Only escalate a file if the gate reports a `platform` fallback failure (`CITRA_SERVICE_URL` unset / blob endpoint down) or the write has **no column at all** to hold a reference.

   **BA escalation template** (use the specific `likely_fix` reason):
   ```
   > ⚠️ I built your app, but one feature can't be completed from here:
   > **<feature, e.g. photo OCR auto-fill>**. <plain reason — e.g. "OCR is not
   > enabled on this deployment", or "the <action> write exposes no field to
   > record this value">. That's an IT/platform change, not something I can fix
   > in the app builder. **Please ask IT to <specific ask — enable OCR / expose
   > a column / fix the MCP>.** Everything else is built and ready in test: <URL>.
   ```
   ```
   > ❌ Smoke test failed: panel `complaints_trend` — data source did not resolve (`ref` is malformed). 🔧 Fixing the ref and re-publishing...
   ```
   Treat a `fail` here as blocking exactly like a 422 from `/publish`. A URL handed to the BA must be one you have *seen render* via this gate.

3b. **Behavior gate — actually RUN each agent action (REQUIRED when the app has an agent).** Panel-render smoke proves the app *displays* data; it does NOT prove the agent *behaves*. Call:
   ```bash
   curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/smoke-run?slug=<slug>" \
     -H "Authorization: Bearer $CITRA_JWT"
   ```
   **Give this `exec` a generous timeout (≥ 240s).** The gate runs each action's
   full agent loop and the server bounds each to 60s — so a multi-action app can
   legitimately take a few minutes. If your `exec` kills curl early you get a
   transport timeout with **no `runs[]` detail** (looks like "smoke returned no
   result"); that is a too-short client timeout, NOT a gate failure — re-run with
   a longer `exec` timeout, do not treat it as a spec error. A genuinely
   slow/looping run now comes back as a graded `status:"fail"`, `run_status:"timeout"`
   with the `likely_fix` — fix the AgentSpec per that, don't just keep extending the timeout.
   It fires **each agent action once on a real test record** (the exact inputs a queue-button click sends — the whole row) and grades the run trace. Report shape: `{passed, checked, runs:[{action, status, run_status, tool_calls, by_tool, duration_s, issues:[{severity,msg,likely_fix}]}]}`. A `status:"fail"` run means the agent **errored, looped, re-queried a record it was already handed, or ran too long** — the class of defect no render check can see (e.g. `read_tamper_events ×5` for one case, `9 tool calls (cap 6)`). Fix using each `likely_fix` — almost always the `system_prompt` re-fetches the provided record or reads aren't scoped (see `citra-agent-spec` "Write FAST system prompts"), re-publish, and **re-run this gate** (≤3 attempts per distinct issue, RULE #1). **A looping agent is a real defect — do NOT hand over the URL until this gate passes.** (No agent bound → it returns `passed:true, checked:0` and you skip straight on.)

3c. **Render gate — `citra_visual_review` is a HARD gate (when available).** Data resolving (3) and the agent running (3b) do NOT prove the page actually *renders* — a spec can be data-valid yet the page **throws a 500 on render** or comes up blank/garbled, and neither the data gate nor the behavior gate can see that (they don't load the page in a browser). So the page render is its own required gate. The **`citra_visual_review` tool** renders each page in the headless-browser render service and a vision model reports what a human sees. Call it **per page, AFTER your LAST edit** (re-publish first if you changed anything since the previous review — never ship a version newer than your last review), with the plain page URL (no `?_t=` — the tool mints its own token):
   ```json
   {"tool": "citra_visual_review",
    "args": {"url": "<page_url>", "context": "<what this page should show>"}}
   ```
   Result: `{passed, overall_ok, issues:[{severity, area, description, likely_fix}]}`.
   - **A render-RESULT failure BLOCKS the URL** — the page returned an **error/500**, is **blank**, shows **error text**, or every tile is **"—"**. This is a real defect exactly like a data-smoke `fail`: fix using `likely_fix`, re-publish, **re-render every page** (≤3 attempts per distinct issue, RULE #1). **Do NOT hand over the URL while any page fails to render.**
   - **Only a tool/infra ERROR is soft** — render backend unreachable / timeout / `RENDER_SERVICE_URL not configured` (the tool itself couldn't run). You cannot gate on infra you can't reach: do NOT loop, note to the BA in one line that the automated render check couldn't run so they should eyeball the pages, and proceed. **Distinguish a render *result* of fail (BLOCK) from the *tool* being unreachable (soft) — they are not the same.** If the tool is absent from your surface entirely, treat as the infra case (soft).

4. **Hand the test URL to the BA, then STOP.** After the smoke gate passes, tell the BA in plain prose (no `>` prefix). **Phrase it as "live at \<url\>" with the URL immediately after "live at"** — the chat UI detects that exact pattern to render the prominent "Open app" button that opens the app WITH the BA's auth token (a URL the BA copies/pastes without the token 401s). Template: *"Your app is **live at <url>** in the test environment — I've checked all <N> panels render against test data, and validated each write against test data. Open it and try it end-to-end; every action here writes to TEST data, never your real systems."* The test URL is the end of your job (W-07).

   After the URL is delivered, **stay available — do NOT declare the build "done" or imply the pod is shutting down.** The session stays open; the BA almost always responds next, one of two ways:
   - Be happy → they take it from there themselves (you take no further action, but remain ready in case they change their mind).
   - Request changes → go back to Phase 3 / 3.5 / self-test, regenerate, re-publish to test (it upserts the same slug — no version explosion), then re-run the smoke gate (step 3). This is the **normal** path; treat a change request as expected, not as the build being over.

### The data smoke gate is never optional

Every publish lands in the **test** store, and the builder's job ends at the test URL. Before
handing the BA the URL you must pass **three gates**, none optional when applicable: the **data
smoke gate** (3, panels resolve), the **behavior gate** (3b, agent runs clean — when there's an
agent), and the **render gate** (3c, every page actually renders). A URL that doesn't render — or
renders blank, or whose agent loops — wastes the BA's time and erodes trust.

The render gate (`citra_visual_review`) is **HARD on a render result of fail** (page 500 / blank /
error / all-"—" tiles → block + fix + re-render, ≤3) and **soft only when the tool itself can't
run** (backend unreachable / not in your tool surface → note it and proceed; you can't gate on infra
you can't reach). Always re-render **after your last edit** — never hand over a version newer than
your last passing review. Depth varies — a text-only edit (label/title) needs only a quick
re-render; a new data panel needs the full data gate + render gate (+ behavior gate if it touches
an agent).

## Versioning
- First publish: `version = 1`.
- Re-publish (same `slug`): server increments and keeps the previous version queryable.
- Treat publish as **append-only**. Never delete history.

## On error
- 400 = schema validation failed → re-run the validator locally and surface the field path to the BA.
- 401 = auth → the build session needs a fresh JWT.
- 409 = slug collision with a different owner → ask the BA for a new slug.

## Hard Rules
- Don't publish without a passing self-test.
- Don't change the `slug` after first publish — that breaks links and history. Use `citra-app-edit` for in-place changes.
- Never include secrets or API keys in either spec.
