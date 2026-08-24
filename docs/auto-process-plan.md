<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Auto-Process — plan (rule-gated autonomous commit, multi-agent per app)

_Status: PLAN (unbuilt), 2026-06-09. Extends Auto-Recommend (agent triggers). Qualifies [[project_builder_workflow_recommend_only]] — the recommend-only posture becomes the DEFAULT, with auto-process as an opt-in, rule-gated relaxation._

## 1. The reframe that keeps it safe (read first)
Auto-process is **NOT** "let the AI write to the SoR autonomously." It is: **the human approves the POLICY once (at build + governance time); the agent then EXECUTES that deterministic policy per instance.** Per-instance approval moves to per-policy approval.


## Build status (2026-06-09) — Phase 1 BUILT + unit/orchestration-verified
- **Models** (`models.py`): `Condition` (DSL), `ValueCap`, `AutoProcessPolicy`, + `Trigger.execution_mode` / `auto_process_policy` / `use_case` (+ validator). **schema** (`app_spec.schema.json`) updated — publish-path validated for both recommend + auto_process triggers (avoided the 3-layer-contract trap).
- **Runtime gate** (`trigger_runner._fire_and_persist`): at `resp.status=="pending_approval"`, auto_process triggers partition planned_writes → commit passers via `main.commit_auto_process_writes`, stage the rest. Covers all trigger types (all route through `_fire_and_persist`). Orchestration test PASS (1 committed / 1 staged with reason). Recommend triggers unaffected (elif).
- **Commit** (`main.commit_auto_process_writes` + `_record_auto_process_decision`): governed dept-MCP write (or overlay branch) under the app system principal, idempotent, + a DecisionRecord per write (env-routed `auto_process_decisions`). Reuses the proven `call_dept_mcp_execute_action`.
- **Confidence gate WIRED** (2026-06-09): the agent reports a `confidence` (0-1) on each `perform_action` call (param in `build_data_tools`), captured onto the planned_write; the gate checks it before the rule, so a **low-confidence write routes to auto-recommend even if the deterministic rule passes** (absent → fail-closed). Unit-verified.
- **Builder skill** (`citra-app-spec/references/writes-triggers.md`): **STEP 1 — present the 3 modes (on-demand / auto-recommend / auto-process) in plain language, NO silent default**; for auto-process **suggest a goal-specific bound** (e.g. `amount < 10000` for claims, allowed-set for routing), let the BA choose, then build; **ask for the bounding criteria (don't refuse)**; the gate bounds, doesn't replace the AI's review; bound strength order (column fact > allowed-set > AI self-score > always); NL→DSL + shape-confirm; value-capped financial; confidence as an extra guard; the multi-agent roster. (If the BA already stated the mode, skip the menu.)
- **REMAINING**: live MCP commit test (needs a configured auto_process trigger + live MCP); Phase 2 guardrails (rate-limit/circuit-breaker persistence, RBAC on enablement, test/prod governance gate); Triggers-panel UI for the roster + per-trigger mode; builder elicitation only as good as the (currently environmentally-blocked) build pods allow.

## 1b. The three execution modes (all from the same trigger infra)
Same propose-then-decide machinery; they differ only in **who commits and when**:

| Mode | Invoked by | Agent does | Commit |
|---|---|---|---|
| **On-demand recommend** | user click (chat / run) | proposes a write (plan_only) | human, in the UI |
| **Auto-recommend** (today's trigger default) | cron / poll / webhook trigger | proposes a write (plan_only) → **stage** to officer inbox | human approves later |
| **Auto-process** (new) | the SAME cron / poll / webhook triggers | proposes a write (plan_only) → **policy gate** | **autonomous if the rule passes; else falls back to auto-recommend (stage)** |

**Auto-process is orthogonal to trigger type** — any trigger (`cron` / `every_seconds` / `poll` / `webhook`) can be `recommend` (default) or `auto_process`. An app has a **roster of triggers** (`AppSpec.triggers[]`), each its own use-case agent; mix recommend + auto-process freely across the roster. Nothing about the firing changes — only the commit decision after the agent proposes.

## 2. Where it plugs into today's architecture (already mostly there)
- **`AppSpec.triggers: List[Trigger]`** — a roster already supports **multiple agents/use-cases per app**. Each `Trigger` invokes an AgentSpec `action` (routing, triage, classification, …). Multi-agent is structural today.
- **`fire_trigger(plan_only=True)`** hardcodes recommend ("not an autonomous-write grant — only lets the agent PROPOSE"); the officer's Approve replays the governed MCP write. Auto-process makes `plan_only` **per-trigger + rule-gated**.
- **The approve-replay commit path** (`call_dept_mcp_execute_action`) is the SAME code auto-process invokes programmatically when the policy passes — no new write path, just an automatic trigger of the proven one.

## 3. Data model
Extend `Trigger` (per-trigger = per-agent/use-case):
- `execution_mode: Literal["recommend", "auto_process"] = "recommend"` (default unchanged).
- `use_case` / `label: str` — friendly name for the roster UI (which agent does what).
- `auto_process_policy` (required iff `auto_process`):
  - `auto_commit_when`: a **deterministic CONDITION** (predicate) over the proposed write `payload.<field>` and the source `row.<field>` — NOT LLM discretion, NOT arbitrary code.
  - `value_cap` (e.g. amount ≤ X), `max_auto_per_run`, `rate_limit_per_hour`.
  - `confidence_min: float` — the agent's self-reported confidence floor; below → recommend.
  - `on_miss: "recommend"` (only legal value at v1 — fail to the human, never drop or blind-commit).

## 4. The deterministic rule engine
- A small, safe **condition DSL** (no `eval`, no LLM): operators `== != < <= > >= in not_in between matches`, combinators `all/any/not`, operands = `payload.<path>`, `row.<path>`, literals.
- Evaluated AFTER the agent proposes the write (plan_only run produces the concrete payload), BEFORE commit.
- **Fails CLOSED**: unknown field, type mismatch, ambiguous, or any evaluation error → `recommend` (never auto-commit on a broken rule).
- Reuse the `FieldSpec`/`OptionsSource` machinery where it fits ([[project_editable_hitl_fieldspec]]).

## 5. How the auto-process policy works at runtime (the mechanism)
The agent ALWAYS proposes first — the recommendation is computed identically to auto-recommend; the policy only decides commit-vs-stage **after** the proposal. Concretely:

2. **The gate** (new — at `trigger_runner.run_trigger_once`, exactly where `resp.status == "pending_approval"` today routes to staging): if `trigger.execution_mode == "auto_process"`, evaluate `trigger.auto_process_policy` against EACH planned write and partition into **commit** vs **fall-back-recommend**:
   - `policy.auto_commit_when` is True over `{payload, row, result}`? (deterministic DSL §4; **fails closed** to recommend on any error/unknown field)
   - **ALL pass → COMMIT** via the SAME governed replay the officer's Approve uses (`_replay_planned_writes_with_overrides` → `call_dept_mcp_execute_action`), under the app system principal, idempotent (`idempotency_key`), **audited + a DecisionRecord** (rule, inputs, proposal, decision, outcome).
   - **ANY fail → STAGE** to the officer inbox exactly like auto-recommend, annotated `auto-process declined: <reason>` (`on_miss=recommend` — never dropped, never blind-committed).
3. **A single run can do both** — auto-commit the rule-passing writes AND stage the rest: autonomy where the policy is confident, human review everywhere else.

**Wiring**: `main` injects a `commit_recommendation` callback (wrapping the replay) into `run_trigger_once`, parallel to the existing `stage_recommendation`. Policy evaluation is a pure module (`auto_process.py`) — no LLM, no `eval`, unit-testable.

```jsonc
{ "id": "auto_route_minor", "type": "poll", "action": "route_grievance",
  "execution_mode": "auto_process",
  "auto_process_policy": {
    "auto_commit_when": { "all": [
      { "field": "payload.amount",      "op": "<",  "value": 5000 },
      { "field": "result.confidence",   "op": ">=", "value": 0.8 }
    ]},
    "value_cap": { "field": "payload.amount", "max": 5000 },
    "confidence_min": 0.8, "max_auto_per_run": 50, "on_miss": "recommend"
  }
}
```

## 5a. Idempotency
Auto-commits reuse the existing `idempotency_key` / poll `dedup_key`, so a re-fired trigger or a retried tick never double-commits.

## 6. Guardrails / governance (this removes the human — non-negotiable)
- **Circuit breaker**: per-agent rate cap + anomaly detection (volume spike, value outliers) → auto-revert to recommend + alert.
- **Kill switch**: per-trigger `enabled` + a global "pause all auto-process" toggle.
- **Audit + DecisionRecord**: every auto-commit logs rule + inputs + agent proposal + decision + outcome — queryable + reversible reference ([[project_llm_governance_plan]]).
- **Test/prod**: rule authored + tested in **test** (commits only to the test SoR/MCP, capped); prod auto-commit enabled only on promotion **+ an explicit governance sign-off**. Never auto-commit prod from a test run.
- **RBAC**: enabling `auto_process` or editing a rule is an **approval-gated config change** by an authorized role (app owner + governance approver), not any BA, not the agent.
- **MCP-layer defense-in-depth**: the dept-MCP's own write-authz + preflight still runs — auto-process does not bypass source-system safety ([[project_llm_guardrail_gaps_2026_05_29]]: MCP owns source-system safety).

## 7. Builder skill + elicitation
- **Default = recommend.** No change unless the BA asks for auto.
- The builder **detects auto-process intent** ("auto-process below X", "auto-route", "auto-approve when…") and enters elicitation that **requires** a concrete deterministic rule + ceiling + fallback.
- It **refuses vague autonomy** ("auto-process everything" / "use your judgment") — if the BA can't articulate a deterministic predicate, it stays recommend.
- New skill `citra-auto-process` + updates to the trigger/agent-authoring skill.

## 8. Multiple agents per app
- Roster = `triggers[]`; each trigger = one use-case agent (trigger + `action` + tools + `execution_mode` + policy + scope). Already structural.
- Add per-trigger `use_case`/`label`; ensure each is independently configured + governed.
- **Triggers panel** (Citra-UI) manages the roster: add/edit/enable each, show its mode (recommend vs auto-process), its rule, and recent auto-commits/fallbacks.
- Event routing already exists (`type` + `input_template` + `action`).

## 9. Phasing
- **Phase 0** — multi-agent UX: surface `triggers[]` in builder + UI with `use_case`/`label` + an (inert) `execution_mode` flag. No behavior change.
- **Phase 2** — guardrails: circuit-breaker, rate/value caps, kill switch, DecisionRecord audit, RBAC on enablement, test/prod gating.
- **Phase 3** — builder elicitation + the `citra-auto-process` skill (NL→rule, refuse vague autonomy, shape-confirm).
- **Phase 4** — observability: an "auto-process activity" view (auto-committed vs fell-back vs anomalies) + alerts.

## 10. Open decisions (need your call)
2. Who approves *enabling* auto-process — app owner alone, or a separate governance role?
4. Confidence gating — do agents self-report confidence reliably enough to gate on, or rely purely on the deterministic rule?
5. Test env — should test ever auto-commit (to the test SoR), or always recommend in test and auto only in prod?
