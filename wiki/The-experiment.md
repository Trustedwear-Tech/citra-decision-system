<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## What we measured

**Can a system learn something from a person that exists in no document -- and
then apply it to exactly the right cases?**

That is what this experiment answers, and you can reproduce it.

Full write-up, with the worked policy example and the arithmetic:
**[Citra Decision Memory -- Credit Note 01](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/docs/Citra-Decision-Memory-Credit-Note.pdf)**
(PDF, 10pp).

### Why it is hard to test

Teach a system a rule already written in the policy, and correct behaviour
proves nothing -- it may simply have read the rule. **The test has to use
knowledge that appears in no document the system can see.**

### What we taught it

In Indian retail lending, sourcing agents are paid when a loan is disbursed,
not when it is repaid. Experienced officers know what that incentive
produces, so on agent-sourced files they ring the employer directly and
confirm the borrower's job is real.

We took that judgement from **three different credit officers** and gave it
to the system as one instruction:

> On files sourced through an agent, verify employment with the employer
> directly. The submitted document set is not enough.

It is in no credit policy. The policy says it applies to every sourcing
channel, then prescribes nothing channel-specific -- so **nothing the system
can read tells it to treat these files differently.** If behaviour changes,
reading is not the explanation.

The case record has a field for whether an income document is *present*.
None for whether it is *true*.

### The run

**Nineteen agent-sourced applications, each run twice.** Identical inputs.
One difference: the judgement on, then off. Then the opposite test --
**control files from other channels**, where the right behaviour is to do
nothing.

| Result | |
|---|---|
| **14 vs 1** | check raised on 14 files with memory on, 1 with it off |
| **19 of 19** | applied on every file it was meant for |
| **0 of 2** | never fired on a control file |
| **p = 0.0005** | odds of this being luck: about 1 in 2,000 |

It fires where it belongs, stays silent where it does not, and the effect is
not the underlying model.

### Where the money is

Take 100 agent-sourced files through the policy gates.

```mermaid
flowchart TD
    A["100 agent-sourced files"] --> B{"Policy gates<br/>credit score · income · debt-to-income"}
    B -->|"~70 declined"| C["Dead on a hard rule<br/>Memory changes nothing<br/>the branch our run sampled"]
    B -->|"~30 clear every gate"| D["Approved<br/>policy asked for documents,<br/>never for a phone call"]
    D --> E["Without memory<br/>30 disburse<br/>employer never contacted<br/>loss lands 6-18 months later"]
    D --> F["With the judgement on<br/>divert to a verification call"]
    F --> G["27 come back clean<br/>cleared within a day"]
    F --> H["3 fabricated<br/>stopped before payout"]
    style C stroke-dasharray: 4 4
    style E stroke-width:2px
    style H stroke-width:2px
```

**About 70 fail a hard rule and are declined** -- credit score 617, debt-to-income 95%,
income below floor. Memory changes nothing here. A file already dead on a
hard rule cannot be saved by better reasoning. **This is the branch our run
sampled**, which is exactly why no verdict moved in it.

**About 30 clear every gate and get approved.** The policy asked for
documents. It never asked anyone to pick up the phone. So they disburse, the
employer is never contacted, and the losses surface six to eighteen months
later as principal, provisioning and collection cost.

**With the judgement switched on, those 30 get diverted to a verification
call.** 27 come back clean and clear within a day. **3 are fabricated and
stop before payout.**

On a $50,000 ticket that is **$150,000 that never leaves the bank -- for the
cost of 30 phone calls.**

*Catch counts are arithmetic on a 100-file cohort. The 14-of-19 marker is
measured.*

### The null result

We seeded **four** judgements. Three restated the written policy. **Retired,
they changed nothing** -- the system reached the same conclusion without
them, because it could read the rule.

That is published deliberately. It is what makes the fourth believable, and
it draws the line exactly: **this is not a rulebook engine. It is the layer
for what the rulebook never covered.**

### Run it on your own data

1. Point it at decided cases, with documents and outcomes.
2. Write **one** judgement your officers apply and your policy does not
   contain.
3. Run them twice -- memory on, memory off.
4. Add control cases it should not touch.
5. Read the reasoning, not only the verdicts.

Clauses are seeded, inspected and retired through the endpoints in
[The memory](Connect-your-data);
the stack itself comes up with [Quickstart](Install-and-first-run) below.
**If it does not reproduce, [open an issue](https://github.com/Trustedwear-Tech/citra-decision-system/issues)**
-- a result that only works in our hands is not a result.

### Limits

One judgement, one application, nineteen cases, one tenant. It is evidence
that a learned judgement changes how the system reasons where policy is
silent, and stays quiet where it is not. It is **not** evidence that decision
memory helps in general, and we would not present it as such. The run tested
a single-facet scope (the sourcing channel); it says nothing about how well
multi-facet scopes discriminate, which we have not measured. Every rupee
figure above is arithmetic on a single ticket size and a flat
loss-given-default -- a real book has a distribution for both.

---

---

The argument this measures — why a fine-tune does not close it, and why the judgement is worth capturing — is on [Why this exists](Why-this-exists).

A narrative version of the same run, written for someone meeting this cold: [Three of four lessons did nothing](Three-of-four-lessons-did-nothing).
