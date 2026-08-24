<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Plan: produce a learned clause in the demo

Status: plan. Nothing run yet. Written 2026-08-17.

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

From `clause_store.py`:

```python
promotion_min_officers: int = 3
"status": "active" if len(officers) >= promotion_min_officers else "candidate"
```

and the comment beside it — *"it counts distinct officers, never per-officer
weights (no trust tiers)"*.

So:

| requirement | value |
|---|---|
| distinct officers correcting the same way | **3** — fewer leaves the clause `candidate`, not `active` |
| corrections must share a case facet | the facet becomes the clause's scope |
| the facet must exist on the app's case_signature | loan-triage has `sourcing_channel`, `product`, `amount_band`, `foir_band`, `income_proof` |

The scope is the **intersection** of the case labels on every correction that
formed it. Correct three files that differ in product and ticket size but share
a channel, and the channel is the only thing left — which is why a well-formed
clause is narrow without anyone choosing its narrowness.

## The recipe

The credit note's C-002 is the worked example and the demo should reproduce it,
not invent something new. The data supports it: **2,863 DSA-sourced
applications** in the acme-bank system of record.

1. **Pick 3–5 DSA-sourced cases** that clear every policy gate — the right-hand
   branch. Deliberately vary product, ticket size and FOIR band so the only
   shared facet is `sourcing_channel:dsa`.
2. **Run each through `loan-application-triage`.** Without memory these come
   back `approve`.
3. **Correct each as a DIFFERENT officer** via
   `POST /apps/{slug}/items/{item_id}/feedback` — three distinct officer
   identities, same change: `approve` → `verify_employment`, with the reason
   stated in the officer's own words rather than copied between them.
4. **Run consolidation** (`POST /admin/consolidation/run`).
5. **Verify**: `GET /apps/{slug}/memory/clauses` shows the clause `active` with
   three officers; `…/{clause_id}/provenance` shows the three corrections and
   the cases behind them.

Step 3 is the one to get right. Three corrections from one officer produce a
`candidate`, not an `active` clause — and a screenshot of a candidate proves
less than nothing, because it looks like the feature half-worked.

## Then check it actually changed behaviour

Forming the clause is not the demo. The demo is that it **changes the next
decision**:

- Re-run a *held-out* DSA case that was not corrected. With the clause active
  it should divert to `verify_employment`.
- Run a **non-DSA** control. It should be untouched — that is the half that
  proves the scope is real rather than the system becoming generally more
  cautious.

That pair is also the honest version of the experiment in the credit note:
14 vs 1 with memory on versus off, 19/19 correctly targeted, 0/2 on controls.

## Whether to ship it in the seed

Two options, and the second is better:

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
