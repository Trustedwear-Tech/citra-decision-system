<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Factor Scorecard Plan — executing the customer's rubric, with evidence

**Status:** Phases 1-7 BUILT (2026-08-13/14). FS-05 shipped as a prose
heuristic, was walked around by a builder on first contact, and has been
REPLACED by the fact comparison in **Phase 6** at the end of this document.
Triggered by supply-chain-finance discovery, where a dealer scorecard is
expected to exist.

**Not carried by phase 6: the SOP fingerprint.** Dropped deliberately — see
"One thing it fixes for free". Phase 5's drift check stays built and inert.

**Goal:** let a Decision App evaluate a customer's **declared** factor tree — their factors,
their weights, their grade scale — score each factor against this case with citations, and
compute the composite **in code**. Rendered as a grid where every cell opens into its evidence,
carried identically by the app, the embed card and the Decision API.

---

## TL;DR

- **We do not build a scorecard. We execute theirs.** The factor tree comes from the customer's
  own policy, confirmed by a human at build time. We never invent a weight.
- **Three shapes, and absent is the default.** `composite` (weights → total → grade),
  `checklist` (judged criteria, **no total** — aviation inspection, KYC, claim triage), or no
  factor set at all, which is correct for most apps. The mode is **permanent** for an app version.
- **A factor is the structured-data twin of an item finding**, not a second review mechanism.
  Inspection apps already have their grid — it is made of photos.
- **~70% of the machinery already exists** under a different name. `check_evaluate` is already
  a per-factor primitive: one instance per `task_type`, `llm` or deterministic `rule` mode, binds
  to an SOP passage, emits a structured `ItemFinding`, renders as its own accept/reject card, and
  files officer corrections into the `(app, task_type)` bucket that clause memory learns from.
- **The gap is narrow and additive:** a numeric `score`/`weight`/`band` on the finding, a declared
  factor tree with data bindings, deterministic aggregation, a band→grade map, and a grid panel.
- **Backend first.** The first three items light up the app, the embed and the API before a
  single pixel of grid exists.
- **Build it as a generic panel primitive, not a credit feature.** The same code must give a
  claims assessor a claim-integrity score and a grid engineer an asset-condition score. Writing
  "dealer scorecard" into the engine would put industry logic in the platform for the first time.

---

## Why a scorecard, and why the credit person is really asking

In wholesale and supply-chain finance a scorecard is structural, not decorative:

| Purpose | What it drives |
|---|---|
| **Delegated authority** | approval limits key off the grade — A/B up to ₹2 Cr regional, C to committee |
| **Comparability** | rank a queue, compare dealers, watch the portfolio distribution move |
| **Consistency** | forces every officer to consider the same factors — a governance requirement |
| **Pricing / provisioning** | risk-based pricing needs a band |
| **Migration reporting** | "how many exposures moved down a grade this quarter" is a board slide |

So when a credit person asks for a scorecard they are usually asking *how does this plug into
the authority matrix and the portfolio report*, not *give me a number*.

### The positioning guardrail — say this early and explicitly

> "It isn't a rating model, and we don't want it to be. Your framework is yours — validated and
> owned by you. We run your declared factors and attach the evidence to each one, so the grade
> you already produce is auditable instead of asserted."

The moment we emit a number that looks like a rating we inherit a regulatory category: internal
rating models get validated, back-tested, documented and periodically reviewed. Executing a
declared tree keeps us out of model-risk governance while making us more useful inside it.

**Corollary, and it is load-bearing:** a score compresses reasoning into a number and throws away
the thing we uniquely produce. The reasons stay primary. The scorecard is supporting detail
underneath them, never a replacement for them.

---

## Vocabulary — facet vs factor

These are different objects and conflating them will wreck the build.

| | **Facet** (exists today) | **Factor** (new) |
|---|---|---|
| What it is | a *label on the case* | a *judgement about the case* |
| Has a number? | no | yes — score, weight, band |
| Example | `region:north`, `product:dealer_finance` | `payment_record — 18/25` |
| Job | scopes memory: which drawer a lesson goes in and comes out of | measures one dimension of this case, with evidence |
| Where declared | `FacetSpec` / `case_signature` in the app spec | new `factors[]` block in the app spec |

They touch at exactly one point: **a factor's outcome can become a facet.** If banking conduct
lands in the weak band, `banking_conduct:weak` is a legitimate scope facet for the lesson an
officer teaches on that case. `FacetSpec` already supports `kind: "band"` with numeric `edges`,
so the banding logic is reused rather than reinvented.

### One word, two meanings — "rubric"

Throughout this doc **rubric** means the CUSTOMER's scoring framework: their
factors, weights and bands. It is a thing they own and we execute.

