# Factor set — executing the customer's rubric

> Reference for `app_spec.factor_set`. Read when the SOP contains a scoring or
> assessment framework. See `docs/factor-scorecard-plan.md` for the reasoning
> behind every rule here; **the code is the contract** — `models.py`'s
> `FactorSet` / `FactorSpec` and publish rules FS-01 / FS-02 win over this file.

## What a factor is — and is not

A **factor** is one judged dimension of a case, with a number and its evidence:
`payment_record — 18 of 25, because 3 invoices were paid beyond 7 days (cited)`.

It is **not** a facet. A facet (`case_signature`) is a *label* that scopes
memory; it has no number and measures nothing. Two different objects — see
`references/case-signature.md`.

It is also not the *input* it reads. Domain shorthand hides this:

| The BA says | What you declare |
|---|---|
| "the factor is CIBIL" | a factor `bureau_standing` whose `reads` is the bureau lookup — **or** a gate, if policy says `CIBIL < 650 → decline` |
| "the factor is claim history" | a factor `claim_integrity` whose `reads` is the claims history dataset |

Model a bureau score *as* the factor and you get a factor whose score is a
number copied from elsewhere: neither a judgement nor evidence, and impossible
to cite.

**A factor is the structured-data twin of an item finding.** Each one is served
by a `check_evaluate` tool in the AgentSpec, one per factor, emitting
`modality="api"`. An inspection app already has its grid and it is made of
photos — do not add a factor set to score images.

> **"Rubric" here means the CUSTOMER's framework** — their factors, weights and
> bands. It does not mean the platform's learned memory. The old per-bucket
> rubric summary is gone; officer corrections become **clauses**, and a run
> injects the clauses scoped to that bucket by facets. A few names are
> vestigial (`rubric_version` on a finding now holds `clauses/C-003,C-034`).

## THE RULE: we never invent a rubric

The factor tree comes from the customer's policy document, and a human confirms
it before it is written into the spec. A hallucinated weight produces a
scorecard that looks authoritative and is wrong — worse than having none.

| What the SOP contains | What you do |
|---|---|
| Explicit rubric — factors, weights, bands | `mode: "composite"` → extract → **confirm with the BA** → declare |
| Criteria with no weights, and none implied | `mode: "checklist"` → extract → confirm. **Do not ask for weights** — there are none, and asking signals you misread their work |
| Weights clearly intended but absent | **Ask.** "§4 names six factors but no weighting. Where does that live?" |
| No rubric at all | **Declare no `factor_set`.** Say so plainly. This is the empty-catalogue rule applied to scoring |

Rows 2 and 3 are a judgement you put to the human, never resolve alone:
*"§4 lists six checks with no weighting. Is this a checklist, or is there a
weighting sheet we haven't seen?"*

**Absent is the default.** Most apps have prose reasons and no grid. Do not add
a `factor_set` because the app is in finance.

## Two modes, and the choice is permanent

| | `composite` | `checklist` |
|---|---|---|
| Typical | credit, dealer finance, limit review | aviation inspection, KYC, claim triage |
| `weight` on each factor | **required** | **forbidden** |
| `grade_scale` | **required** | **forbidden** |
| Output | total + percentage + grade | rows only, **no total** |

`checklist` is a first-class mode, not a degraded composite. A hull crack and a
scuffed placard do not average, so summing them would be unsafe.

**FS-02 rejects a mode change on a published app.** Every past decision was
recorded under the old mode; a checklist row that meant "judged, not scored"
must not retroactively read as a component of a grade nobody computed. If the
customer really wants to switch, that is a new app.

## Shape

