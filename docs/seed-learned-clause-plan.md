<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Plan: produce a learned clause in the demo

Status: DONE, and the original recipe was wrong. Written 2026-08-17, run
and corrected 2026-08-24. What shipped is `demo-data/scripts/teach_clause.py`;
it produced C-001 on the acme-bank demo. The correction is in "What
consolidation requires" below -- read that before writing corrections of
your own, because the instinct the first version encoded is the natural one
and it silently produces nothing.

The acme-bank seed publishes four apps, a policy library and 211,615 rows of
system-of-record data — and **no learned judgement**. `smartapp_clauses` is
empty, so the one thing that distinguishes this product from a grounded copilot
cannot be shown in the demo it ships with.

---

## Why it is empty

Clauses are not seeded, and should not be. They are *formed*, from corrections
officers actually made:

```
decision -> officer disagrees -> correction -> consolidation -> clause
```

Seeding a clause directly would fake the provenance, which is the part worth
showing. The demo needs the corrections, and then the real consolidation pass.

## What consolidation requires

Two components, and the plan originally read only the first.

**Promotion** (`clause_store.py`) — how many officers make a clause `active`:

```python
promotion_min_officers: int = 3
"status": "active" if len(officers) >= promotion_min_officers else "candidate"
```

It counts DISTINCT officers, never per-officer weights. Three corrections from
one officer produce a `candidate`, and a screenshot of a candidate proves less
than nothing, because it looks like the feature half-worked.

**Clustering** (`consolidation.py`) — whether those corrections are even
recognised as the same lesson. This is the part that was missed. Corrections
combine only when BOTH gates pass, pairwise against the cluster's first member:

| gate | function | threshold | what it means |
|---|---|---|---|
| text | `text_similarity` | `CLUSTER_SIMILARITY` = **0.34** | Jaccard over content tokens |
| facets | `facet_compatible` | `CLUSTER_FACET_OVERLAP` = **0.5** | overlap coefficient, `len(a&b) / min(len(a), len(b))` |

### The trap

The original recipe said: pick cases that differ in product, ticket size and
FOIR band so the only shared facet is `sourcing_channel:dsa`, and the scope
falls out narrow without anyone choosing its narrowness.

That is exactly backwards. Sharing one facet out of five is an overlap of
**0.2**, well under 0.5 — such corrections are treated as different KINDS of
case and never cluster, so no clause forms at all. Measured on the first
attempt:

```
priya vs arjun    text=0.148 FAIL   facets=FAIL
priya vs fatima   text=0.107 FAIL   facets=FAIL
arjun vs fatima   text=0.071 FAIL   facets=FAIL
```

Consolidation reported `pending: 3, clusters: 3, created: 0` — three clusters
of one. Nothing in that output says "your facets were too different"; it just
quietly creates nothing.

**A clause's scope cannot be narrower than what the clustering gate will hold
together.** With a five-family signature, corrections must agree on at least
three families (0.6), so the tightest achievable scope is three facets.

The text gate bites too, and in the opposite direction from what feels right.
Officers writing the same lesson in genuinely different words score 0.07–0.15.
Real corrections repeat the domain's vocabulary — "DSA", "verify employment",
"employer", "before approval" — and that repetition is what makes them
clusterable. Writing five freshly-phrased 25-word paragraphs, which reads as
more realistic, produces five unrelated clusters. (Same effect recorded when a
25-word minimum was proposed for correction reasons and rejected: 0/15 clusters
survived it.)

## The recipe

What actually works, and what `teach_clause.py` does:

1. **Pick 3–5 cases that agree on three facet families and vary the rest.** For
   loan-triage that is `sourcing_channel:dsa` + `foir_band:lt_30` +
   `income_proof:present`, varying product and ticket size — 3/5 = 0.6 overlap.
2. **Run each through `loan-application-triage`**, passing the WHOLE record as
   `inputs`, not just the application id. Case facets are derived from the
   record; a run given only an id yields `family:__unknown` for every family.
   The correction is still stored, its facets match nothing, and no clause can
   ever be scoped from it. Nothing anywhere reports this.
