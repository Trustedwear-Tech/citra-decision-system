---
name: citra-safety-rules
description: Canonical safety-rule reference for all SmartApp artefacts
metadata:
  category: citra
  tools: []
---

# Citra Safety Rules

The single source of truth for what a SmartApp build is **not allowed to do**. Every other skill defers here. If a rule here conflicts with a per-skill suggestion, **this file wins**.

---

## 1. How to read this file

Each rule has a **stable ID** so narration, validators, and audit logs can cite it unambiguously. The ID never changes; new rules get new IDs (never reuse retired ones).

| Prefix | Domain |
|---|---|
| `W-XX` | Source-system **W**rites |
| `L-XX` | **L**LM-as-judge limits |
| `T-XX` | Cross-**T**enant / cross-dept isolation |
| `X-XX` | Tool allowlists (e**X**ecution surface) |
| `H-XX` | **H**ITL |
| `A-XX` | Reversibility + **A**udit |
| `C-XX` | **C**ost / runaway |
| `D-XX` | **D**ashboard rules |
| `K-XX` | Copilot rules (`K` = co**K**pilot, `C` was taken) |
| `S-XX` | Smart App **S**cope |

**Always cite the ID** when narrating a refusal or unmet requirement: *"Cannot ship: rule W-01 forbids…"* — never paraphrase without the ID.

For each rule:

- **Statement** — one line, declarative.
- **Applies to** — `app` / `dashboard` / `copilot` / `all`.
- **Enforced at** — `STRUCTURAL` (schema rejects), `PUBLISH-VALIDATOR` (publish endpoint rejects), `BUILDER-SKILL` (this skill refuses to emit), `RUNTIME-NARRATION` (runtime explains and stops).
- **Instead** — the corrective action the builder takes.

---

## 2. Source-system writes (W)

Writes back to source systems (SAP, CRM, ERP, ticketing, billing, identity) are the highest-blast-radius operation a SmartApp can perform. Defaults are restrictive.

### W-01 — No verb=delete on source-system records
- **Statement:** No `write_action` may declare `verb=delete` against a source-system record.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL (AppSpec/AgentSpec schema enum excludes `delete`) + PUBLISH-VALIDATOR
- **Instead:** Use a soft-delete column (e.g. `is_archived=true`, `status=cancelled`) declared as `verb=update`.

### W-03 — Writes are governed: human approval by default, or the BA's auto-process policy
- **Statement:** Every source-system write is **governed** — it either pends for a human, or commits under a policy the **BA authored**. Nothing commits blind. The execution mode is the **BA's choice** (offer it, never default silently):
  - **On-demand / auto-recommend (default):** the write is staged as a recommendation and commits only when a human officer clicks Approve (`/apps/{slug}/run` runs plan-then-apply).
  - **Auto-process (the BA opts in):** a trigger with `execution_mode="auto_process"` + an `AutoProcessPolicy` (models.py) auto-commits the writes the BA's policy clears. **The BA owns the policy and the autonomy bound for THEIR domain:** the `auto_commit_when` gating conditions, `value_cap`, `confidence_min`, `max_auto_per_run`, `rate_limit_per_hour`. The **builder asks the BA for these bounding conditions** and builds them. The commit decision is **deterministic — no LLM** (the LLM proposes the write; the policy engine decides commit-vs-recommend) and **fails closed** to `recommend` (human queue) on any miss or evaluation error — never a blind commit. Whatever the policy doesn't clear goes to the human queue.
- **No platform refusal — the BA owns their domain.** The platform does **not** bar any domain, topic, or severity from auto-process — it is the BA's design decision (see L-01). The builder never declines based on domain and never caps the autonomy; it surfaces the choice (on-demand / auto-recommend / auto-process) and the bounds, and builds what the BA picks.
- **Applies to:** `app`
- **Enforced at:** RUNTIME — `plan_only` for the LLM tool loop; `auto_process.py` deterministically commits within the BA's policy (fail-closed on miss/error). Auto-process triggers publish **inactive**; the officer activates them in the **Auto-Recommend** panel.
- **Instead:** For human-reviewed work, surface the recommendation queue (a `workflow_staging` data source + queue panel). For the subset the BA wants automated, capture their bounds and wire an auto-process policy.