```jsonc
"factor_set": {
  "mode": "composite",
  "terminology": {                       // what the SCREEN says
    "panel": "Scorecard", "row": "factor",
    "band": "Band", "composite": "Grade"
  },
  "gates": [                             // pass/fail, evaluated FIRST
    { "id": "exposure_cap", "label": "Single-dealer exposure cap" }
  ],
  "factors": [
    {
      "id": "payment_record",
      "label": "Payment track record with anchor",
      "weight": 25,                      // ALSO the max score: 18/25
      "scope": "entity",                 // "entity" = the counterparty's history
      "reads": {                         // ← load-bearing, see below
        "kind": "dataset",
        "dataset_id": "anchor_invoices",
        "where": "dealer_id == {record.dealer_id} AND invoice_date >= today-365d"
      },
      "sop": { "source": "credit_policy",
               "query": "delay beyond due date adverse classification" },
      "bands": [                         // score-based: code assigns
        { "label": "minor",    "max": 20 },
        { "label": "moderate", "max": 10 },
        { "label": "severe" }            // last band never carries "max"
      ]
    }
  ],
  "grade_scale": [                       // "min" is a PERCENTAGE of the
    { "min": 80, "grade": "A" },         // attainable maximum, not a raw total
    { "min": 60, "grade": "B" },
    { "grade": "C" }                     // last step never carries "min"
  ]
}
```

### `reads` is not optional detail

Without it the model hunts for its own data and the same case scores
differently on consecutive days. **FS-01 rejects a factor bound to a dataset the
app does not have** — it would produce no finding on every case, and under
`composite` the case still renders a confident grade over a partial rubric.

`where` interpolates the anchor record as `{record.<field>}`. The anchor read
resolves first and every factor fans out from it, so a factor cannot reference
a field the base record does not carry.

Three `kind`s: `dataset` (needs `dataset_id`), `document` (`dataset_id` is the
attachment column on the anchor record), `lookup` (needs `tool_name`).

FS-01's dataset-existence check applies to `kind: "dataset"` only. A `document`
factor still has to name the column it reads, but that column is a field on the
anchor record, not a dataset id, so it is not checked against the bound
datasets. Same for `lookup`, which names a tool.

### Bands: pick one style per factor

* **Score-based** — every band but the last carries `max`, an upper bound on the
  factor's own score. **Code** assigns the band, so two cases with the same
  score always band identically.
* **Label-only** — no band carries `max`. The evaluator picks from the closed
  set, because the threshold lives in the SOP's units (days late, a ratio) which
  score space does not carry.

Mixing them in one factor is rejected: nobody could say who decided the band.
An evaluator returning a label you did not declare is an error, never a new
band.

### `scope` — whose history is this?

`entity` (default) measures the counterparty: vintage, payment record, banking
conduct. `case` measures *this* application: requested increase, utilisation of
the current limit. Declare both in one tree — the distinction is read scope, not
two scorecards. It is display and analysis only; both score identically.

## Gates are a different object

"Single-dealer exposure cannot exceed X% of anchor turnover" is pass/fail. It
short-circuits: a failed gate suppresses the composite entirely, because
"68/100 — declined" invites an argument with the number instead of a reading of
the gate.

Serve each gate with a `check_evaluate` tool in **`mode: "rule"`** — no LLM,
`rule_expr` over the fetched data, and a rule error fails loud to `flag`. **An
unevaluated gate flags rather than passes**: a limit that quietly stopped being
enforced is the failure nobody notices.

## The AgentSpec side

Declare one `check_evaluate` tool per factor and per gate, with `task_type`
equal to the factor/gate `id`. **This is load-bearing, not a convention**: the
runtime looks the factor up by `task_type`, and a tool whose task_type does not
match a declared factor id is treated as an ordinary check — it produces no
score, and under `composite` the factor renders as "not scored". In the
system prompt, instruct the agent to:

* judge the factor against the fetched data and the cited SOP passage and
  return **`score_fraction`** — 0.0 to 1.0, *how fully this factor is met*.
  **You do not tell the model the weight.** The runtime multiplies the fraction
  by the declared weight in code, so the weight lives in exactly one place; put
  "score out of 25" in a prompt and it silently disagrees with the spec the
  moment anyone re-weights. Under `mode: "checklist"` the model returns a
  **`band`** from the declared set instead, and no number at all;