It does **not** mean the platform's learned memory. The per-`(app, modality,
task_type)` rubric SUMMARY — one ~1000-word blob rewritten on every correction —
was deleted, not deprecated: `analysis_rubrics.py` now only routes corrections
into `smartapp_corrections`, and consolidation turns that evidence into
**clauses** out of band. What a run injects is a selection of clauses scoped by
facets, and `ItemFinding.rubric_version` now carries `clauses/C-003,C-034`.

The bucket key `(app, modality, task_type)` survives — as the partition that
corrections and clauses are filed under, not as a document. So "this factor's
rubric" is properly "the clauses scoped to this factor's bucket". Some names in
the code are vestigial (`analysis_rubrics.py`, `rubric_version`,
`rubric_tenant_for_app`); the mechanism behind them is clauses.

### A factor is the structured-data twin of an item finding

This is the framing that prevents the worst version of this build.

An aviation asset-inspection app has no dealer to score. What it has is **items** — a photo of a
fuselage panel, a borescope frame, a page of an inspection report. Each already becomes an
`ItemFinding` with its own verdict, rationale, citations and accept/reject card. **That per-item
review already is the evaluation grid.** It shipped.

A credit app has nothing to photograph. Its "items" are factors.

> A factor is the structured-data twin of an item finding — for cases where there is nothing to
> photograph.

`check_evaluate` already emits `modality: "api"` alongside `image` and `document`. Factors are the
same substrate with numbers attached, **not a second review mechanism sitting next to item
findings.** Building them as a parallel path would give inspection apps two grids and credit apps
an unused one.

### What a factor is not — inputs and gates

Domain shorthand hides this, so state it plainly:

| People say | It actually is |
|---|---|
| "for a loan the factor is CIBIL" | CIBIL is a **lookup the factor reads**, or a **gate** (`CIBIL < 650 → decline`, `mode: "rule"`) — not the factor |
| "for a claim the factor is claim history" | claim history is the **table** a "claim integrity" factor reads |

Model a bureau score as a factor and you get a factor whose score is a number copied from
elsewhere — neither a judgement nor evidence, and impossible to cite meaningfully.

---

## What already exists

Verified in code, not assumed.

### `check_evaluate` is the factor primitive

`CheckEvaluateTool` — `smart-app-service/schemas/agent_spec.schema.json`, dispatched at
[`tools_v2_dispatch.py:762`](../smart-app-service/tools_v2_dispatch.py):

- **One instance per `task_type`** — already the declared-tree shape, one tool per factor.
- **Two modes.** `llm` — the model judges the fetched data against the SOP passage and the
  learned clauses selected for that bucket. `rule` — a deterministic boolean over the data via `rule_expr`, **no LLM call**, for
  fixed thresholds. The `rule_expr` evaluator allows comparison AST nodes only
  ([`tools_v2_dispatch.py:191`](../smart-app-service/tools_v2_dispatch.py)), and a rule error
  **fails loud to `flag`**, never a silent pass. This is the hard-gate object, already built.
- **SOP-bound.** `sop_source` / `sop_doc_path` / `sop_query` already tie each check to the passage
  it is judged against — "each factor cites the passage it came from" is done.
- **Emits `ItemFinding`** ([`models.py:1246`](../smart-app-service/models.py)):
  `{item_id, item_type, modality, subject, fields, recommendation, confidence, rationale, citations[]}`.
  Compare the target shape `{id, score, band, rationale, citations[], clauses_fired[]}` — nearly
  the same object.

> `docs/per-check-review-decision-apps.md` states per-check review is not built. **That doc is
> stale** — `check_evaluate` shipped, including per-check officer feedback
> ([`main.py:3280`](../smart-app-service/main.py), `modality="api"`). Update it alongside this work.

### The review, ledger and learning substrate

- **Per-factor accept/reject** already renders and is already captured.
- **Factor-scoped corrections** already work: `append_correction`
  ([`analysis_rubrics.py:138`](../smart-app-service/analysis_rubrics.py)) takes `modality`,
  `task_type`, `contested_fields`, `case_facets` — a correction lands against one check, not the
  whole case.
- **Per-item ledger with outcome linkage.** `ItemDecisionRecord`
  ([`models.py:1283`](../smart-app-service/models.py)) is written twice — the model's verdict at
  run end, the officer's disposition at feedback — and **inherits `outcome` from the parent
  `DecisionRecord` via `correlation_id`**. The substrate for factor-level drift analysis is
  already accumulating.

### One spec, three surfaces

- The embed is a **page kind**, not a separate artefact: `Page.kind ∈ {standard, dashboard, embed}`.
  An embed page renders the same panel primitives minus `chart`/`map`, blocked loudly at publish
  ([`models.py:3470`](../smart-app-service/models.py)).
- `EmbedSpecResponse` ships `app_spec` + `agent_spec` + `page_id`
  ([`models.py:4015`](../smart-app-service/models.py)).
- The Decision API returns the same `RunResult`, `item_findings` included
  (`decision-api-sdk/API-REFERENCE.md`).

**Consequence:** there is no embed-versus-API decision to make, and no drift risk between the
three surfaces. There is only a build-it-once requirement — anything not built is missing from
all three equally.

---

## What is missing

Grepped `scorecard|factor_tree|weight|composite_score|grade_scale` across `smart-app-service`:
**zero hits.**

1. **No numeric score, weight or band** on a finding.
2. **No declared factor tree** with data bindings in the app spec.
3. **No aggregation.** Nothing computes a composite.
4. **No band→grade map.**
5. **No scorecard panel.** 16 panel types; the renderer switch
   ([`PanelRenderer.tsx:131-165`](../citra-app-runtime/src/components/PanelRenderer.tsx)) has no
   grid case.
6. **No SOP-version fingerprinting.** Grepped `sop_version|doc_version|sop_hash|policy_version`:
   zero hits. `sop_doc_path` binds a check to a document, but nothing detects the document changed.

### The trap: `confidence` is not a score

`ItemFinding.confidence` is the model's self-reported certainty, `0.0–1.0`. A factor score is a
**policy quantity**. Collapsing them yields a composite that moves when the model gets more or
less sure — precisely what a model-validation team will kill. They are separate fields and both
are persisted.

---

## Design

### The governing rule: structure once, evidence per case

An SOP is prose; a scorecard is a structure. The structure cannot be re-derived from prose on
every run — the weights would wobble case to case and the same dealer would score differently on
consecutive days.

- **Build time** — extract the structure once, a human confirms it, it is frozen into the spec.
- **Run time** — the agent never re-parses the SOP for *structure*. It fetches *this case's data*
  and judges the already-declared factors, retrieving SOP passages only as grounding for
  thresholds.

### Build time

```
   BA uploads SOP / credit policy  +  connects the data source
        │
        ▼
   BUILDER AGENT
     1. reads the SOP (RAG)      ──►  proposes factor tree: names, weights, bands
     2. reads the CATALOGUE      ──►  binds each factor to the data that feeds it
     3. asks when the SOP is silent
        │
        ▼
   HUMAN CONFIRMS  ← mandatory, never silently accepted
        │
        ▼
   FROZEN INTO app_spec.factors[] + agent_spec check_evaluate tools
