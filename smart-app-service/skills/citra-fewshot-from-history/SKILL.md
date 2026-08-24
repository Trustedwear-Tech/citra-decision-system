---
name: citra-fewshot-from-history
description: Ground a Smart App agent in the tenant's own historical decisions (few-shot from history) — detect the opportunity, vet the data (Gate A), then bind the neighbor_samples tool + a grounding contract. NO workflow, NO model training.
metadata:
  category: citra
  tools: [bash]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra — Few-Shot from History

## Purpose

When a Smart App's goal is repetitive decisions on records of the same shape
(claims triage, KYC scoring, vendor approval, application shortlisting), the
agent is far stronger when grounded in the tenant's **own past decisions**
rather than the LLM's generic prior.

This is **in-context few-shot grounding** — at runtime the `neighbor_samples`
tool loads the nearest **completed-decision** records (the inputs a team saw +
the decision they reached) into the prompt. **No model training. No LoRA. No
workflow.**

The gate is **"does the dataset contain completed decisions?"** — NOT
"historical table vs live table". A completed decision is a row that holds the
input AND the FINAL outcome. Such rows usually live **inside a live operational
table**, not a separate archive — e.g. a `theft_cases` table holds both
in-progress rows (`pending`, `under_recovery`) and decided rows (`recovered`,
`written_off`, `disputed`). So you DO ground on live operational datasets — you
just pull only the **decided (terminal-state) rows**, never the in-progress
ones. A purely-live dataset with NO completed decisions (open tickets never
resolved, a sensor feed) → do not ground; there is nothing to learn from.

The sample collection is built and refreshed by a deterministic
smart-app-service operation (pull decided rows → package → select → guard →
write). Your job in the build is only to: **detect the opportunity, vet the
data, and emit the binding** — two spec edits, no infrastructure to author.

## When to Use