* return the rationale and citations that justify the number;
* **never** compute a total, a percentage or a grade. The arithmetic is done in
  code from the declared weights, and the model never sees them. If the prompt
  asks the model for a composite, the score stops being reproducible and a
  model-validation team will reject the whole thing.

`score` is **not** `confidence`. Confidence is the model's certainty; score is a
policy quantity. Both are returned, separately.

### The factor's check must be `mode: "llm"`

**FS-06 rejects a `mode: "rule"` `check_evaluate` whose `task_type` is a declared
factor, under `composite`.** Rule mode is deterministic and returns a *verdict* —
pass / flag / fail — with no number. That is exactly right for a gate and useless
for a weighted factor: the finding arrives scoreless, so the factor is marked
`unscored` and its weight drops out of the denominator on **every** case. The
grade still renders, confidently, over a rubric that is not the one the customer
signed off — and nothing in the run looks wrong, which is why this is caught at
publish and not left to a runtime error.

If the check really is a deterministic pass/fail limit, it is a **gate**, not a
factor — move it to `factor_set.gates`. Under `checklist` the rule is not
applied: a checklist row carries a band rather than a number, and a rule verdict
can reasonably stand in for one.

## The UI

There is **no scorecard panel**. The grid renders inside the officer's decision
card automatically whenever the app declares a `factor_set` — gates, then the
composite, then the rows, above the recommendation. A separate screen would
force the officer to reconcile two artefacts that might disagree.

To let a queue be ranked by grade, add a `grade` column with
`"column_formats": {"grade": "grade"}` on a `workflow_staging` queue. The rows
carry `grade`, `score_percent` and `gated` as flat columns. **This only works
for precomputed rows** — a queue cannot be ranked by a grade that only exists
once someone opens the case, so a scorecard app that wants portfolio ranking
needs a trigger, not on-demand runs.

## Officer corrections — the learning loop

Every row is correctable. An officer changes a score, gives a reason, and
`POST /apps/{slug}/factors/{factor_id}/override` re-scores the case **in code**
and folds the reason into the `(app, "api", factor_id)` bucket — the same path a
per-check reject already uses. You do not wire this: it exists for any app with
a `factor_set`.

What it guarantees, and why you should not try to reproduce it elsewhere:

* the model's own score is preserved in `original_score` on the FIRST override
  and never touched again, so a second edit still shows what the AI said;
* `grade_before_override` is stamped once, so an override can never quietly
  move a grade;
* the officer is held to the same ceiling as the model — a score above the
  declared `weight` is refused, because that is a rubric change made one case at
  a time;
* a **reason is mandatory**, and a gated or already-decided case refuses the
  edit outright.

> **Authority.** An override can move the grade. Where approval limits key off
> the grade, that means an officer could widen their own signing limit. The
> platform records who/when/why plus both grades and does **not** arbitrate it —
> an authority matrix is the customer's policy. If the BA raises it, that is a
> real discovery question, not a bug.

## SOP fingerprinting — catching a policy that moved

> **Only works with `sop.doc_path`.** Publish rule **FS-04** rejects a
> `fingerprint` without one. In query mode the runtime hashes a top-12 semantic
> retrieval, which moves when the index is rebuilt or an unrelated document
> outranks a chunk — so the fingerprint would either cry wolf forever or never
> be compared at all. Whole-document mode is deterministic, and is the only mode
> in which "the policy changed" is a statement about the policy.

Set `sop.doc_path` to the SOP document, and stamp `sop.fingerprint` with the
hash of that document's text as fetched. At run time `check_evaluate` hashes the
document it actually judged against and the two are compared.

**There is no automatic stamper yet.** Deriving the factor tree from the SOP is
something you do in conversation with the BA — read the document, propose the
tree, get it confirmed — and the fingerprint is stamped the same way. Until a
build-time helper exists, omitting `fingerprint` is the honest choice: it means
"drift is not detected", which is true, rather than a value that pretends
otherwise.

