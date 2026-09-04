<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# Why this exists


<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/1-why-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/1-why-light.svg">
    <img alt="Cases arrive; your SOP and data answer most of them; the ones carrying real money are resolved by a person whose judgement is written down nowhere" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/1-why-light.svg" width="100%">
  </picture>
</p>

**AI still cannot be trusted with the decisions that carry money -- and the
model is not the problem.**

Every sector has them. Whether to energise a substation. Whether an aircraft
part is fit to fly. Whether to sanction a loan, pay a claim, clear a shipment,
escalate a transaction, or commit an asset to an operation. High-stakes calls,
where being confidently wrong costs money, uptime, or lives.

Point a model at one of these and it stalls. Not because the model is weak,
but because what settles a hard case usually is not in the systems at all. The
SOP is written for the average case. The tables hold fields, not reasons. So
the recommendation arrives confident and hollow, the officer overrides it, and
within a few weeks nobody opens it any more. That is where almost every
enterprise AI pilot for high-stakes work is stuck right now.

You may already have tried fine-tuning a model for exactly these.

A fine-tune learns the patterns in the records it was shown. That is also its
ceiling. It cannot weigh what is not in the data, and in a hard case the
deciding factor usually is not: **the column that would settle it does not
exist, and the SOP does not cover this one.** That is the moment a person
takes over and runs the show, on judgement built over years and applied
exactly where the rules run out.

That judgement is the most valuable asset in the operation and the least
protected. It lives in one head. It walks out of the door.

So a system worth trusting here has to do two things at once. It has to be
**grounded** -- reasoning over your real records, citing them, never
improvising. And it has to be **governed** -- able to do precisely what it
was authorised to do, and nothing else. That is why the ontology exists: a
declared layer that binds the AI to real data and bounds what it is allowed
to do with it.

On that footing, Citra learns the judgement itself -- how your people
actually think, including the part no policy document contains -- and
recommends against it on the next case. Every accept, every override, every
recorded reason makes the next recommendation sharper, until the system reads
as a natural extension of your best officer rather than a tool being
operated. People leave and models change; the judgement stays, because it
lives in a ledger you own rather than in one head or one model's weights.
What it gives back is measured in two currencies: **money saved, and time
saved.**

**This is a decision system, not a vertical application.** It carries no
industry logic of its own. What a case is, what evidence counts, what may be
written back and by whom -- all of it is declared per deployment in the
ontology, which is why the same engine serves energy and utilities, banking
and insurance, financial crime, public services, field operations, logistics
and defence. Where a human currently applies judgement to a high-stakes call,
this applies.

The demo that ships with this repo is a bank, because a runnable demo has to
pick one. Nothing in the platform is built around it.

Built for organisations that cannot send operational data to a third-party
SaaS. Start on a hosted model endpoint to evaluate in minutes; move to your
own GPU server for production.

**If this is a system you think should exist -- one that judges like a human
and stays grounded -- come build it with us.**

## The problem it addresses

Every organisation that makes consequential decisions at volume has the same
queue: cases arrive, rules are applied, and a handful of them need a person who
knows better than the rules. That person is the whole system, and nobody has a
copy of them.


<p align="center">
  <img alt="A claim recommendation citing Health SOP sections 2 and 4 and Motor SOP section 2.1, with the write staged for approval"
       src="assets/screens/04-recommendation.png" width="100%">
</p>

<p align="center"><i>A real run on the bundled demo. The recommendation cites the sections it relied on, the fraud check is stated rather than implied, and the write is <b>staged</b> — "review the plan below and Apply to commit" — not executed.</i></p>

The same structure, five industries:

- A **credit officer** declines a file that passes every policy gate, because
  it came through a channel whose incentive is disbursal rather than repayment.
- A **claims assessor** recognises a repair estimate as inflated before any
  threshold fires, from the pattern of the workshop and the photographs.
- A **grid engineer** refuses to re-energise a feeder that reads healthy,
  because of what the last three faults on that line had in common.
- A **compliance analyst** escalates a set of payments that are each
  individually legal and collectively laundering.
- A **logistics or defence planner** overrides a routing that is optimal on
  paper, because the corridor's risk is not in any field the system holds.

Different sectors, one shape: **the rules cleared it, and a human knew better.**
Written policy captures the rules. It was never built to capture the judgement
applied once the rules run out -- and that judgement is what an institution
loses every time the person holding it leaves. It lives in one head, it is
never written down, and it walks out of the door.

### Why a better model does not close this

The gap is narrower and stranger than "the AI needs to be smarter".

Look at what any of these systems actually holds per case. A loan application
has an amount, a tenure, a sourcing channel, a declared income, a status. There
is no field for *is this employment real*. A feeder has a load profile, a fault
count, an age. There is no field for *has this line been patched badly before*.
The closest column records that a document or a reading exists -- nothing
distinguishes **the record is present** from **the record is true**.

**The signal is not in the columns. It is in a column nobody thought to
create.** That is why a model trained harder on the same fields cannot close
the gap: it can price the uncertainty as a slightly worse score, but it cannot
recommend *acquiring evidence that exists nowhere in its training data*. A
person can, and does, every day.

Citra's job is to capture that person's move -- once -- and replay it on every
matching case afterwards, in whatever sector the queue happens to be.

## Why the obvious answers don't close it

- **Copilots.** AI proposes, a person acts. The reasoning stays in that
  person's head -- nothing the organisation can replay, audit, or build on.
  After two years of copilot usage you own nothing.
- **Agents.** Systems that act without a governed envelope and without a
  record: unbounded risk going in, nothing to learn from coming out.
- **Fine-tunes.** A fine-tune buries judgement inside model weights. It holds
  patterns, not reasons; it cannot cite the past case that justified a call;
  and it does not survive a model swap with its reasoning intact.

The missing layer in all three is the same: **a decision memory you own,
independent of any model.**
