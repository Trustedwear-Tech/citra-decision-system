<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# Three of four lessons did nothing

*A field note on testing whether captured human judgement actually does
anything — and finding that three quarters of it did not.*

We build an open-source decision system for calls where being confidently
wrong is expensive: approving a loan, settling a claim, releasing an aircraft
part for service, energising a substation, clearing a shipment. Anywhere an
experienced person overrides what the data alone would say.

**Lending is the test domain below, not the product.** It is where we could
get real judgement from real officers and measure the effect honestly. The
engine carries no industry logic of its own.

> **The demo is a hypothetical Indian bank**, so figures below are in rupees
> where they come from the seeded data. Nothing in the platform is tied to
> that: currency, date order and ID checksums come from the country pack.

## The situation

A loan application arrives. On paper it is clean: salary slips, bank
statements, a decent credit score, an employment letter. Every automated check
passes. It is approved and the money goes out.

Six months later it defaults. Someone finally phones the employer — and the
company has never heard of this person. The documents were fabricated.

**An experienced credit officer would have caught it.** Not from anything in
the file, which looked fine. They would have caught it because they know
something else: in Indian retail lending, sourcing agents are paid when a loan
is *disbursed*, not when it is *repaid*. So on any file that came through an
agent, the experienced officer picks up the phone and calls the employer
directly, however good the paperwork looks.

That knowledge is not in the loan file. It is not in the credit policy. It is
in one person's head, built over fifteen years, and when they retire it leaves
with them.

## Why training a model does not reach this

The obvious move is to train on historical decisions. It does not work here,
for a reason worth being precise about.

A model trained on your records learns the patterns *in those records*. That
is also its ceiling. In a hard case, the fact that decides it is usually not
in the records at all.

Look at what the lender's own system stores. There is a field for whether an income
document is **present**. There is none for whether it is **true**. No quantity
of training data fixes that, because the column does not exist — you cannot
learn a pattern in a variable nobody recorded.

The same applies to the written policy. The lender's credit policy says it applies to
all sourcing channels, then prescribes nothing channel-specific. A model that
reads every policy document perfectly still has nothing telling it to treat
agent-sourced files differently.

**That is the gap.** Not that the model is not clever enough — the deciding
information is not anywhere the model can reach. It is in a person.

## What we built instead

Rather than training, we capture the correction. When the system recommends
something and a human overrides it, the *reason* is recorded. When the same
correction arrives from several officers, it becomes a rule applied to the next
matching case.

We seeded four and tested each honestly: run the same files twice, once with
the judgement active, once with it off. Identical outputs mean the judgement
did nothing.

## Three of the four did nothing

Not a small effect. **No effect.** Switched off, the system reached exactly the
same conclusion.

All three restated something the written policy already said, and the system
can read the policy. We had carefully taught it things it already knew.

That failure is worth more than it looks, because it marks the boundary. If
your procedure already covers the case, none of this is needed.

## The fourth one worked

Taken from three different credit officers, given as one instruction:

> On files sourced through an agent, verify employment with the employer
> directly. The submitted document set is not enough.

Two properties made it work where the others failed. **It is in no policy
document** — so if behaviour changes, reading is not the explanation. And
**the deciding fact is not a column** — present is recorded, true is not.

Nineteen agent-sourced applications, each run twice with identical inputs:

| | |
|---|---|
| **14 vs 1** | verification raised on 14 files with the judgement on, 1 without |
| **19 of 19** | applied on every file it was meant for |
| **0 of 2** | never fired on control files from other channels |
| **p = 0.0005** | roughly 1-in-2,000 odds of that split being chance |

Against a realistic hundred-file cohort: about seventy die on a hard rule
anyway. Around thirty pass every automated gate — the policy asked for
documents, never for a phone call. With the judgement on, those thirty get a
verification call. Twenty-seven clear within a day. Three are fabricated and
stop before payout.

## What this says about domains other than lending

Nothing in the mechanism is about loans. The shape recurs wherever a rule
covers the common case and a person covers the rest: an inspector who knows
which supplier's paperwork to distrust, a controller who knows which route
files understate turnaround, an adjuster who knows which garage inflates.

In each, the deciding fact is not a column and the procedure does not mention
it — which is exactly the gap the three failed lessons mapped out, and exactly
where the fourth worked.

## What it changed

**The null result is the control.** Without those three failures, "our system
improved outcomes" is unfalsifiable — you cannot separate learned judgement
from the base model being competent. Three lessons doing nothing is what makes
the fourth believable.

It also set a threshold. A correction now needs to arrive from **three
distinct officers** before it hardens into a rule. One person having a bad
afternoon should not reshape how decisions are made — and a lesson that merely
echoes policy tends not to survive that bar anyway.

## The part we are least sure about

Knowing *when* a captured judgement applies. Our answer is deterministic
facets: every case carries closed-vocabulary tokens (`loss_type:theft`,
`amount_band:25000_100000`), and a judgement fires only where its scope is a
subset of the case's facets. No model in the routing path.

It held up in this run. Where we think it could still break is tracked openly:

- [Recall is unmeasured, and a subset rule fails closed](https://github.com/Trustedwear-Tech/citra-decision-system/issues/1)
- [Subset scoping cannot transfer across domains](https://github.com/Trustedwear-Tech/citra-decision-system/issues/2)
- [Banded facets create cliffs](https://github.com/Trustedwear-Tech/citra-decision-system/issues/3)
- [A facet token can go semantically stale](https://github.com/Trustedwear-Tech/citra-decision-system/issues/4)
- [Two in-scope clauses can contradict](https://github.com/Trustedwear-Tech/citra-decision-system/issues/5)

---

Method, the full run and the limits: [The experiment](The-experiment) ·
How the loop works: [The learning loop](The-learning-loop)