### W-06 — Direct-write buttons require confirm
- **Statement:** A panel `tool_button` bound to a write tool (`kind="mcp_action"`) — the direct, no-LLM write path — MUST set `confirm`. A direct source write commits immediately on click; it can never be a silent one-click. (Read / refresh buttons don't need it.)
- **Applies to:** `app`
- **Enforced at:** PUBLISH-VALIDATOR (`validate_direct_write_buttons_confirm`)
- **Instead:** Set `confirm` text on the button; for destructive/financial/legal direct writes, gate the button with `roles` too.

### W-07 — The builder works only in TEST; its job ends at the test URL
- **Statement:** You (the builder) build + fully test against the **test** environment — the app publishes to the test store, runs against the test MCPs, and its writes COMMIT against test data so the BA validates the real effect (see [citra-self-test](../citra-self-test/SKILL.md) step 0f, `/builder/probe` `execute:true`). You hand the BA the test URL and **STOP**. There is nothing beyond test for the builder to do.
- **Applies to:** `app`
- **Enforced at:** STRUCTURAL (the builder always runs in test → publish lands in the test store) + RUNTIME (the test write-validation 409s outside test).
- **Instead:** Build → validate writes in test → hand the BA the test URL → STOP.

---

## 3. LLM-as-judge (L) — autonomy is the BA's decision

The LLM **suggests, summarises, classifies, triages, and proposes writes**. Whether a proposed write commits autonomously or waits for a human is the **BA's** decision — captured in the execution mode + auto-process policy (W-03). The platform does not decide that for them, and the builder never refuses a domain.

### L-01 — Autonomy is the BA's decision (no platform refusal)
- **Statement:** For **any** write — routine or high-impact, **any domain or topic** — the builder does **NOT** refuse and does **NOT** force a particular autonomy level. It makes the choice **explicit** with the BA (on-demand approve, auto-recommend, or auto-process with BA-defined bounds) and builds what the BA chooses. The BA designs the system for their own domain; the platform special-cases no domain.
- **Applies to:** `all`
- **Enforced at:** BUILDER-SKILL (surface the autonomy choice + capture the policy bounds — **never refuse a domain**).
- **Instead:** Ask the bounding conditions (the `auto_commit_when`, value/confidence/rate caps) and wire the BA's choice. Reserve `requirements_unmet` for a genuine **capability or data gap** (the platform can't do it / the data isn't catalogued) — never to decline a domain the BA wants.

### L-02 — Auto-process is the BA's policy (no domain barred)
- **Statement:** Auto-process applies wherever the BA's `AutoProcessPolicy` says — there is **no** domain the platform excludes. The BA sets the gates; the builder asks for them and wires them. The only platform-side behaviour is robustness, not restriction: the commit decision is deterministic (no LLM) and **fails closed to the human queue** on a policy miss or error. See W-03 + `citra-app-spec` → `writes-triggers.md` for the policy DSL.
- **Applies to:** `app`
- **Enforced at:** RUNTIME (the BA's policy, evaluated deterministically + fail-closed).

### L-03 — requirements_unmet template (capability / data gaps — NOT domain refusal)
- **Statement:** When the goal needs a **capability the platform doesn't have** or **data that isn't catalogued / exposed by a dept-MCP**, the builder emits a `requirements_unmet.md` and builds the rest cleanly, rather than silently degrading. This is for *can't-build* gaps — never for declining a domain the BA wants (the BA owns their domain, L-01).

**Template:**

```markdown
# Requirements unmet

**Goal:** <one-line restate of BA's ask>

**Blocked by:** <the real gap — e.g. "no dept-MCP exposes the dataset this goal needs" / "OCR not configured on this deployment" / "no matching write action in the catalogue">

**Why:** <one sentence — what's missing in the platform/catalogue, not a policy judgement about the domain>

**What I can build now:** <one or two concrete things buildable with what IS available>

**What you need to unblock the rest:** <usually: ask IT to expose the dataset / write action via a dept-MCP + the catalogue, or enable the capability>
```

---

## 4. Cross-tenant / cross-dept isolation (T)

### T-01 — tenant_id filter is mandatory on every read
- **Statement:** Every `data_sources[]` entry that hits an MCP MUST resolve to a query with `tenant_id = <current tenant>` injected server-side.
- **Applies to:** `all`
- **Enforced at:** RUNTIME-NARRATION (proxy injects; builder cannot opt out)

### T-02 — No cross-tenant joins
- **Statement:** No tool call may reference data from a tenant other than the build session's `tenant_id`.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL (proxy refuses)
- **Instead:** If a BA legitimately needs cross-tenant aggregation (rare, platform-team only), it's a Citra-platform analytics job, not a SmartApp.

### T-03 — admin_only actions are not BA-buildable
- **Statement:** Any MCP tool flagged `admin_only=true` in the catalogue MUST NOT appear in a BA-authored AppSpec.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL (catalogue filter hides them) + PUBLISH-VALIDATOR (defence in depth)
- **Instead:** Surface the gap to the BA: *"That capability is admin-only; ask your platform admin to wrap it in a BA-safe action with the right approvals."*

### T-04 — Dept boundary respected
- **Statement:** A SmartApp owned by `dept=A` MUST NOT bind a tool from `dept=B` unless the BA's SA has explicit cross-dept grant in IdP.
- **Applies to:** `all`
- **Enforced at:** PUBLISH-VALIDATOR (checks SA grants at publish time)

---

## 5. Tool allowlists (X)

### X-01 — Only catalogued tools may be declared
- **Statement:** Every `agent_spec.tools_v2[]` entry MUST exist in `TOOL_CATALOGUE` for the current tenant + dept.
- **Applies to:** `app`, `dashboard`, `copilot`
- **Enforced at:** PUBLISH-VALIDATOR

### X-02 — tool_kind enum is closed
- **Statement:** `tool_kind` MUST be one of the values below. No free-form kinds.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL

**tool_kind enum**

| `tool_kind` | What it does | Builder declares it when |
|---|---|---|
| `mcp` | Read from a source system via dept-MCP | Any source-system read |
| `mcp_action` | Write to a source system via dept-MCP | Any source-system write (requires W-02; W-03 approval where applicable) |
| `rag` | Semantic search over indexed docs | BA wants policy/SOP lookup |
| `code_exec` | Run sandboxed Python for compute/report | Numeric computation or doc generation |
| `llm` | Sub-agent call | Distinct expertise warrants a sub-agent |
| `validate_form` | Deterministic form schema validation | Every FormPanel (per AGENTS.md multi-modal rules) |
| `vision_ocr` | RAW-TEXT OCR over an image/PDF (no structure/review) | FormPanel `accepts_files=true`, or the rare raw-text-only case; `OCR_ENABLED=true` |
| `image_analyze` | STRUCTURED per-image judgment → `ItemFinding` + per-item review + rubric learning | Assess / grade / judge a photo and act on it (record-bound); `OCR_ENABLED=true` |
| `doc_extract` | STRUCTURED document field extraction → `ItemFinding` + per-item review + rubric learning | Extract fields from a report/invoice/ID and act on them (record-bound); `OCR_ENABLED=true` |
| `consistency_check` | Deterministic record↔artifact cross-check + format/checksum validators (local, no LLM, read-only) → explainable `mismatches[]` evidence | Money/asset disposition apps (claims, loans, reimbursements, KYC) — proposed to the BA, never silent; findings are officer evidence, never an auto-reject |
| `neighbor_samples` | Inject decided past cases for grounding (`mode:"canonical"` always-on baseline + `mode:"neighbors"` per-case) | App is grounded in decided history (see `citra-fewshot-from-history`) |

### X-03 — code_exec is sandboxed and read-only on disk
- **Statement:** `code_exec` MUST NOT be granted network egress or write access outside `/workspace/scratch/`.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL (sandbox image) + RUNTIME-NARRATION

### X-04 — mcp_action requires explicit allowlist
- **Statement:** A tool of kind `mcp_action` MUST bind to a catalogue `write_actions[]` entry (from `GET /builder/catalogue?full=true` — separate from read tools), copying its `id`/`dataset_id`/`input_schema` verbatim.
- **Applies to:** `all`
- **Enforced at:** PUBLISH-VALIDATOR

---

## 6. HITL (H)

### H-01 — Human-in-the-loop is the BA's choice
- **Statement:** Whether a write waits for a human is the BA's design (W-03): **on-demand** and **auto-recommend** keep a human approver in the recommendation queue; **auto-process** commits per the BA's policy. You do **not** set per-action `approval_required` — the chosen mode + the auto-process policy govern it. Optionally name approvers in `hitl_policy.approvers` to restrict *who* may approve. For high-stakes decisions, make the autonomy choice explicit with the BA (L-01) and build what they choose — never refuse.
- **Applies to:** `app`
- **Enforced at:** RUNTIME (plan-then-apply for the human-reviewed modes; the BA's deterministic auto-process policy, fail-closed, for the rest)

### H-02 — Chat is read-only for source systems
- **Statement:** Chat surfaces (the global `agent_chat` panel, the copilot) MUST NOT execute `mcp_action` tools. Writes happen only from explicit officer surfaces: queue actions, form submissions, the recommendation-queue Approve, or a **direct `tool_button`** (the classic no-LLM write — see citra-app-spec "Two write paths"). Never from chat.
- **Applies to:** `app`, `dashboard`, `copilot`
- **Enforced at:** STRUCTURAL (runtime refuses) + PUBLISH-VALIDATOR

### H-03 — Approver != requester
- **Statement:** The user who submitted a queue item MUST NOT be in `hitl_policy.approvers` for the same action.
- **Applies to:** `app`
- **Enforced at:** RUNTIME-NARRATION (runtime hides the Approve button)

### H-04 — `hitl_policy.allow_writes_in_chat` is deprecated and inert
- **Statement:** The field `hitl_policy.allow_writes_in_chat` is deprecated. Setting it `true` is a publish error. The runtime does **not** honor it: chat is **unconditionally** read-only for source systems — `mcp_action` tools are stripped from the chat tool manifest AND blocked at dispatch regardless of this flag's value. A stored/migrated spec carrying `true` cannot enable chat writes.
- **Applies to:** `app`
- **Enforced at:** STRUCTURAL (publish rejects if present and true) + RUNTIME (manifest filter + dispatch block, both unconditional)
- **Instead:** Move the write into a queue-action or form `on_submit` (see decision tree below).

**Queue-action vs chat — decision tree**

```
BA wants the agent to do something that touches the source system.
              │
              ▼
   Is it a read (lookup, summarise, search)?
              │
        ┌─────┴─────┐
        │           │
       YES          NO  ──► It's a write. STOP using chat.
        │                          │
        ▼                          ▼
  OK to surface in           Does the user pick the
  agent_chat panel.          target record from a list?
                                   │
                             ┌─────┴─────┐
                             │           │
                            YES          NO
                             │           │
                             ▼           ▼
                  Queue panel +     Form panel +
                  row action on     on_submit action
                  the selected row. with a confirm modal.
```

---

## 7. Reversibility + audit (A)

### A-01 — Every write is audited (who / what / when)
- **Statement:** Every `mcp_action` execution MUST write an `app_run_audit` row capturing actor (`requested_by`), action, inputs, result (`write_events`), tenant, correlation_id, and timestamp — on a tamper-evident per-tenant hash chain. This holds for **both** write paths: the AI recommendation Approve **and** the direct `tool_button` action (`surface="smartapp_tool_direct"`). A committed write whose audit row fails to persist returns 5xx — the builder cannot suppress it.
- **Applies to:** `all`
- **Enforced at:** RUNTIME (runtime writes the record on both paths)

### A-02 — Audit panel auto-injected
- **Statement:** Any AppSpec containing at least one `mcp_action` MUST render an `audit` panel (auto-injected by the publisher) showing that app's DecisionRecord entries for the current SA.
- **Applies to:** `app`
- **Enforced at:** PUBLISH-VALIDATOR (publisher injects)

### A-04 — Audit retention
- **Statement:** DecisionRecord entries are append-only and retained per tenant retention policy. Builders MUST NOT declare TTL on audit data sources.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL

---

## 8. Cost / runaway (C)

### C-01 — Loop iteration cap
- **Statement:** Any tool-calling loop in the runtime MUST cap at **12 iterations** per turn. Builders MUST NOT request a higher cap.
- **Applies to:** `app`, `copilot`
- **Enforced at:** STRUCTURAL (runtime hard cap)

### C-02 — Batch concurrency cap
- **Statement:** Any `code_exec` or **poll-trigger** fan-out MUST cap concurrency at **8 parallel tasks**.
- **Applies to:** `app`
- **Enforced at:** STRUCTURAL (sandbox + trigger runner refuse higher)

### C-03 — Trigger cadence bounds (cron / interval / poll)
- **Statement:** A timer trigger's cadence (`schedule.cron`, `schedule.interval.every_seconds`, `poll.every_seconds`) MUST respect the bounds below. The **builder does NOT author the cadence** — it declares only the trigger `type` + `action` (+ poll wiring); the **officer sets the schedule** in the Auto-Recommend panel.
- **Applies to:** `app`
- **Enforced at:** STRUCTURAL (PATCH `/ai-triggers` + publish reject out-of-range) + RUNTIME (the scheduler clamps to the minimum).

| Bound | Value |
|---|---|
| Minimum interval | **5 minutes** (cron `*/5 * * * *`; interval/poll `every_seconds ≥ 300`) |
| Recommended interval | 15 minutes – 1 hour for polling jobs |
| Maximum interval | 30 days / 86400s (anything longer ⇒ use a webhook trigger) |

### C-04 — Token budget per turn
- **Statement:** Single-turn LLM calls MUST respect `LLM_MAX_OUTPUT_TOKENS`. No skill may override it.
- **Applies to:** `all`
- **Enforced at:** STRUCTURAL

### C-05 — Fan-out caps (RAG queries + trigger poll fan-out)
- **Statement:** Two fan-out limits share this id: (a) a single agent turn MUST NOT issue more than **5 RAG queries** (ask the user to narrow beyond that); (b) a **poll** app trigger processes at most a bounded page of new rows per tick with bounded concurrency (runner-enforced, ≤ the batch cap of 8; **1** in the test env).
- **Applies to:** `all` (RAG) / `app` (poll trigger fan-out)
- **Enforced at:** RUNTIME-NARRATION (RAG) + RUNTIME (the trigger runner bounds rows-per-tick + concurrency; see `trigger_runner._process_new_poll_rows`). Defaults: **5 rows/tick, concurrency 1** — operator env ceilings (`TRIGGER_MAX_POLL_ROWS_PER_TICK` / `TRIGGER_POLL_CONCURRENCY`) a BA cannot raise.

### C-06 — Timer triggers bind a BATCH action (no per-record input)
- **Statement:** A `schedule.cron` / `schedule.interval` trigger fires the agent with **NO input** (`{}`), so its `action` MUST have no required inputs — the agent queries the pending set itself (batch). A **per-record** action (whose `input_schema` requires e.g. `case_id`) MUST instead be bound to a **`poll`** trigger (inputs via `input_template`) or a **`webhook`** trigger (inputs from the POST body).
- **Applies to:** `app`
- **Enforced at:** PUBLISH-VALIDATOR (rejects a timer trigger whose bound action declares required inputs).

---

## 9. Dashboard rules (D)

### D-01 — Read-only data sources only
- **Statement:** Dashboard data sources MUST be `kind ∈ {mcp, rag}` with read semantics. `mcp_action` is forbidden.
- **Applies to:** `dashboard`
- **Enforced at:** STRUCTURAL

### D-02 — Narrator agent is mandatory
- **Statement:** Every AppSpec with a `dashboard` page MUST declare `app_spec.agent_id` pointing at an AgentSpec narrator. The hero-brief copilot band runs that agent in read-only chat_mode — it is rendered **automatically** from `agent_id`. Do **NOT** add an `agent_chat` panel to a dashboard page (the band is not a panel; an `agent_chat` panel there is redundant). The publish validator (`validate_dashboard_page_has_narrator`) checks for `agent_id`, **not** for a panel.
- **Applies to:** `dashboard`
- **Enforced at:** PUBLISH-VALIDATOR

### D-03 — No personal-vault datasets
- **Statement:** Dashboards MUST NOT read user-uploaded files or personal-vault datasets.
- **Applies to:** `dashboard`
- **Enforced at:** PUBLISH-VALIDATOR
- **Instead:** Register the file as a dept-MCP catalogue dataset first.

### D-04 — KPI tiles must cite their dataset
- **Statement:** Every KPI tile MUST declare `dataset_id` so the audit trail can attribute the number.
- **Applies to:** `dashboard`
- **Enforced at:** STRUCTURAL

---

## 10. Copilot rules (K)

### K-01 — Copilot is per-user, not per-tenant
- **Statement:** A copilot's memory and tool grants resolve against the **invoking user's SA**, never a tenant-wide shared SA.
- **Applies to:** `copilot`
- **Enforced at:** RUNTIME-NARRATION

### K-02 — Copilot inherits SmartApp safety rules
- **Statement:** Every rule in this file (W-, L-, T-, X-, H-, A-, C-, D-, K-, S-) applies to copilots unmodified.
- **Applies to:** `copilot`
- **Enforced at:** all layers

### K-03 — No background loops
- **Statement:** A copilot MUST NOT poll, schedule, or otherwise run when the user is not present. Use an **app trigger** (`app_spec.triggers[]` — schedule/webhook/poll) for scheduled / precomputed agent work.
- **Applies to:** `copilot`
- **Enforced at:** STRUCTURAL

### K-04 — Cross-app context is opt-in
- **Statement:** A copilot may read context from another SmartApp only when the user has explicitly granted that scope in their copilot settings.
- **Applies to:** `copilot`
- **Enforced at:** RUNTIME-NARRATION

---

## 11. Smart App scope (S)

### S-01 — Internal officer tools, not public surfaces
- **Statement:** SmartApps are internal tools for authenticated SA members. They MUST NOT be configured for anonymous / public access.
- **Applies to:** `app`, `dashboard`
- **Enforced at:** STRUCTURAL (publish defaults to `visibility=sa_members`; no `public` option exists)

### S-02 — Agentic operations app, grounded in an MCP source
- **Statement:** A SmartApp is an **agentic operations app**: a UI + dashboards + an AI agent that reads the tenant's live data, **recommends** decisions with reasoning, **writes back** to the source on human approval, and can **automate** that on a trigger. It is always grounded in a source database exposed through a dept-MCP and registered in the data catalogue. The *domain* is unrestricted — marketing ops, sales ops, internal operations are all fair game. The one hard requirement is a **registered MCP source**: every read, write, and panel must bind to a catalogued dataset / write-action — never to invented, mocked, or static data. (Internal-only / no-anonymous-access is covered by **S-01**.)
- **No source data?** A SmartApp cannot be built without a source. If no dept-MCP source covers the goal, do NOT improvise or build a static/mock surface — stop and offer the BA the two ways forward: **(1)** if the data already exists, ask IT to **expose that dataset** via its dept-MCP + data catalogue; **(2)** if it doesn't exist yet, ask IT to **create the database and business function and expose it** through MCP + the data catalogue. Surface the gap in `requirements_unmet`, then return when the source is registered.
- **Applies to:** `app`, `dashboard`
- **Enforced at:** BUILDER-SKILL (refuse with S-02) + PUBLISH-VALIDATOR

### S-03 — Tenant catalogue is the source of truth
- **Statement:** SmartApps MUST source data exclusively from the tenant catalogue (dept-MCP datasets, RAG corpora). User-uploaded CSVs are out of scope for dashboards (D-03) and require dept-MCP registration for apps.
- **Applies to:** `app`, `dashboard`
- **Enforced at:** PUBLISH-VALIDATOR

---

## 12. Narration templates

The verbatim refusal/stop copy (S-02, W-01, L-01, H-04, T-03, W-03, C-03 — substitute the placeholders, cite the rule ID, one sentence, no emoji) lives in **`references/narration-templates.md`**. Read it **only when you are actually refusing or surfacing a safety stop**; the rule IDs to cite are all in §2–§12 above.