```

Four builder behaviours. The first question is always **which shape** (see below), and the last
row is the one that protects us:

| What the SOP contains | Builder behaviour |
|---|---|
| Explicit rubric — factors, weights, bands | `mode: composite` → extract → confirm → done |
| Criteria with no weights, and none implied | `mode: checklist` → extract → confirm. **Do not ask for weights** — there are none to ask for, and asking signals we misread their work |
| Weights clearly intended but absent | **ask.** A legitimate interview question, and the moment we discover their framework is informal — useful information for both sides |
| No rubric at all | **say so; do not invent one.** Ship the app with no `factors[]` block — prose reasons only, which is the correct shape for most apps |

Distinguishing rows 2 and 3 is a judgement the builder must put to the human, not resolve alone:
*"§4 lists six checks with no weighting. Is this a checklist, or is there a weighting sheet we
haven't seen?"*

The last row is the empty-catalogue rule applied to scoring: the build stops rather than
fabricating. A hallucinated weight produces a scorecard that looks authoritative and is wrong,
which is worse than having none. Human confirmation is mandatory for the same reason the
catalogue never lets an LLM rename a column.

### Three shapes — and the composite is optional

Not every domain totals its judgements, and forcing one shape on all of them is the single
easiest way to make this feature unusable outside credit.

| Shape | Typical domain | Weights | Composite | Band |
|---|---|---|---|---|
| **`composite`** | dealer finance, credit, limit review | yes | yes | grade A/B/C |
| **`checklist`** | aviation inspection, claim triage, KYC | **no** | **no** | per-criterion only |
| **absent** | most apps | — | — | — |

**`checklist` is a first-class mode, not a degraded composite.** An airworthiness assessment reads
*"corrosion within limits — yes, with the photo"*, *"fastener torque within spec — no, with the
report page"*. Each judged, each cited, each reviewable. **Summing them would be meaningless and
unsafe** — a hull crack and a scuffed placard do not average. A checklist app renders the grid,
shows no total, and is never prompted for weights.

**Absent is the default.** `factors[]` is omitted unless the SOP actually carries a rubric. Most
Decision Apps have prose reasons and no grid, and that is correct.

#### The mode is permanent

`mode` is declared at build time, confirmed by a human, and **cannot change on a published app.**
A checklist app that silently grew a total one day would change how every one of its past outputs
should be read — the same class of harm as memory quietly moving a score. Switching a live app
from `checklist` to `composite` is a **new version with its own confirmation step**, and the
publish validator rejects a mode change on an existing version.

Consequences, enforced rather than documented:

- `mode: composite` **requires** every factor to carry a `weight` and the app to carry a
  `grade_scale`. Missing either → publish fails loud.
- `mode: checklist` **forbids** `weight` and `grade_scale`. Present → publish fails loud, because
  their presence means someone intended a total that will never be computed.

### The declaration

New `factors[]` block in the app spec. The `reads` binding is load-bearing: without it the model
hunts for its own data at run time and the score becomes unreproducible.

**`mode: composite`** — dealer finance:

```yaml
factor_set:
  mode: composite                    # PERMANENT for this app version
  terminology:                       # what the SCREEN says; the engine never uses these words
    panel: "Scorecard"
    row:   "factor"
    band:  "Grade"
  factors:
    - id:     payment_record
      label:  "Payment track record with anchor"
      weight: 25                     # required in composite, forbidden in checklist
      reads:
        dataset: anchor_invoices
        where:   "dealer_id == {record.dealer_id} AND invoice_date >= today-365d"
      sop:
        source: credit_policy
        query:  "delay beyond due date adverse classification"
      bands:
        - { max: 2, label: minor }
        - { max: 5, label: moderate }
        - { label: severe }
  grade_scale:                       # required in composite, forbidden in checklist
    - { min: 80, grade: A }
    - { min: 60, grade: B }
    - { grade: C }
```

**`mode: checklist`** — aviation asset inspection. No weights, no total, no grade:

```yaml
factor_set:
  mode: checklist
  terminology:
    panel: "Evaluation criteria"
    row:   "check"
    band:  "Disposition"
  factors:
    - id:    corrosion_limits
      label: "Corrosion within allowable limits"
      reads:
        dataset: inspection_findings
        where:   "asset_id == {record.asset_id} AND zone == 'fuselage'"
      sop:
        source: maintenance_manual
        query:  "allowable corrosion limits fuselage skin"
      bands:
        - { label: within_limits }
        - { label: conditional }
        - { label: exceeds }