3. **Correct each as a DIFFERENT officer** — `POST
   /apps/{slug}/run/{correlation_id}/approve` with
   `{"decision": "reject", "decision_reason": "..."}`. Three distinct
   identities. Reasons should share the domain's vocabulary; check them against
   `text_similarity` before spending model calls on runs.
4. **Run consolidation** — `POST /admin/consolidation/run`.
5. **Verify**: `GET /apps/{slug}/memory/clauses` shows it `active` with
   `support_count: 3`, and `provenance` lists the three correction ids.

Result on the demo:

> **C-001** · active · *"For DSA-sourced files, verify employment directly with
> the employer before approval."*
> scope: `foir_band:lt_30`, `income_proof:present`, `sourcing_channel:dsa`
> 3 officers · 3 corrections cited

Note the scope carries `foir_band` and `income_proof` as well as the channel.
That is not a compromise to make the demo work — it is the honest scope of what
those three officers actually agreed on, and the clustering gate is what stops
you claiming anything broader.

## Then check it actually changed behaviour

Forming the clause is not the demo. The demo is that it **changes the next
decision**:

- Re-run a *held-out* DSA case that was not corrected. With the clause active
  it should divert to `verify_employment`.
- Run a **non-DSA** control. It should be untouched — that is the half that
  proves the scope is real rather than the system becoming generally more
  cautious.

That pair is also the honest version of the experiment in the credit note.
The figures are deliberately NOT repeated here: they were restated in this
plan and in the README and the wiki, and a number kept in four places drifts
in three of them. They live in one place now -- the dedicated experiment record at
[docs/Descision-System-Memory](https://github.com/Trustedwear-Tech/citra-decision-system/tree/main/docs/Descision-System-Memory), alongside the null result and what
the run does not show. Restatements elsewhere were deleted rather than
re-synced: a second copy is a second thing to keep true.

### Measured, 2026-08-24

`teach_clause.py --effect-only`, against C-001:

| case | channel | clauses cited |
|---|---|---|
| `LAN-2026-005351` — held out, never corrected | dsa | **1** |
| `LAN-2026-000276` — control | branch | **0** |

The two differ in the CHANNEL AND NOTHING ELSE: both auto, both under ₹500k,
both FOIR under 30, both with income proof on file. So the clause firing on one
and not the other is attributable to the channel, which is what the clause
claims to be about.

Getting that right took a second attempt. The first control (`LAN-2026-000205`)
sat at FOIR 31.36 and so differed in two families at once — it also returned 0
cited clauses, but that number could not distinguish "the scope is real" from
"the FOIR band excluded it". A control that differs in more than one place
answers a question you did not ask.

One thing this measures and one it does not: it shows the clause is RETRIEVED
for the held-out case and not for the control, which is the scoping mechanism
working. Whether the final recommendation flips to `verify_employment` is a
further claim about the model's use of what it was given — worth checking
separately, and not something the citation count establishes on its own.

## Whether to ship it in the seed

Two options. The second was chosen, and `demo-data/scripts/teach_clause.py`
is it — the seed does NOT run it:

- **Seed the corrections** in `demo-data/tenants/acme-bank/` and have
  `seed-demo.sh` run consolidation as step 7. Every install then shows learned
  judgement out of the box.
- **Ship a script** — `demo-data/scripts/teach_clause.py` — that a user runs
  deliberately, so they *watch* a clause form rather than finding one
  pre-baked.

The second is more persuasive and more honest: the product's claim is that it
learns from your officers, and a clause that is simply present when you install
it demonstrates the opposite. It also makes a better screenshot, because the
before/after is the story.

## Timing

Consolidation runs **both** ways: a background pass every
`CONSOLIDATION_INTERVAL_SECONDS` (default **900** — fifteen minutes), and on
demand via `POST /admin/consolidation/run`.

For a scripted seed, call the endpoint — waiting a quarter of an hour for a
scheduled pass makes the step look flaky when it is merely slow. For a live
demo the interval is the better story: correct three files, carry on talking,
and the clause forms on its own.

Worth knowing either way: if you correct files and check for a clause
immediately without calling the endpoint, you will find nothing and conclude it
does not work.