Run as **Phase 1.5** — after Phase 1 (Internship/Discovery), before Phase 2
(Agent Spec). **Timing matters:** in 1.5 you **detect** the opportunity,
**vet** the data (Gate A), and get the **BA's explicit yes**. But
`agent_spec.json` does not exist yet (it's a Phase 2 artifact), so the actual
`neighbor_samples` tools + `grounding` contract below are **applied when you
author the AgentSpec in Phase 2**, not in 1.5. Record the confirmed grounding
decision now; wire it into `agent_spec.json` in Phase 2. Trigger when ANY of:

1. The goal contains "triage", "auto-approve", "classify", "shortlist",
   "decision", "review queue".
2. A catalogued dataset the BA picked carries a `decision_history` descriptor
   with `is_decision_record: true`.
3. The BA says "look at how we did it before" / "use our past data".

Otherwise skip silently — don't burden the BA with optional infrastructure.

## Step 1 — find datasets that contain completed decisions

A dataset is a candidate when you can **SEE, in its real sampled rows**, that it
holds completed decisions: an **outcome/decision column whose values are decided
states** (e.g. `recovery_status` ∈ {recovered, closed}, `status` = resolved,
`theft_confirmed` = true/false) **PLUS the input columns that drove that
decision**. **Eligibility is a REQUIREMENT, not a hint:** ground ONLY on a
dataset the catalogue explicitly marks `decision_history.is_decision_record ==
true`, set by the IT/data team who own the source (in dept_sources / the
catalogue). Do NOT auto-select, judge, or infer a decision dataset from sampled
rows. If a relevant table is not marked, grounding is OFF for it — tell the BA to
ask the IT/data team to mark it (declare the decision column, terminal states,
and outcome rule) in the catalogue; until then build on rules/docs.

> **DO NOT INVENT — this is the hard rule.** Only treat a dataset as a decision
> dataset when you can point to a *real* decided-outcome column in the sampled
> rows. Never fabricate a decision column, terminal states, or example rows;
> never coerce a live/in-progress table (`open_claims`) or RAG knowledge corpus
> (`policies_pdf`) into "decisions." If you cannot genuinely see completed
> decisions, it is **not** a decision dataset — skip grounding and say so.

Keep ONLY flagged candidates:
- **Flagged (required):** `select(.decision_history.is_decision_record == true)` — copy `decision_column` / `terminal_states` / `timestamp_column` / `reasoning_column` (and the outcome rule, when present) from the descriptor. A dataset that is **not** flagged is **not** a candidate — never add it, never reconstruct a mapping from the sample.

```bash
jq '[.datasets[] | select(.decision_history.is_decision_record == true)
     | {dataset_id, source_id, name,
        decision_column:  .decision_history.decision_column,
        timestamp_column: .decision_history.timestamp_column,
        terminal_states:  .decision_history.terminal_states,
        reasoning_column: .decision_history.reasoning_column}]' \
  <catalogue response> > /workspace/build/historical-candidates.json
# ONLY flagged datasets are candidates. If this is empty, there is nothing to
# ground on — do not substitute an unflagged table.
```

## Step 1.5 — vet the data (Gate A). THIS IS THE GATE.

Grounding on bad history is **worse than not grounding**. For each candidate:

```bash
# Flagged dataset only — the catalogue descriptor supplies the mapping:
GET /builder/history-quality?dataset_id=<id>&source_id=<source_id>
```

It returns `hard_gate_pass`, `signals`, a `suggested_contract`, and `notes`.

1. **Hard gates (deterministic).** If `hard_gate_pass == false` → **do NOT
   ground.** (Too few rows, only one decision class, decision column missing,
   no input columns.) Tell the BA plainly why and build on rules/docs instead.
2. **Your judgment.** Even when hard gates pass, decide: are the
   `n_input_columns` the *actual drivers* of the decision (not off-table in a
   PDF or an adjuster's head)? Is the class balance usable? Is the
   `date_range` recent enough to reflect current policy? If inputs can't
   plausibly reproduce the decision, the few-shots mislead — **don't ground.**

Write a one-paragraph verdict — this becomes `evaluation_verdict` in the
contract (REQUIRED; publish is blocked without it).

**If NOT good enough:** skip Step 2. Say so, e.g.:
> "Your closed-claims table is 98% 'approved' with almost no recorded
> reasons — not enough signal to ground safely. I'll build on your policy
> documents and rules instead."

## Step 2 — emit the binding (only when Step 1.5 PASSED **and the BA confirms**)

**MANDATORY — get the BA's explicit confirmation before wiring grounding.**
Never auto-enable few-shot-from-history. Tell the BA, in plain language, exactly
which dataset and which decisions you'd ground on, and that you judged it a
decision dataset *from the real data* (name the decision column + terminal
states you actually saw):
> "Your `field_operations.theft_cases` table has ~N past cases with their
>  recovery outcomes (recovered / closed) — I can ground the agent's
>  recommendations on those real decisions. Want me to?"

Wire the binding **only on an explicit yes**. If the BA declines or is unsure,
do NOT wire it — build on rules/docs and move on. Never invent the dataset, the
decisions, or the BA's consent. On yes, make TWO edits to the AgentSpec **when
you author it in Phase 2** (in Phase 1.5 the file doesn't exist yet — just
record that grounding is confirmed for this dataset so Phase 2 wires it):

### (a) Add TWO `neighbor_samples` tools to `tools_v2`

Add **both** — the canonical one is the always-on baseline (the runtime
pre-injects it into the prompt every run, no matching needed), the neighbors
one sharpens it per-case when the run carries case features:

```jsonc
// ALWAYS-ON BASELINE — the representative spread of past decisions, injected
// into every run regardless of input. This is the grounding that matters most.
{
  "kind": "neighbor_samples",
  "name": "history_baseline",
  "collection": "Historical_Refresh",   // shared collection for ALL agents (rows isolated by agent_id)
  "mode": "canonical",                    // filter-only; always loads the curated set
  "top_k": 8
},
// PER-CASE — nearest past cases by feature similarity (only useful when the run
// receives the case's features, not just an id).
{
  "kind": "neighbor_samples",
  "name": "history_similar",
  "collection": "Historical_Refresh",
  "mode": "neighbors",
  "top_k": 3
}
```

Always include the **canonical** tool — it's the always-on baseline and does
not depend on rich run inputs. The neighbors tool is a bonus that pays off when
the app passes case features into the run.

### (b) Add the `grounding` contract to the AgentSpec

Take `suggested_contract` from Gate A verbatim, fill the field mapping from the
dataset's `decision_history` descriptor, and add your `evaluation_verdict`:

```jsonc
"grounding": {
  "source_id":        "<source>",
  "dataset_id":       "<source>.<table>",
  "source_kind":      "sql",
  "filters":          { "status": "closed" },
  "max_results":      5000,

  "source_id_field":  "<e.g. claim_id>",
  "input_fields":     ["<the decision drivers the agent should see>"],
  "output_fields":    ["<decision + any outcome cols>"],
  "decision_field":   "<decision_history.decision_column>",
  "terminal_states":  ["<decision_history.terminal_states — the DECIDED states>"],  // ground ONLY these; excludes in-progress (e.g. "pending")
  "reasoning_field":  "<decision_history.reasoning_column, if any>",

  "target_count":     8,            // canonical few-shots to curate
  "per_decision_min": 1,

  // Gate B guard thresholds — copy from suggested_contract:
  "min_samples":               8,
  "min_canonical":             5,
  "shrink_floor":              0.5,
  "required_decision_classes": ["approve","reject","escalate"],
  "min_decision_fill_rate":    0.9,

  // Gate A evidence — REQUIRED (publish blocked without both):
  "source_profile_baseline":   { /* the /builder/history-quality "signals" */ },
  "evaluation_verdict":        "<your one-paragraph ground/don't-ground rationale>"
}
```

### (c) Add `outcome_poll` (self-improving loop) — DERIVED, never hand-authored

If `/builder/history-quality` returned a non-null **`suggested_outcome_poll`**,
copy it **VERBATIM** into `agent_spec.outcome_poll`. It is derived from the
catalogue's `decision_history` OUTCOME fields (outcome column, good/bad/neutral
values, key, settling window) that the IT/data team declared — **never invent the
outcome column or values yourself.** Leave `auto_refresh: false` (the default —
auto-learning stays off until the user enables it in the app). If
`suggested_outcome_poll` is **null**, do NOT add `outcome_poll`: outcomes won't be
auto-observed until IT declares the outcome signal in the catalogue (grounding
still works; the loop just won't self-validate yet).

That's it. **Do not author any workflow.** The sample collection is built by a
deterministic smart-app-service operation (pull from the tenant's real history →
package → select → **guard** → replace this agent's rows in the shared `Historical_Refresh` collection,
plus a Mongo audit copy), but it runs **only when the BA triggers it manually** —
publish does NOT auto-populate.

**Refresh is MANUAL by default — including ongoing self-learning.** Beyond the
publish-time gap above, the *continuous* loop (auto-folding validated outcomes
into memory) and the *periodic* full rebuild are BOTH off until the user turns on
auto-run for the app. **Do not enable auto-run in the build.** Leave
`outcome_poll.auto_refresh = false` (the default); tell the BA they can switch the
app to auto-learning later from the app's settings. Until then, learning happens
only when they click *Refresh grounding*.

**Closing message — tell the BA to refresh BEFORE testing.** When you've
published a grounded app, your hand-off MUST say this clearly, e.g.:

> "Done — and because this app learns from your past decisions, **run *Refresh
> grounding* first, before you test it**. Open the app in your Smart Apps list
> and click *Refresh grounding* — you'll see it pull your history and a 'done'
> event with how many examples loaded. Until then the app runs on its base
> prompt, so testing first would not show the grounded behaviour. You can
> re-run the refresh any time."

It pulls the tenant's real domain history, shows live progress + a completion
event in the UI, and the guard refuses to replace good live samples with a
degraded pull (fails safe).

## Guardrails

- **collection must equal `Historical_Refresh`** — publish (rule G-01) rejects
  a mismatch. All agents share this one collection; rows are isolated by an
  `agent_id` field that the runtime reader filters on automatically.
- **Gate A evidence is mandatory** — `source_profile_baseline` +
  `evaluation_verdict`. Publish (G-01) blocks grounding that wasn't vetted.
- **Never ground on data that failed Gate A or your judgment.** Skipping
  grounding is always a valid, safe outcome.