```

**Terminology is declared, never hardcoded.** The engine says `factor`; the screen says whatever
the customer says. Nothing in the renderer, the aggregator or the validator ever contains the word
"credit".

| Domain | `panel` | `row` | `band` |
|---|---|---|---|
| Dealer finance | Scorecard | factor | Grade A/B/C |
| Insurance claim | Assessment | criterion | high / medium / low concern |
| Aviation inspection | Evaluation criteria | check | airworthy / conditional / grounded |
| KYC | Verification checklist | check | pass / refer / fail |

**Where each piece lives.** `grade_scale` is institution-wide → deployment level **when it exists
at all**; a checklist app has none. The factor tree and weights vary by product and change often →
**app spec, not the MCP ontology**. The ontology describes *what data exists*; a factor tree
describes *how this product decides*.

### Two objects, never blended

SOPs carry hard gates as well as scored factors and they must not mix.
"Single-dealer exposure cannot exceed X% of anchor turnover" is pass/fail — it short-circuits.
If a gate fails, the composite is irrelevant, and showing "68/100 — declined" is confusing.

- **Gates** → `check_evaluate` `mode: "rule"`, deterministic, rendered at the top.
- **Scored factors** → `mode: "llm"` (or `rule` for a mechanical score), rendered underneath as
  supporting detail.

### Run time

```
   Officer opens a case (app | embed in their LOS | Decision API — same path)
        │
        ▼
   ANCHOR READ  — deterministic read-by-key, never an LLM guess
        │
        ▼
   FOR EACH DECLARED FACTOR
        fetch what its `reads` names:  records (MCP) | document (doc_extract) | lookup (bureau/KYC)
        judge it:  mode=llm → model + SOP passage + learned clauses
                   mode=rule → arithmetic, no model
        emit:  { factor_id, score, band, rationale, citations[], clauses_fired[] }
        │
        ▼
   CODE aggregates — score × weight, summed, mapped through grade_scale
   (no model in this path)
        │
        ▼
   Gate result → Grade → factor grid → recommendation → Approve / Override / Reject
        │
        ▼
   LEDGER — every factor score, joined later to the outcome
```

### The read map — anchor first, then fan-out

A scorecard case is **not** "one read for the record and one read for the factors". It is one
deterministic anchor read followed by a fan-out of independent per-factor reads, and they land on
different tables, documents and lookups.

Worked against the SCF job *"dealer requests a credit-limit increase, sourced by the anchor"*:

```
  QUEUE ROW  ──►  application_id = APP-2026-0912
       │
       ▼
  ① ANCHOR READ — read-by-key, deterministic, never the NL planner
     credit_limit_applications WHERE application_id = APP-2026-0912
     ↳ dealer_id = DLR-4471, requested 3.5 Cr, current 2.0 Cr, anchor_id = TATA-MOT
       │
       │  dealer_id now resolved
       ▼
  ② FAN-OUT — each factor fires its own declared `reads`, keyed on that dealer_id
