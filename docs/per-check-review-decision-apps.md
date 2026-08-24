<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Per-Check Review for API / SoR Checks (CIBIL, Aadhaar, …)

**Status:** Draft for review. **Pre-first-customer** — build when a loan/KYC-style
Decision App actually needs it (see [When](#when-to-build)).

**Goal:** let an API/System-of-Record check render in the officer UI as its own
reviewable line — *"CIBIL check: looks good [accept/reject]", "Aadhaar match:
looks good [accept/reject]"* — alongside the single overall application
approve/reject, exactly the way analyzed **images/documents** already do in
`acme-power-inspection-triage`.

---

## TL;DR

- **Multi-API is already supported** — the main agent can fire CIBIL + Aadhaar +
  bureau across up to 12 tool iterations (parallel calls allowed).
- **Per-check accept/reject is NOT built** — today `mcp`/`rag`/`consistency_check`
  results feed the agent's **one** record-level recommendation. Only
  `image_analyze`/`doc_extract` emit a per-item reviewable `ItemFinding`.
- **The whole review/ledger/learning substrate is already generic.** The gap is
  narrow: non-media results are never *collected as findings* and never *judged
  per-check*. Closing it is additive, not a rewrite.
- **The learning rubric SHOULD be reused** — same machinery, per-check-type
  bucket. Valuable where a check involves judgment (identity/name match, risk),
  near-free where it's a fixed threshold.

---

## Background: how it works today

### Images/docs (the pattern to mirror)
`image_analyze`/`doc_extract` are called **once per artifact** by the main agent.
Each returns an `ItemFinding` (`item_id, subject, recommendation, confidence,
rationale, citations`). The runtime collects these into
`RunResponse.item_findings` (only for those two kinds —
`runtime.py:3146-3173`), persists one `ItemDecisionRecord` per finding
(`disposition="proposed"`), and the UI renders each as an accept/reject card
(`ItemFindingReview.tsx`). The officer's per-item disposition posts to
`/items/{id}/feedback`; a **reject folds its reason into the
`(app, modality, task_type)` rubric** (`analysis_rubrics.py`). The overall
application approve/reject is the record-level Apply/`/approve`, and
`item_review_gate` (`models.py:2474-2486`) blocks Apply until every item is
reviewed. Per-item and overall are linked by `correlation_id`; the parent's
settled outcome is stamped back onto every item (one-directional).

### API/SoR checks (today)
`mcp` returns raw rows, `rag` returns semantic hits, `consistency_check` returns
`{mismatches, summary, …}` explicitly labelled *"EVIDENCE for the officer — cite
it; do not auto-reject."* None enter `item_findings`; all fold into the single
`RunResponse.recommendation` + `planned_writes`. There is **no per-check
verdict** and **no per-check review card**.

### What's already general vs media-pinned
The `ItemFinding` shape, the item ledger, two-tier precedent memory, rubric
learning, correlation/outcome plumbing, and the record-level review gate are all
**domain-neutral**. Only three things pin the flow to media:
1. `modality: Literal["image","document"]` on `ItemFinding`/`ItemDecisionRecord`
   (`models.py:1026, 1075`) + the feedback endpoint whitelist (`main.py:2879`);
2. the runtime collecting only `image_analyze`/`doc_extract` (`runtime.py:3147`);
3. the review card rendering an image thumbnail (`ItemFindingReview.tsx`).

---

## Proposal

### 1. A per-check evaluator tool — `check_evaluate`
The one genuinely new piece. A raw `mcp` read ("CIBIL score 780") is *data*, not
a verdict — something must judge it. `check_evaluate` is the **structured-data
twin of `image_analyze`**: same "fetch policy → judge input against it → emit a
finding" shape, with a data payload instead of an image.

- **Input:** `{subject: "CIBIL eligibility", item_id, data: <the mcp read result
  the agent already fetched>, task_type: "cibil-check"}` (+ optional `sop_source`
  for the policy, cached per `(app, task_type)` exactly like the media tools).
- **Output:** an `ItemFinding{subject, recommendation, confidence, rationale,
  fields, modality:"api"}` — same object the media tools emit.
- **Orchestration:** the agent calls `mcp` (CIBIL read) → passes the result to
  `check_evaluate` → gets a per-check verdict. One `check_evaluate` per check,
  mirroring one `image_analyze` per photo. **Main-agent tool, not a sub-agent**
  (sub-agents use the legacy path, return prose, emit no findings).
- **Deterministic shortcut:** when a check's acceptance is a fixed rule
  (`score >= 700`, exact match), allow a `rule` mode that produces the finding
  **without** an LLM call — cheap, and it still renders/reviews identically.

*(Alternative shape: a record-bound variant like `consistency_check` that fetches
+ judges in one call. Prefer the two-step above — it matches how `image_analyze`
takes an artifact the agent resolved, and keeps read separate from judgment.)*

### 2. Three small enabling changes (the substrate is otherwise ready)
- **Widen the modality enum** to add `"api"` (or `"check"`) on `ItemFinding`
  (`models.py:1026`), `ItemDecisionRecord` (`:1075`), the feedback whitelist
  (`main.py:2879`), and the TS interface (`ItemFindingReview.tsx:25`).
- **Lift the runtime collection gate** at `runtime.py:3147` so `check_evaluate`
  (and, if wanted, `consistency_check`) results are collected into
  `item_findings`.
- **Non-image review body** in `ItemFindingReview.tsx` — render `fields`/`data`
  as a small table instead of a thumbnail when `modality==="api"`. (Everything
  else — accept/reject/reason, the `/items/{id}/feedback` post — is unchanged.)

### 3. What needs NO change (already generic)
The item ledger, `fetch_item_precedents` (keyed on `(modality, task_type)`), the
rubric fold, outcome inheritance, `correlation_id` linkage, and the
`item_review_gate` (it only checks whether `item_findings` is non-empty) all work
as-is once API findings populate the list. The overall application approve/reject
(record-level Apply/`/approve`) is unchanged.

---

## Do we need the learning rubric here too?

**Yes — reuse the exact same rubric machinery**, and it's the right call for two
reasons:

1. **It's essentially free.** The rubric is keyed by `(tenant, app_slug,
   modality, task_type)` and trained by `append_correction` on reject. A
   `check_evaluate` finding for `(api, "cibil-check")` folds into its own rubric
   bucket exactly like `(image, "defect-photo")` — **no new rubric code**, just a
   new bucket per check type. So each check type learns independently: "how to
   judge a CIBIL check" vs "how to judge an Aadhaar match".

2. **It's genuinely valuable for JUDGMENT-based checks.** Where a check has grey
   areas — an Aadhaar name that's a transliteration variant, a bureau flag an
   officer routinely overrides, a borderline score with compensating factors —
   the rubric captures the accumulated officer corrections into the check's
   prompt criteria, on top of the authoritative SOP. This is the same value it
   gives image judgment.

**Nuance — deterministic vs judgment checks.** For a pure-threshold check
(`score >= 700`), the acceptance rule is fixed; the authoritative **SOP** (fetched
live, same as the media tools) carries it, and the rubric stays thin (few rejects
to learn from) — harmless, not wasteful. So the layering is identical to media:

> **SOP = authoritative policy (hard rules) · Rubric = learned officer
> refinements on top.**

Recommendation: wire the rubric for `check_evaluate` from day one (it's free and
correct), and use the deterministic `rule` mode to avoid an LLM call where the
policy is a hard threshold — the rubric simply won't accumulate much there.

---

## How it's structured — the 5 layers (CIBIL example)

The tool is not just backend code; it has to be *known to the builder* and
*wired per app*. Layers, and where `check_evaluate` lives in each:

1. **Platform tool definition (code).** `CheckEvaluateTool` (a `tools_v2` kind) in
   `models.py` + its handler in `tools_v2_dispatch.py`. Emits an
   `ItemFinding(modality="api")`.
2. **Builder skill (`skills/citra-agent-spec/SKILL.md`).** Where each tool kind +
   *when to use it* + *how to wire it* is taught to the builder LLM — today it
   documents `image_analyze`/`doc_extract` (one per `task_type`, record-bind,
   `sop_source`, ask the BA for `item_review_gate`). `check_evaluate` needs an
   analogous stanza, or **the builder won't know to wire it.**
3. **Authored AgentSpec (per app, produced by the builder).** The builder reads
   the catalogue (which describes the CIBIL API — `input_schema`, required `pan`,
   description) + the skill, and emits, e.g.:
   ```
   tools_v2:
     - mcp             ref=bureau.cibil          # the read
     - check_evaluate  task_type="cibil-check"   sop_source=<credit-policy>
     - mcp             ref=uidai.aadhaar
     - check_evaluate  task_type="aadhaar-match" sop_source=<kyc-policy>
   ```
   plus a `system_prompt` ("read CIBIL → call the cibil check; read Aadhaar → call
   the aadhaar check; then the overall recommendation") and, on the AppSpec,
   `item_review_gate="hard"`.
4. **Runtime (per decision).** Main agent: `mcp`(CIBIL) → `check_evaluate` →
   `ItemFinding{subject:"CIBIL", modality:"api", task_type:"cibil-check", …}` →
   collected → review card. Same for Aadhaar. Then the overall Apply/reject.
5. **Learning rubric (per check type, per app).** A reject on the CIBIL card folds
   into bucket **`(tenant, app_slug, modality="api", task_type="cibil-check")`** —
   i.e. "the CIBIL check *for this app*." Aadhaar → a separate bucket.

**Rules that fall out:**
- **`task_type` is the unit of both review and learning.** Each check = a distinct
  `task_type` → its own review card *and* its own rubric. The builder must give
  CIBIL and Aadhaar different `task_type`s (as it does `asset-inspection-defect`
  vs `inspection-report` for images).
- **The catalogue is how the API becomes known** — `sources.json` → data-discovery
  describes it (params, `mandatory_when_used`) → the builder reads it. No
  catalogue entry, no wiring.
- **One-per-check tool, main agent** — not a sub-agent.

Full chain: **`sources.json` (API) → catalogue (described) → builder skill (knows
`check_evaluate`) → authored AgentSpec (mcp read + check_evaluate per check) →
runtime (per-check finding) → rubric (per app, per check-type).**

## Related but distinct: mandatory checks (must-check CIBIL)
The existing `mandatory_when_used` + read-before-write evidence guard
(`required_lookup_autowire.py`, `evidence_guard.py`) guarantees a check **ran**
before a decision commits — a boolean gate, not a review. It composes cleanly: a
mandatory check should ALSO emit a `check_evaluate` finding, so "it ran" and "the
officer reviewed its verdict" are both enforced (the latter via
`item_review_gate="hard"`).

---

## Effort estimate (when scheduled)

| Piece | Effort |
|---|---|
| `check_evaluate` tool (dispatch handler, `rule` + `llm` modes, SOP fetch reuse) | ~1–1.5 days |
| Model/enum widening + feedback whitelist + collection-gate lift | ~0.5 day |
| Non-image review body in `ItemFindingReview.tsx` | ~0.5 day |
| Builder support (expose `check_evaluate` in the builder + wire mcp→check) | ~1 day |
| Tests (finding shape, gate, rubric fold, rule vs llm) | ~0.5 day |

~3.5–4 days. No change to the ledger/memory/gate/correlation substrate.

---

## Open questions (resolve when building)
- **Who supplies the check's data to `check_evaluate`** — the agent passes the
  `mcp` result (flexible, recommended), or a record-bound variant fetches it
  (turnkey)? Start with agent-passed.
- **Verdict vocabulary** — free-text `recommendation` (like media) vs a small
  enum (`pass/flag/fail`)? Free-text is consistent with today; an enum would let
  the UI colour-code.
- **Rubric namespace collisions** — confirm check `task_type`s don't clash with
  media `task_type`s in the same app (they're separated by `modality`, so safe).
- **Does an unreviewed API finding block the overall Apply?** Yes if
  `item_review_gate="hard"` — confirm that's the desired default for checks.

---

## When to build
Not for the current demo. Build when a **loan / KYC / onboarding** Decision App
is on the table — that's the use case that needs per-check review. The multi-API
plumbing is already there; this adds the per-check *verdict + review* layer on top
of the generic substrate.