* A mismatch **flags, it does not block.** Scoring continues, the officer sees a
  warning on the card, and the app is marked `needs_reextraction`. We cannot
  tell a material edit from a typo, and halting a portfolio on either would be
  worse than saying so.
* The hash is **whitespace-insensitive** — a re-flowed PDF extraction is not a
  policy change, and a check that cried wolf would be muted within a week.
* Comparison happens only when BOTH sides exist. No fingerprint ⇒ nothing is
  claimed: silence means "not checked", never "verified".
* `mode: "rule"` checks fetch no SOP, so they carry no fingerprint. Their
  threshold is `rule_expr` on the spec, and its history is the spec's history.

When the app is flagged, re-extract: read the current policy, confirm the tree
with a human again, and re-stamp the fingerprints.

## FS-05 — the rubric you FOUND must be the rubric you DECLARE

When you read a policy looking for a scoring framework, **record what you
found** on the spec:

```jsonc
"rubric_finding": {
  "source":   "sop_library_lending",
  "doc_path": "/policy/dealer-finance-credit-policy-v4.2.txt",
  "verdict":  "weighted_rubric",        // | criteria_checklist | none
  "evidence": { "factors_named": 6, "weights_present": true,
                "grade_scale_present": true,
                "excerpt": "4.3 Scored factors and weights … total 100 marks" },
  "confirmed_by": "<the BA who agreed it>", "confirmed_at": "<when>"
}
```

Publish then compares that record against `factor_set`:

| verdict | `factor_set` | outcome |
|---|---|---|
| `weighted_rubric` | absent | **blocked** |
| `weighted_rubric` | `composite` | pass |
| `criteria_checklist` | absent | **blocked** |
| `criteria_checklist` | `checklist` | pass |
| either | the *other* mode | **blocked** — shape mismatch |
| `none` | absent or present | pass |
| no record | anything | pass — nothing was claimed |

`verdict: "none"` **requires a reason**, and a positive verdict **requires
evidence**. Both exist so the BA can contradict you: "I read your credit policy
and found no scoring rubric" is a sentence they will correct on the spot if it
is wrong.

**Why it is shaped this way.** FS-05 used to be a heuristic over your prose —
weight words plus aggregate words with no `factor_set`. It fired correctly, and
then a build described the same assessment in different words and sailed
through. Prose is a rendering of intent and you can re-render it; a recorded
fact you cannot. Do not treat the record as a formality to fill in: it is the
thing being checked.

**`none` is a legitimate answer.** Policies that are eligibility gates,
narrative guidance, or that keep the rating sheet somewhere else are common.
Record it, say why, tell the BA. What you must not do is find a rubric and
declare nothing.

## Common mistakes

* Adding a `factor_set` because the domain sounds like credit. Absent is right
  for most apps.
* Inventing weights when the SOP has none. Ask, or declare `checklist`.
* Putting `weight` on a `checklist` factor — publish fails, and correctly: it
  means someone intended a total that will never be computed.
* Writing `grade_scale` mins as raw totals. They are percentages of the
  attainable maximum.
* Asking the model for the total.
* A factor whose `reads` points at a dataset the app is not bound to.
* Domain words in `terminology` for the wrong domain — an aviation app whose
  panel says "Scorecard" and whose band says "Grade" reads as a credit tool.
* Omitting `sop.fingerprint` and assuming the app will notice a policy edit. No
  fingerprint means no check, and the card says nothing.
* Building your own score-edit surface. The override endpoint already preserves
  the model's score, both grades, and the reason — a bespoke one will not.
* Finding a rubric, recording it, and declaring no `factor_set`. FS-05 blocks
  it, and rightly: that is the artefact this whole feature exists to replace.
* Recording `verdict: "none"` to get past FS-05 when the policy plainly carries
  a rubric. That is not a wording choice, it is a false statement about a named
  document, with your name and a timestamp on it.