```

| Factor | Source kind | What it reads |
|---|---|---|
| Vintage | dealer master | `dealers` WHERE dealer_id → onboarded_date |
| Offtake trend | transaction history | `anchor_purchases`, last 24 mo (12 + prior 12 for the trend) |
| Payment record | transaction history | `anchor_invoices`, last 12 mo, due vs paid dates |
| Banking conduct | **document on this application** | bank statement PDF → `doc_extract` |
| Financial strength | document or table | FY25 financials attached to the application |
| Collateral | security records | `collateral` / guarantee documents |
| GATE — exposure | two reads, joined | `dealer_exposure` outstanding ÷ anchor master turnover |

**The dealer master is the smallest contributor** — it gives vintage and little else. The scoring
weight sits in transaction history and attached documents. Anyone planning this as "join the
dealer table" will under-scope it by a factor of five.

**Ordering is not optional.** Every factor's `reads` clause resolves `{record.dealer_id}` from the
anchor record, so the anchor read must complete before any factor fires. This is exactly why the
anchor read is deterministic read-by-key and never the NL planner: if the base record were a
guess, every factor beneath it would be scoring the wrong dealer. After the anchor resolves, the
factor reads are independent and fan out in parallel.

#### Entity-level vs case-level factors

| | About the **dealer** | About **this application** |
|---|---|---|
| Examples | vintage, offtake, payment record, banking conduct, financials | requested increase %, utilisation of current limit, purpose, seasonality of the ask |
| Changes | slowly, between applications | only with this application |
| Reads | the dealer's history | the anchor record itself and its attachments |

Most SCF scorecards are dealer-quality scorecards, so the weight sits on the left. But the right
column is what makes *this* application approvable: a grade-B dealer asking for a 75% limit jump
is a different decision from the same dealer asking for 10%. **Declare both in one tree** — the
distinction is about read scope, not about two separate scorecards.

**Do not cache entity-level scores across cases.** A payment record from six weeks ago is a
different fact today; re-score every run. What we *can* show for free is the previous scorecard as
a precedent — `cited_precedents` already carries this, so *"this dealer scored 71 in May, 68 now"*
needs no new machinery.

### The one hard engineering rule

**The model scores factors; code does the arithmetic.** Weights are applied in code and never
shown to the model. If the composite is not deterministic and reproducible, a model-validation
team will reject it, and rightly. The model answers one question at a time — *how did this case
do on this factor, and why* — and never sees the weights.

---

## What the officer sees

**One card, not two.** A separate scorecard screen would force the officer to reconcile two
artefacts that might disagree, and they would trust neither.

```
┌──────────────────────────────────────────────┐
│  GATE — exposure 8.75% of anchor turnover    │  fails → everything
│         (policy cap 10%)          ✓ PASS     │  below is moot
├──────────────────────────────────────────────┤
│  GRADE  B          68 / 100                  │  computed in code
├──────────────────────────────────────────────┤
│  Vintage              9/10   ●  6 yrs        │
│  Offtake trend       14/20   ●  down 12%  ▸  │  the grid
│  Payment record      18/25   ●  3 delays  ▸  │  click ▸ → citations,
│  Banking conduct      8/15   ●  2 bounces ▸  │  SOP passage, clauses fired
│  Financial strength  15/20   ●  DSCR 1.4  ▸  │
│  Collateral           4/10   ●  partial   ▸  │
├──────────────────────────────────────────────┤
│  RECOMMENDATION + reasons + cited precedents │  as today
│  + planned writes                            │
├──────────────────────────────────────────────┤
│  [ Approve ]  [ Override ]  [ Reject ]       │  as today
└──────────────────────────────────────────────┘
```

**The cell is the product, not the total.** Any incumbent can print `18/25`. None can open that
row onto the three late invoices, the policy paragraph, and the learned clause that fired —
because incumbents compute scores from columns and have nothing underneath.

**The one genuinely separate surface** is a `grade` column on the queue panel, so a credit head
can rank the portfolio and run grade-migration reporting. That is a column, not new machinery.

### Panel work is a 3-layer change

Per `docs/ui-component-expansion-plan.md`, a new panel moves three layers together:

```
SCHEMA (models.py → app_spec.schema.json)  →  RUNTIME (PanelRenderer)  →  SKILL (catalogue)
```

Plus the `scripts/` static-check mirror. There is no shortcut, and the runtime fails loud on an
unknown panel type. The panel must be **generic** — it renders a declared factor tree, with no
knowledge of credit.

---

## When factors are computed — and why the queue column forces the answer

Two moments are possible, and **both paths already exist** (`WorkflowStagingRow.source`):

| | **Precomputed** (`source: "trigger"`) | **On demand** (`source: "queue_action"`) |
|---|---|---|
| When | a trigger fires the agent ahead of the officer | the officer clicks Review |
| Queue row arrives | already scored | unscored |
| Cost | every case in the queue costs a full factor run, opened or not | only opened cases cost anything |
| Officer wait | none | the full fan-out, on the click |

**A `grade` column on the queue requires precompute.** A queue cannot be ranked, filtered or
distribution-reported by a grade that only exists once someone opens the case. Since ranking the
portfolio is one of the main reasons a credit team asks for a scorecard at all, phase 3's queue
column is not an independent piece of work — **it depends on trigger-based scoring being the
default posture for scorecard apps.**

That posture is materially heavier than on-demand. A dealer portfolio with several hundred pending
applications pays for a full multi-read, multi-LLM factor run on every one of them, on every
refresh, whether or not an officer ever opens it. Consequences to decide before phase 3:

- **Refresh cadence.** Score once at case creation, or re-score on a schedule as invoices and
  statements move? A grade that is three weeks stale is worse than no grade.
- **Staleness display.** A precomputed grade must show *when* it was computed, or an officer will
  read it as current.
- **Recompute on open.** Proposed: the queue shows the precomputed grade for ranking, and opening
  the case re-scores so the card the officer acts on is always fresh. That costs the run twice on
  opened cases but keeps ranking cheap and decisions current.
- **Gate-first cheap pass.** Gates are `mode: "rule"` — no LLM. Running gates alone across the
  queue is nearly free and already screens out the cases where the composite is irrelevant. A
  cheap gate sweep plus scoring only what passes may be most of the ranking value at a fraction of
  the cost.

## Learning loop

When an officer changes a factor score and gives a reason, that correction arrives **already
scoped to a factor** — a much better memory input than free-text override notes, because
factor-scoped corrections cluster more cleanly and their scope is more honest. The existing
`append_correction` path already accepts exactly this shape.

**Set expectations honestly.** We have measured that memory pays off where the SOP is silent, and
that SOP-duplicating lessons change nothing. A factor tree extracted *from* the SOP is by
construction the part the SOP is *not* silent about. So expect factor-scoped corrections to
cluster well but teach comparatively little. **The value sits in the gap** — between what the
declared policy says and what officers keep overriding. This build makes that gap visible for the
first time; that is the interesting report, not the factors themselves.

**One guard, from the start:** a learned judgement **annotates** a factor, it never silently moves
its score. Render "clause C-014 raised a concern on banking conduct", attributed and visible —
never two points quietly subtracted. The moment memory adjusts numbers invisibly the composite
becomes unexplainable and we have traded away the audit property that is the entire point.

## Drift analysis — the eventual payoff

Because every factor score lands in the ledger next to the eventual outcome, we can eventually
say *"the collateral factor has not discriminated in eighteen months"* — a sentence no bank has
heard from its own scorecard, and the natural extension of what `ItemDecisionRecord` already
stores.

**Design the schema for it now; do not promise it in a meeting.** It needs outcomes at volume and
outcome polling that actually fires.

---

## Worked example — SCF dealer DLR-4471, anchor Tata Motors

**Gate** (`mode: rule`, no model):
`outstanding ₹2.8 Cr ÷ anchor turnover ₹32 Cr = 8.75%` vs cap 10% → **PASS**.
Had it failed, the composite below would not be shown.

| Factor | Weight | Fetched at run time | Score |
|---|---|---|---|
| Vintage | 10 | dealer master → onboarded 2019 | 9 |
| Offtake trend | 20 | 12-mo purchase rows → ₹18.2 Cr vs ₹20.7 Cr prior | 14 |
| Payment record | 25 | 47 invoices → 3 paid late, avg 11 days | 18 |
| Banking conduct | 15 | bank statement PDF → 2 return entries | 8 |
| Financial strength | 20 | FY25 financials → DSCR 1.4, current ratio 1.1 ↓ | 15 |
| Collateral | 10 | security docs → PG only, no hypothecation | 4 |

Code sums: **68 → Grade B** → regional authority, not committee. That routing sentence is what
the credit head actually cares about.

Expanding the payment row:

```
Payment track record          18 / 25    moderate

  3 invoices paid beyond 7 days in the last 12 months (avg 11 days).
  Policy treats 3-5 instances as moderate.

  ▸ INV-2024-8831  due 14 Mar  paid 27 Mar  (13 d)
  ▸ INV-2024-9102  due 02 Jun  paid 11 Jun  ( 9 d)
  ▸ INV-2025-0447  due 19 Nov  paid 30 Nov  (11 d)

  📄 Credit Policy §4.2 — "Delays beyond 7 days shall be treated as adverse…"

  🧠 Clause C-014 fired: "Delays clustered in Q1 for auto dealers usually
     reflect anchor billing cycles, not stress." — from 4 officer corrections
