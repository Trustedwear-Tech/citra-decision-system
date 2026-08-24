<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-app-spec — Write paths, approval queue & triggers

Read this when the app changes a source system: the canonical approval queue
(AI-recommendation flow), direct user writes (no LLM), and precompute triggers.

## STEP 1 (do this FIRST for any decision/recommendation app): present the MODE options — NEVER default silently
The BA usually doesn't know these choices exist. Before building, **ASK** — present all three modes in plain language and let the BA pick. Do NOT silently default to auto-recommend.

> **"How should the AI handle each <item>? Pick one:**
> 1. **On-demand** — you open the app and click to get the AI's recommendation for an item when you want it.
> 2. **Auto-recommend (pre-processed)** — the AI reviews each new <item> automatically (on a schedule / as they arrive) and queues a recommendation; you just review the queue and approve/forward. Saves the per-item triggering — you still decide each one.
> 3. **Auto-process** — the AI decides **and acts on its own** (no human click), within limits you set. Fastest; the AI commits.
>
> If you choose **auto-process**, what limit should it act within? For *this* app I'd suggest something like **`<goal-specific suggestion>`** — e.g. claim approvals: `amount < 10000`; routing: only the known teams; screening: objective knockouts — and anything outside that queues for your review. Or, if you want it to handle **everything with no limit**, tell me and I'll set that up (highest-autonomy mode)."

**Suggest the bound from the GOAL** — infer a sensible default and offer it; don't make the BA invent it cold. Then **let the BA think and reply**, and build exactly what they choose (NO defaults):
- **On-demand** → no scheduled/auto trigger; a `user_action` trigger / a panel button runs the agent on click.
- **Auto-recommend** → a `schedule`/`poll`/`webhook` trigger with `execution_mode: "recommend"` (proposes → stages to the queue).
- **Auto-process + a bounding criteria** → `execution_mode: "auto_process"` with `auto_process_policy.auto_commit_when` = that criteria (see **Auto-process** below).
- **Auto-process + "all, no limit"** (explicit BA choice) → `auto_commit_when: { "always": true }` + tell them it's unbounded (still capped by the safety ceiling/confidence).

If the BA already stated the mode in their request (e.g. "auto-approve claims under 10000"), skip the menu and just shape-confirm that one. Otherwise present the menu and resolve before building.

## Canonical approval shape: ONE recommendation queue

Every SmartApp that needs officer decisions has **one queue**, backed by **one collection** (`smartapp_workflow_staging`), surfaced by a `workflow_staging` data source. The same queue holds recommendations no matter how they were produced — both are the app's OWN agent (no workflow engine):

- **Eager (precomputed by an app trigger)** — a `schedule`/`webhook`/`poll` trigger ran the agent ahead of time and staged the row into the queue (see **Triggers**).
- **Lazy (on-demand)** — the agent runs when the officer opens an item and `/apps/{slug}/run` writes the **same** staging row into the **same** queue.