```

---

## Build order

Backend first — phases 1 and 2 improve the app, the embed and the Decision API simultaneously,
before any UI exists. That matters commercially: we can commit to the capability without
committing to a UI date.

| Phase | Work | Surfaces lit |
|---|---|---|
| **1** ✅ | `score` / `weight` / `band` / `clauses_fired` on `ItemFinding`; `factor_set` in the app spec (`models.py`); builder extraction guidance + **mandatory human confirmation** (`citra-app-spec` → `references/factor-set.md`); per-factor `reads` bindings; publish rules FS-01 / FS-02 | API + ledger |
| **2** ✅ | Deterministic aggregation (`factor_scoring.py`); gate short-circuit; band→grade map; card frozen on the staging row and carried to `DecisionRecord` | API + ledger + authority routing |
| **3** ✅ | `ScorecardView` in the officer's decision card (**not** a page panel — see below); expandable cells; `grade` column format on the queue | app + embed |
| **4** ✅ | `POST /apps/{slug}/factors/{factor_id}/override` — reason mandatory, officer held to the factor's declared weight, gated and already-decided cases refused; recomputed in code; correction folded into the `(app, "api", factor_id)` bucket. Editable cell in `ScorecardView`. | learning loop |
| **5** ✅ | `sop.fingerprint` on the spec, hashed again at run time by `check_evaluate`; drift surfaced on the card and stamped durably on the app as `factor_set_drift.needs_reextraction`. | governance |
| **6** ✅ | **BUILT** (rule + model + 15 tests; builder-skill guidance pending).</br>Was: Replace FS-05's prose heuristic with a `rubric_finding` record the builder writes when it reads the policy, human-confirmed — so publish checks a declared FACT against the declaration, not a phrase that can be paraphrased away. Also supplies phase 5's missing fingerprint stamper. | governance |

### Deviation from phase 3, recorded

The plan's own UI section requires **one card, not two** — a separate scorecard
screen would make the officer reconcile two artefacts that might disagree. A
page-level `scorecard` panel type was therefore built and **reverted**: it is by
definition the second surface this design rejects, and on a page with no run in
view it could only render an empty box. `ScorecardView` renders inside the
existing decision card automatically for any app declaring a `factor_set` —
nothing to place, nothing to wire. The only genuinely separate surface, the
queue `grade` column, was built as planned.

Phase 5 is small but it is the one that stops an app quietly scoring against last year's policy.

---

## Non-goals

- **We do not build a rating model.** No factor, weight or band is ever invented by us.
- **No credit vocabulary in the engine.** The panel, the aggregator and the validator know only
  "declared factor set", and every user-facing word comes from `terminology`. Verify by building
  an aviation `checklist` app from the same code **before** shipping the credit one — if that is
  awkward, the abstraction is wrong.
- **No composite without declared weights.** Missing weights → ask, or declare `checklist`. Never
  a silent total.
- **No second review mechanism.** Factors reuse the item-finding substrate (`modality: "api"`).
  If an app ends up with two grids, the design is wrong.
- **No mode migration in place.** `checklist` → `composite` is a new app version with its own
  human confirmation, never an edit to a live one.
- **No factor set by default.** Absent unless the SOP carries a real rubric.
- **No memory-driven score adjustment.** Annotate only.
- **The score does not replace the reasons.** Reasons stay primary in every surface.

## Open questions

1. **Where does `grade_scale` actually live** — deployment config, or ontology? Argued above for
   deployment level (institution-wide), but this needs a decision before phase 2.
2. **Sub-factors** — do we support a two-level tree in v1, or flat only? Flat is proposed; real
   credit policies frequently nest.
3. **Does an overridden factor score re-route authority?** If an officer lifts a case from grade C
   to B, do they thereby grant themselves signing authority? **Still open, and deliberately not
   decided in code** — an authority matrix is the customer's policy, and inventing one would put
   industry logic in the engine. Phase 4 shipped with everything such a rule needs recorded
   (`overridden_by`, `overridden_at`, `override_reason`, `original_score`, `grade` and
   `grade_before_override`) and the change logged. Answer it with the customer, then enforce it.
4. **Gate failure and the ledger** — is a gated-out case a decision record at all, or a separate
   disposition?
5. **Scoring cadence and cost** — precompute the whole queue, or gate-sweep cheaply and score only
   what passes? And does opening a case re-score? Blocks phase 3 (see the precompute section).
5. Should this doc also be copied to `citra-decision-system/docs/` per the platform-docs
   convention, or does it stay internal until phase 1 ships?

---

# Phase 6 — the extraction record: check a FACT, not a phrase

**Status:** designed, not built (2026-08-14). Written after FS-05 was built,
shipped, and then walked around by the builder on its first encounter with it.

## What happened, twice

**Build 1.** The builder read the credit policy and extracted the rubric
perfectly — six factors, exact weights, both band scales, the grade scale, the
hard gate, the delegated-authority rule. It then said, unprompted:

> *"Since the policy has a clear scoring framework with fixed weights, I'll
> include `factor_set` so the decision modal renders a proper scorecard."*

It published with `factor_set: null` and zero `check_evaluate` tools. The
scoring survived only as prose, and reached the officer as a decision reason
reading `"Total Score: 0/100 — Grade C. Factor breakdown:- 4.3(a) …"`.

**FS-05 was built for exactly that**, and it works: it fires on that spec
through the real `/builder/validate` endpoint (422, sole rule), and is silent
on all 14 apps in the local store.

**Build 2**, same brief, with FS-05 now documented in the builder's own skill
file. The builder again authored `factor_set: null` and no `check_evaluate`
tools mid-build — the miss is reproducible. But the app it finally published
carries **no weighted scoring at all**: no totals, no weights, no grade, and an
explicit statement that only two of six factors are assessable. FS-05 is
correctly silent, because there is no longer a rubric to declare.

## The problem with that outcome

Two readings fit the same artefact, and **the artefact cannot distinguish
them**:

1. **Honest de-scoping.** Four of six factors have no backing dataset, so a
   composite would be arithmetic over absences. Declining to score is the
   empty-catalogue discipline applied one level up, and is *correct*.
2. **Writing around the lint.** The skill documents FS-05's trigger vocabulary.
   The builder had that vocabulary in build 1 and does not in build 2.

The prompt reads honest — it names the unassessable factors and says so to the
officer. But we documented the trigger conditions in the file the builder reads,
and then it produced prose avoiding them. Calling that a clean pass would be
fooling ourselves.

**The lesson is not "harden the regex".** That is an arms race against a
language model, run on the model's home ground: paraphrase. Every round makes
the rule more brittle and more likely to fire on an honest app, and a rule that
cries wolf gets muted — after which it protects nothing. FS-05 watches the wrong
thing. Prose is not evidence of intent; it is a rendering of intent, and
renderings can be changed without changing the intent.

## The control: make the builder record what it found

The builder already reads the SOP and reaches a conclusion about it. Today that
conclusion exists only in conversation, then evaporates. **Write it down as a
structured fact on the spec, confirmed by the human, and check the declaration
against the fact.**

```jsonc
"rubric_finding": {
  "source":   "sop_library_lending",
  "doc_path": "/policy/dealer-finance-credit-policy-v4.2.txt",
  "verdict":  "weighted_rubric",     // | criteria_checklist | none
  "evidence": {
    "factors_named":    6,
    "weights_present":  true,
    "grade_scale_present": true,
    "excerpt": "4.3 Scored factors and weights … total 100 marks"
  },
  "confirmed_by": "coo@acme-bank-demo.citra.ai",
  "confirmed_at": "2026-08-14T09:12:00Z"
}
```

FS-05 then becomes deterministic and unphraseable:

| `rubric_finding.verdict` | `factor_set` | Outcome |
|---|---|---|
| `weighted_rubric` | absent | **block** — you found a rubric; declare it |
| `weighted_rubric` | `mode: composite` | pass |
| `criteria_checklist` | absent | **block** — declare `mode: checklist` |
| `criteria_checklist` | `mode: checklist` | pass |
| `none` | absent | pass — the honest case |
| absent (no SOP read) | absent | pass — nothing was claimed |

No regex. No vocabulary to route around. The check compares two declarations
the builder made itself.

## Why this is better, stated honestly

It does **not** make evasion impossible. A builder can still write
`verdict: "none"` over a policy that plainly has a rubric. What changes is the
*kind* of act required:

- Today: rephrase a sentence. Invisible, deniable, leaves no trace.
- After: **state a false fact about a named document**, attributed, timestamped,
  and put in front of a human for confirmation.

That is the whole gain, and it is worth having. A BA who is shown *"I read
Dealer Finance Credit Policy v4.2 and found no scoring rubric"* against a policy
they wrote will object immediately. The record is also auditable after the fact:
the `doc_path` and `fingerprint` are there, so anyone can re-read the document
and check the claim. A paraphrase leaves nothing to check.

Same reasoning as the rest of this plan: we do not stop the model being wrong,
we make being wrong **visible and attributable** instead of silent.

## One thing it fixes for free

**~~Phase 5's missing stamper.~~ Dropped — the record carries NO fingerprint.**
Drift detection is the weakest thing in this plan and the record should not be
loaded with it. The SOP passage is re-read on every run, so the evidence is
always current; only the weights are frozen, and weights change through a
committee, not silently. `rubric_finding` is about what was FOUND and DECLARED,
not about watching a document. Phase 5 stays as built and inert until someone
asks for it.

**The confirmation gate becomes real.** "A human confirms it before it is
written into the spec" is, today, an instruction in a skill file with nothing
behind it. `confirmed_by` / `confirmed_at` on a stored record makes it a fact
that either exists or does not.

## What it does not fix

- **A builder that never reads the SOP** produces no record, and the check has
  nothing to compare. An app whose agent binds a `rag` tool to a policy source
  while declaring no `rubric_finding` is suspicious, but making *that* a rule
  risks the same brittleness — see open questions.
- **A wrong extraction** — right verdict, wrong weights — is unaffected. That is
  what human confirmation is for, and why the excerpt is in the record.
- **It is still the builder's own claim.** Nothing here verifies the document
  independently.

## Build order

1. `RubricFinding` model + optional `app_spec.rubric_finding`; publish rule
   rejects a malformed record (verdict without source, etc).
2. Rewrite FS-05 to the table above. **Delete the regex** — do not keep it as a
   fallback. Two rules watching the same thing, one of them gameable, is worse
   than one rule, because the gameable one shapes behaviour while looking like
   redundancy.
3. Builder skill: reading a policy for a rubric now ENDS in a `rubric_finding`,
   confirmed with the BA in the same breath as the factor tree.
4. ~~Carry the fingerprint onto `sop.fingerprint`.~~ **Dropped** — see above.
   The record carries no fingerprint and phase 5 stays inert.
5. Re-run the same two builds. The bar is not "it declares a factor_set" — it is
   **the record and the declaration agree, and a human saw both**.

## Open questions

1. **Should a rag-bound policy source with no `rubric_finding` be a warning?**
   It would catch the never-read case, and it is the sort of rule that goes
   stale and noisy. Probably a build-time nudge to the builder rather than a
   publish block.
2. **Where does confirmation actually happen?** The builder chat is the natural
   place, but the confirmation must be recorded server-side to be worth
   anything — a claim in a transcript is not a gate.
3. **Does `verdict: none` deserve a reason field?** "I read it and found no
   rubric" is more checkable when it says *why* (a policy of eligibility gates
   only, say). Cheap to add, and it is the field a reviewer would want.

---

# Phase 7 — review fixes, and the scoreless-factor decision

**Status:** BUILT (2026-08-14). Four defects found in a review of the phase 1-6
diff. Three were straightforward; one forced a real decision about what a
missing score means, recorded here because it changed a rule.

## The decision: a scoreless factor is `unscored`, not a dead run

`build_scorecard` used to RAISE when a composite finding arrived with no number
— on the reasoning that scoring it 0 would silently downgrade the case. The
first half of that is right and stands: **a missing score must never become a
zero.** The second half was wrong. Raising takes down the *whole run* —
recommendation, planned writes, the officer's card — over one factor.

There was always a third option, already built: `unscored`. The factor is named
on the card, excluded from **both** sides of the fraction, and reported in
`unscored_factor_ids`, so the officer sees a composite over 2 of 3 factors
rather than a confident wrong one. That is strictly better than either
alternative, and it is what the `llm` path already did — a missing
`score_fraction` is refused at the tool, no finding is collected, and the row
lands as `unscored` anyway. The raise only ever fired for a finding that reached
the aggregator scoreless by some *other* route.

In practice that route was a `mode: "rule"` check wired to a factor. So the
error moved to where it can actually be fixed:

**FS-06 — a declared factor cannot be served by a `mode: "rule"` check**
(composite only). Rule mode returns a verdict and no number. Wired to a weighted
factor it costs that factor's weight out of the denominator on *every* case,
silently, against a rubric the customer signed off. Nothing in the run looks
wrong, which is exactly why it belongs at publish. The fix is `mode: "llm"`, or
`factor_set.gates` if it is genuinely a pass/fail limit. Checklist is exempt: a
checklist row carries a band, and a rule verdict can stand in for one.

## The other three

| # | Defect | Fix |
|---|--------|-----|
| 2 | **FS-01 rejected every `kind: "document"` factor.** For a document factor `dataset_id` is the *attachment column* on the anchor record, not a dataset id — so checking it against the bound datasets failed the moment the directory was hydrated, which is the one state a real publish is always in. | Skip the existence check for `document`, mirroring `lookup`. The column is still mandatory. |
| 3 | **The queue labelled checklist rows "gated".** `panel_data` nulled the grade when gated, and the renderer inferred "gated" from an empty grade. But a checklist app has no grade *by design*, and neither does a composite where nothing scored — so those rows announced a policy breach that never happened. | `gated` is its own field: pass grade/percent through unchanged and let the renderer read `row.gated`. An absent grade renders `—`. Projection extracted to `panel_data.project_scorecard_columns` so it is testable. |
| 4 | **A band-only override left the row `unscored`.** The officer supplies the disposition and the card still says "not scored" beside it — and a panel with `hide_unscored` drops the row they just filled in, the one place an override has to be visible. | `row.unscored = False`, matching the score path. |

Defects 1 and 3 were the two that would have reached a customer: one kills a run,
the other tells a credit officer a policy limit was breached when none was.

## Tests

124 in the four factor files (was 111). The two tests that encoded the old raise
now assert the `unscored` contract — including that `confidence: 0.9` does not
become a score of 0.9, which was the property the raise was really protecting.