There is **no `approve_claim` / `reject_claim` agent action to declare** — the Approve is handled **server-side** (`_approve_workflow_staging` replays the row's `planned_writes`, applying any officer field-overrides). The officer's actions on a row are **Approve / Reject / Cancel**.

**The shape:**

The queue lists pending rows; a **row-click navigates to a detail page** whose `detail` panel carries an **`approval` section** — the runtime renders the Approve / Reject / Cancel buttons there and calls `POST /apps/{slug}/run/{cid}/approve` itself. You do **not** invent action fields for the decision; you wire the navigation and add the `approval` section.

```jsonc
{
  "data_sources": [
    { "id": "ds_pending", "type": "workflow_staging", "ref": "<this app's slug>",
      "filters": { "slug": "<this app's slug>", "status": "pending_review", "max_age_days": 30 } },
    { "id": "ds_history", "type": "workflow_staging", "ref": "<this app's slug>",
      "filters": { "slug": "<this app's slug>", "status": { "$in": ["applied", "rejected", "cancelled"] } } }
  ],
  "pages": [
    {
      "id": "inbox", "title": "To review", "layout": "grid",
      "panels": [
        { "id": "needs_review", "type": "queue", "title": "Awaiting your decision",
          "data_source": "ds_pending",
          "columns": ["case_natural_key", "llm_recommendation_text", "status", "created_at"],
          "actions": [
            { "label": "Review", "is_row_click": true,
              "navigate": { "page": "review_detail",
                            "params": { "id": "{row.workflow_execution_id}:{row.case_natural_key}" } } }
          ] },
        { "id": "history", "type": "queue", "title": "Recent decisions",
          "data_source": "ds_history",
          "columns": ["case_natural_key", "status", "llm_recommendation_text", "updated_at"] }
      ]
    },
    {
      "id": "review_detail", "title": "AI recommendation", "hide_in_nav": true, "layout": "stack",
      "params": [{ "name": "id", "required": true,
                   "description": "Composite {workflow_execution_id}:{case_natural_key} — the correlation_id for /approve/{cid}." }],
      "panels": [
        { "id": "review_view", "type": "detail", "linked_to": "needs_review", "id_field": "case_natural_key",
          "sections": [
            { "type": "fields", "title": "Recommendation" },
            { "type": "markdown", "title": "Why", "content": "Review the reasoning + proposed write, then decide." },
            { "type": "approval", "title": "Decide" }
          ] }
      ]
    }
  ]
}
```

- The **`approval` section** is what commits the decision: Approve replays the row's `planned_writes` against the source MCP (server-side); Reject / Cancel are terminal non-apply states. The composite `id` passed in navigation is the `correlation_id`.
- The `workflow_staging` `ref`/`slug` is just **this app's slug** — the queue is fed by the app's own agent (on-demand `/run` and/or an app trigger). The self-test (`workflow_staging_wiring`) only checks that *something* feeds it (an `agent_id` or a trigger).
- **No `approve_claim`/`reject_claim` agent actions** — don't declare them; the platform owns apply / reject / cancel.

**Authorization** is by SA membership. Whoever the SA admin adds as a member can see and act on the queue.

**Assignment / routing is NOT part of this shape.** If the BA wants "leave it for someone else / reassign to another officer or dept," that is a **separate panel + action the builder composes** alongside the queue — it is its own capability, not built into the approval flow. Keep the recommendation queue to Approve / Reject / Cancel.

## Two write paths — AI recommendation vs direct user action

A SmartApp has **two independent, parallel ways** to change a source system. The BA chooses per task; both are fully audited (every action logs who/what/when to the same `app_run_audit` ledger).

| | **AI recommendation flow** | **Direct user action (classic write)** |
|---|---|---|
| Who decides | LLM agent reasons → proposes; officer Approves | The **user** decides and clicks; no LLM in the loop |
| Trigger | `/apps/{slug}/run` (on-demand click) or an app **trigger** (precompute — see Triggers below) | a panel **`tool_button`** → `/apps/{slug}/tool/{name}` |
| Commit | only on Approve (replays `planned_writes`) | **immediately** on click |
| Use it for | judgement work — triage, classify, score, recommend a write | day-to-day deterministic operations the user just *does* — **assign / reassign, add comment, transfer, change status, close, tag**, any write the BA asks for |
| Both | audited (actor + action + args + result + timestamp); governed by the same dept-MCP write actions | same |

### Direct write action (no LLM)

This is the **classic write button/form** — the user knows what they want; the LLM would only add cost and latency. Bind to a deterministic `tools_v2` tool of `kind="mcp_action"` (a registered dept-MCP write). The runtime POSTs to `/apps/{slug}/tool/{tool_name}`, which fires the write **directly** (`dry_run=False`), commits to the source, and **writes an audit row** (`surface="smartapp_tool_direct"`) — independent of the recommendation queue. **Two ways to wire it, by whether the action needs user input:**

**(a) No-input action → a `tool_button`.** For an action whose args are static or come from the page (e.g. "mark verified", "close", "assign to me" on a record's detail page). Use `{param.x}` to pull a page param (a detail page's record id):

```jsonc
// On a detail page (pageParams.id = the record id):
"tool_buttons": [
  { "tool_name": "close_case", "label": "Close case",
    "args": { "case_id": "{param.id}" },
    "confirm": "Close this case?",
    "roles": ["supervisor"] }      // optional per-button role gate
]
```

**(b) Action that needs the user to type/choose → a `form` panel with a direct `on_submit.tool_name`.** The form fields become the write's arguments — no LLM. This is how assign-with-a-reason, add-comment, transfer-with-target work:

```jsonc
{ "type": "form", "id": "add_note", "title": "Add note",
  "schema_inline": { "type": "object",
    "properties": { "case_id": { "type": "string" }, "note": { "type": "string" } },
    "required": ["case_id", "note"] },
  "on_submit": { "tool_name": "add_comment" } }   // direct write; fields = args; NO agent
}
```

- The bound tool MUST be a `kind="mcp_action"` `tools_v2` entry. **Do NOT** wire an agent *action* as a `tool_button` (the publisher rejects it — that's the LLM path; use the queue's `agent_action` / `approval` section for those). `on_submit.tool_name` and `on_submit.agent_action` are mutually exclusive — pick direct or LLM.
- **`confirm` is required on a write `tool_button`** (publish rule **W-06**) — a direct source write can't be a silent one-click. (Forms gate via the submit step + their fields.)
- Authorization: the panel's `permissions` + the panel-level allowlist (a leaked token can't fire a tool that isn't on a panel the user can see). A `tool_button` may add an optional `roles: [...]` gate; a form direct-submit gates via the panel's permissions.
- **Assign / comment / transfer need no special primitive** — each is just an `mcp_action` write the source MCP already exposes; wire a button (no input) or a form (with input) per operation.

## Triggers — precompute a recommendation

The on-demand path (`/apps/{slug}/run`) runs the agent **when the officer clicks**. To have the
recommendation **ready before the click** — the officer opens the inbox and the triaged case is
already there — add an **app trigger**. A trigger runs the *same agent action* ahead of time and
stages the recommendation into the *same* inbox (`smartapp_workflow_staging`): it's the app's own
agent, fired on a schedule/event.

A trigger in the **default `execution_mode: "recommend"`** never commits — it produces a
recommendation the officer approves, exactly like an on-demand run. **The ONE exception is an
auto-process trigger** (`execution_mode: "auto_process"` + an `auto_process_policy`): there the
deterministic policy engine auto-**commits** the writes that clear the BA's bound, and queues the
rest (see **Auto-process** below). So: recommend-mode trigger = never commits; auto-process trigger
= commits within the BA's bound, fails closed to the queue. Pick the mode the BA chose — do not
build recommend-mode when they asked to auto-process under a bound.

Author them on `app_spec.triggers[]`. Four kinds:

| `type` | Fires when | Key fields |
|---|---|---|
| `webhook` | An external system POSTs an event (one case per call) | `secret_ref: "env:NAME"` (HMAC) |
| `schedule.cron` | A cron time | *(cadence is set by the officer in the UI — see below)* |
| `schedule.interval` | Every N seconds | *(cadence is set by the officer in the UI — see below)* |
| `poll` | Periodically query an MCP tool for **new** rows, run the agent **per new row** | `tool: "server.tool"`, `args`, `dedup_key`, `input_template` |

`action` = the AgentSpec action to run.

**You author WHAT the trigger does, NOT WHEN it runs.** Declare the trigger's `type` + `action`
(and for `poll`: `tool` / `dedup_key` / `input_template`). Do **NOT** author the schedule cadence
(`cron` / `every_seconds`) — that is **operational config the officer sets in the app's Auto-Recommend
panel**, where smart-app-service enforces a **5-minute floor + safe caps**. Leave `cron` /
`every_seconds` unset; the trigger publishes **deactivated** and the officer picks the cadence and
activates it. (Cron/interval/poll batch-size + concurrency are **operator-only** env ceilings a BA
cannot alter.)

**Timer vs per-record — pick the right kind (publish REJECTS a mismatch, rule C-06):**
- `poll` / `webhook` → **per-record**: the action runs once per row/event, inputs from
  `input_template` (poll) or the POST body (webhook). Use these whenever the action needs a
  specific record (e.g. `case_id`).
- `schedule.cron` / `schedule.interval` → **batch/timer**: the agent fires with **NO input** and
  must **query the pending set itself**, so their `action` MUST have **no required inputs**. Binding
  a per-record action (one that requires e.g. `case_id`) to a timer trigger is **rejected at
  publish** — use `poll`/`webhook` for per-record work.

```jsonc
"triggers": [
  // Precompute triage for every new complaint, every 10 minutes:
  { "id": "triage_new", "type": "poll", "action": "triage_complaint",
    "tool": "field_ops.list_complaints", "args": { "status": "new" },
    "dedup_key": "complaint_id",
    "input_template": { "complaint_id": "$row.complaint_id" } },
  // Or event-driven, one case per webhook:
  { "id": "carrier_event", "type": "webhook", "action": "triage_complaint",
    "secret_ref": "env:COMPLAINTS_WEBHOOK_SECRET" }
]
```

Triggers publish **deactivated** (off by default). The officer **sets the schedule
(cron / interval) AND activates** the trigger in the app's **Auto-Recommend** panel — the builder
never owns the cadence. The builder just declares the trigger in `app_spec`; it only fires
automatically once the officer has set a cadence + activated it (and the operator has enabled the
scheduler) — all of which happens after the builder's job is done.

**Volume & cadence (poll) — each row is a full LLM agent run, so this is bounded:**
- A poll **does not** fire one run per row unbounded. Per scheduler tick it processes at most a
  **bounded page** (default 5) **one-at-a-time** (concurrency 1) to protect the shared LLM/GPU; a
  backlog drains over successive ticks (the dedup cursor advances only for rows actually
  processed). These caps are **operator env settings** — a BA cannot raise them.
- Keep the poll's fetch cheap — put a **`limit`** in `args` (e.g. `{ "status": "new", "limit": 25 }`)
  and a `since`/`$last_cursor` so it returns only the new page.
- **In test it is ONE row at a time** — the BA fires via the **Run now** button (pulls exactly one
  new row, runs once, stages one recommendation; click again for the next case). The paged /
  concurrent scheduled cadence is enabled later by the officer + operator, after the build is done.
- For **high-volume / real-time**, prefer a **`webhook`** (one case per call — backpressure comes
  from the source) over a tight poll. A huge one-off backlog is a bulk job, not a recurring trigger.

## Auto-process — policy-gated autonomous commit (opt-in)

By **default every trigger is auto-recommend**: it proposes a write and **stages it for a human** (above). Build that unless the BA explicitly asks for automation.

When the BA says something like _"auto-process X below threshold"_, _"automatically route…"_, _"auto-approve when…"_, build an **auto-process** trigger. Safety model: **the human approves the POLICY (here, at build time); the agent then EXECUTES it per instance.** The commit decision is a **deterministic rule the runtime evaluates** — the LLM extracts/classifies, a deterministic predicate decides commit-vs-stage. Anything the rule doesn't clearly allow falls back to auto-recommend (staged).

Set on the `Trigger`:
- `execution_mode: "auto_process"` (default `"recommend"`).
- `auto_process_policy`:
  - `auto_commit_when` — a **deterministic Condition**: a leaf `{field, op, value}` (field = `payload.<k>` / `row.<k>` / `result.<k>`; op = `== != < <= > >= in not_in between matches`) or a combinator `{all|any|not: …}`.
  - `value_cap` `{field, max}`, `confidence_min` (0–1), `max_auto_per_run`, `rate_limit_per_hour` — the BA's optional bounding tools.
  - `on_miss: "recommend"` (only value — non-passing cases stage for a human).

```jsonc
{ "id": "auto_route_minor", "type": "poll", "action": "route_grievance",
  "execution_mode": "auto_process",
  "auto_process_policy": {
    "auto_commit_when": { "all": [
      { "field": "row.severity",   "op": "<=", "value": 3 },
      { "field": "payload.amount", "op": "<",  "value": 5000 } ]},
    "confidence_min": 0.8,
    "value_cap": { "field": "payload.amount", "max": 5000 }, "max_auto_per_run": 50 } }
```

**Two policy shapes — match the decision type:**
- **Threshold / fact rule** (a numeric or value bound exists): gate on a deterministic FACT, e.g. `payload.amount < 10000`. Strong determinism — good for a **financial** (or any) decision: bound it with a `value_cap` (strongly recommended for value decisions — the cap is the safety, though the platform doesn't force it). Auto-decide insurance claims under ₹10,000:
  ```jsonc
  { "execution_mode": "auto_process", "auto_process_policy": {
      "auto_commit_when": { "all": [
        { "field": "payload.amount",   "op": "<",  "value": 10000 },
        { "field": "payload.decision", "op": "in", "value": ["approve", "reject"] } ]},
      "value_cap": { "field": "payload.amount", "max": 10000 } } }
  ```
- **Classification / routing rule** (NO threshold — the agent picks a category): there's no number to compare, so gate on the **OUTPUT being in an allowed set** with the `in` op — route only to known-safe targets:
  ```jsonc
  { "execution_mode": "auto_process", "auto_process_policy": {
      "auto_commit_when": { "field": "payload.assigned_team", "op": "in",
        "value": ["customer_service", "it", "support", "technical"] } } }
  ```
  The determinism is **"the agent's choice must be one of these N safe targets"** (not a number). Use it for **low-stakes, reversible** routing — a misroute is cheaply fixed by the receiving team. Do NOT auto-process a high-stakes/irreversible write on a bare allowed-set; pair it with a fact rule (like the financial example) or keep it auto-recommend.

**Confidence (a recommended extra guard):** the agent reports a `confidence` (0-1) with every write. Set `confidence_min` (e.g. `0.8`) and a **low-confidence write automatically routes to human review (auto-recommend) even if the deterministic rule passes** — exactly what you want for an unsure auto-decision. Recommend setting it on most auto-process policies. It is an *extra* gate, not a substitute: still drive the decision with `payload.*` / `row.*` deterministic rules (confidence is a self-report). (`row.*` = fields of the source record that triggered the run; `payload.*` = the agent's proposed write.)

**RULES for the builder:**
- **ASK for the bounding criteria — do NOT refuse.** The AI reviews the case (prose + columns) and decides in EVERY mode; the gate just bounds *which* of those decisions auto-commit. So when the BA wants auto-process, ASK: _"What's the criteria to auto-process under — e.g. amount < ₹10,000, only junior roles, only routine cases?"_
  - BA gives a criteria → build that gate (a deterministic bound — see the two shapes above).
  - BA gives NONE → **ask explicitly, don't assume**: _"You haven't given a criteria — auto-process ALL cases? That removes the deterministic business bound; the AI's decision then commits for everything within the safety limits (confidence, value cap). Confirm?"_ Only on a clear yes, emit `auto_commit_when: { "always": true }` and tell them it's the highest-autonomy mode. Never silently auto-process-all, and never silently refuse.
- **Map NL → the Condition DSL** and **shape-confirm**: _"I'll auto-commit when `amount < ₹10,000`; everything else waits for approval — correct?"_
- Auto-process is **orthogonal to trigger type** — any `poll` / `schedule.cron` / `schedule.interval` / `webhook` trigger can be `recommend` or `auto_process`.

## The gate BOUNDS the AI's decision — it does NOT replace the AI's review
In EVERY mode the AI reviews the whole case — **prose AND columns** — and decides. Auto-process just adds a deterministic GATE that bounds **which** of those decisions commit without a human. The gate limits **blast radius** (value / scope / reversibility); it does NOT verify the AI is right. So **judgment-based decisions CAN auto-process — bounded.**
- **Insurance:** the AI reads the full claim (prose + fields) and decides approve/reject; `amount < 10000` bounds auto-commit to small claims; bigger → human.
- **Job application:** the AI reads the whole application (resume prose + fields) and decides advance/reject; a bound like `row.role_level == "junior"`, a hard-knockout (`row.has_required_license == false`), or the AI's own `result.fit_score >= 80` decides which auto-commit; senior/borderline → human.

**The builder's job is to capture the BOUND, not to refuse.** Pick it with the BA — strongest first:
1. **Column threshold / fact** on `row.*` (amount, years, role_level) — bounds by a source truth (strongest, deterministic).
2. **Allowed-set** on the decision (`payload.decision in [...]`, `payload.team in [...]`).
3. **The AI's own score** (`result.fit_score >= 80`) — *weaker*: the AI is judging itself. Fine for **low-stakes/reversible** (routing, auto-advance); for high-stakes pair it with a column bound + `confidence_min` + a tight `value_cap`.
4. **`always: true`** — NO business bound (BA opened it for all). Emit ONLY on explicit confirmation; flag it.

**Always ASK these before building auto-process:**
1. What's the **criteria/bound** to auto-commit under? (if none → confirm "auto-process all?" per the rules above)
2. Are the gated fields **structured** (`row.*`, entered in the app/source) or **extracted from prose** by the AI? Prose-extracted facts carry extraction risk → pair with `confidence_min`, or gate on the structured source field instead.
3. **Advance or reject?** Prefer auto-**ADVANCE** (reversible — a human still decides downstream) over auto-**REJECT** (ends a candidacy / hard to reverse → tighter bound + audited, or keep recommend).

You may still **decompose** when it helps (auto-process the clear‑cut slice, auto-recommend the borderline), but do not tell the BA judgment "can't" be auto-processed — it can, within a bound they choose. A job-application app is the canonical example: eligibility knockouts + clearly-qualified juniors as `auto_process`; senior/borderline fit as `auto_recommend`; both on the same app's trigger roster.

## Multiple agents per app (the trigger roster)
`AppSpec.triggers[]` is a **roster** — one app can run **several agents for different use-cases** (routing, triage, classification…), each its own `Trigger` (own `type`, `action`, tools, `execution_mode`/policy). Give each a `use_case` label. Mix freely: a routing trigger can be `auto_process` while a high-value-approval trigger stays `recommend`. Build one trigger per distinct automated job the BA describes.
