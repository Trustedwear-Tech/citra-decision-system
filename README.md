<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="assets/banner-light.png">
    <img alt="Citra Decision System — the decision system that learns human judgement" src="assets/banner-light.png" width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Trustedwear-Tech/citra-decision-system/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Trustedwear-Tech/citra-decision-system/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-2563EB"></a>
  <img alt="Self-hosted" src="https://img.shields.io/badge/deploy-docker%20compose-1E3A8A">
  <img alt="Open models" src="https://img.shields.io/badge/models-open%20weights-4B5563">
  <a href="https://discordapp.com/channels/1519703038724669551/1535992242433433700"><img alt="Discord" src="https://img.shields.io/badge/discord-join-5865F2"></a>
</p>

<p align="center">
  <b><a href="#quickstart">Quickstart</a> ·
  <a href="#the-core-concepts-in-plain-terms">Core concepts</a> ·
  <a href="#how-it-is-built">Architecture</a> ·
  <a href="#what-the-demo-gives-you">Demo</a> ·
  <a href="docs/">Docs</a> ·
  <a href="https://citra-ai.com">Website</a></b>
</p>

*Sovereign by design -- run it in your own infrastructure, on open models. Your
data never leaves.*

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/story/1-why-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/story/1-why-light.svg">
    <img alt="Cases arrive; your SOP and data answer most of them; the ones carrying real money are resolved by a person whose judgement is written down nowhere" src="assets/story/1-why-light.svg" width="100%">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/story/2-loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/story/2-loop-light.svg">
    <img alt="A case is recommended with citations, a person approves or corrects it, three officers agreeing turns that into a rule, and the next similar case uses it" src="assets/story/2-loop-light.svg" width="100%">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/story/3-governed-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/story/3-governed-light.svg">
    <img alt="The agent proposes an action, a policy gate bounds it, a person approves before anything is written, and every step is recorded" src="assets/story/3-governed-light.svg" width="100%">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/story/4-surfaces-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/story/4-surfaces-light.svg">
    <img alt="Runs as an app, embedded in your own UI, or as an API; on demand, prepared ahead of time, or automatically within limits you set" src="assets/story/4-surfaces-light.svg" width="100%">
  </picture>
</p>

Two things come out of that loop, and they are worth separating: the
**money** saved by getting the high-stakes calls right, and the **hours**
saved by not re-deciding the routine ones. Most tools chase the second.
This is built for the first, and gets the second on the way.

The rest of this page is the detail: [the problem](#the-problem-it-addresses),
[what we measured](#what-we-measured), [how it works](#how-it-works), and
[how to run it](#quickstart).


## Support this project

Citra Decision System is Apache-2.0 and free to run on your own
infrastructure, forever. Sponsorship funds maintenance, the documentation, and
the demo tenant people try before they self-host.

**[→ Support this project](https://citra-ai.com/open-source)**

<sub>Contributions go to Trustedwear Tech Private Limited, which maintains this
project. They are not tax-exempt donations, and they buy no licence, warranty,
support entitlement or influence over the roadmap — the project stays
Apache-2.0 either way.</sub>

<details>
<summary><b>Contents</b></summary>

- [The problem it addresses](#the-problem-it-addresses) — and why a better model does not close it
- [Why the obvious answers don't close it](#why-the-obvious-answers-dont-close-it) — copilots, agents, fine-tunes
- [What we measured](#what-we-measured) — the experiment, the money, and the null result
- [How it works](#how-it-works) · [What a "learned judgement" actually is](#what-a-learned-judgement-actually-is)
- [Three surfaces, one intelligence](#three-surfaces-one-intelligence) — app, API, embeddable UI
- **[The core concepts, in plain terms](#the-core-concepts-in-plain-terms)**
  - [The ontology](#1-the-ontology----the-rulebook-for-one-deployment) — the rulebook for one deployment
  - [Governed writes](#2-governed-writes----why-the-ai-cannot-go-off-script) — why the AI cannot go off-script
  - [The data catalogue](#3-the-data-catalogue----the-menu-the-builder-orders-from) · [Fraud screening](#4-fraud-screening----making-we-have-seen-this-before-mean-something) · [Country and vertical packs](#5-country-and-vertical-packs----same-code-local-rules)
- [How it is built](#how-it-is-built) — the five moving parts, in code
- [Quickstart](#quickstart) · [Configuration](#configuration) · [Requirements](#requirements)

</details>

Every sector has decisions that carry real weight. Whether to energise a
substation. Whether an aircraft part is fit to fly. Whether to sanction a
loan, pay a claim, clear a shipment, escalate a transaction, or commit an
asset to an operation. High-stakes calls, where being confidently wrong costs
money, uptime, or lives.

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

## What we measured

**Can a system learn something from a person that exists in no document -- and
then apply it to exactly the right cases?**

That is what this experiment answers, and you can reproduce it.

Full write-up, with the worked policy example and the arithmetic:
**[Citra Decision Memory -- Credit Note 01](docs/Citra-Decision-Memory-Credit-Note.pdf)**
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
    A["100 agent-sourced files"] --> B{"Policy gates<br/>bureau · income · FOIR"}
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

**About 70 fail a hard rule and are declined** -- bureau 617, FOIR 95%,
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

On a ₹5 lakh ticket that is **₹15 lakh that never leaves the bank -- for the
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
[The memory](#5-the-memory----what-makes-the-next-recommendation-better);
the stack itself comes up with [Quickstart](#quickstart) below.
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

## How it works

1. **Author in plain English.** Describe a workflow -- underwriting,
   recovery, claims review -- and Citra generates the Decision App and its
   API in days, not a multi-month integration build.
2. **Citra recommends.** Each case arrives assembled and scored, with a
   recommended action and the reason for it, grounded in your own SOPs, your
   own records, and your own past decisions.
3. **Your team decides.** Approve or override, and record why. The agent
   never writes to your systems on its own -- every write is schema-
   validated, and autonomy is opt-in, later, only where you choose.
4. **The judgement compounds.** Every recorded reason is written to the
   decision ledger you own. The next matching case is recommended against
   it, and a corrected pattern that repeats across officers hardens into a
   named, attributed, reversible learned judgement -- not a silent weight
   update.

## What a "learned judgement" actually is

This is the object the whole system exists to produce, so it is worth being
precise. It is not a summary, an embedding, or a fine-tune. It is a row in a
Mongo collection (`smartapp_clauses`), and it has five properties that follow
directly from the code in `smart-app-service/clause_store.py`:

- **Atomic.** One rule, one sentence, 40 words or fewer. A clause longer than
  that is two clauses.
- **Scoped.** Every clause carries a set of facets, and it fires for a case
  only when `scope_facets ⊆ case_facets`. Its scope is the intersection of
  the labels on every correction that formed it -- so a rule formed from
  three corrections about small agent-sourced files applies to those, and
  stays silent everywhere else. That scope has a floor: corrections only
  combine into one judgement when their facets overlap by at least half, so a
  clause is never narrower than what its own evidence agreed on.
- **Written once.** The text is written at birth, from roughly three
  clustered corrections, by a single LLM call -- and is never rewritten.
  Later matching corrections touch provenance and counters only. This is
  deliberate: a store that re-summarises on every correction is performing
  the Nth lossy re-encode of an already-lossy encode, and the earliest
  lessons degrade first.
- **Provenanced, and it is enforced.** A clause must cite the corrections
  that taught it; the store rejects one that doesn't, on the grounds that an
  unprovenanced clause is LLM-authored policy rather than your officers'.
  It carries the officers whose corrections formed it, so a reviewer can go
  read the actual cases.
- **Corroborated before it counts.** A clause is born a *candidate* and is
  promoted only once **distinct** officers support it (three by default). A
  candidate still fires, but is injected with honest attribution -- one
  officer's judgement, not yet corroborated. Counting distinct officers,
  never per-officer weights, is what stops one prolific officer quietly
  writing the app's policy.
- **Reversible.** Retiring one is a status change, not a migration. A policy
  clause, by contrast, ships unmeasured and takes another committee to
  revise.

<p align="center">
  <img alt="App Memory showing one team judgement learned from three officer corrections, scoped to DSA-sourced, low-FOIR, documented-income files"
       src="assets/screens/panels/17-app-memory.png" width="100%">
</p>

<p align="center"><i>One of these, as the app holds it: the sentence, the scope it fires within, and the three officers whose corrections formed it. Every judgement links back to the corrections that taught it, so it can be read, challenged and retired — never silently rewritten.<br><br><b>This is not what a fresh install looks like.</b> Clauses are formed, not seeded, so the demo ships with none — an app that arrived already knowing things would be demonstrating the opposite of the claim. Run <code>demo-data/scripts/teach_clause.py</code> to watch this one form from three officer corrections.</i></p>


Three worked examples, in the shape the system stores them -- from three
different sectors, all the same object:

> *"Files sourced through an agent get employment verified with the employer
> directly -- the submitted document set is not enough."*
>
> scope: `sourcing_channel:dsa` (DSA = Direct Selling Agent, the channel's
> name in the source system) · formed from corrections by three named credit
> officers · appears in no policy document

> *"Feeders with two or more transient faults in a monsoon month get a
> physical line inspection before re-energising, whatever the load reading
> says."*
>
> scope: `asset_class:11kv_feeder, season:monsoon` · formed from corrections
> by three named grid engineers · appears in no maintenance SOP

> *"Estimates from a workshop first seen in the last ninety days get the
> parts list checked against the surveyor's photographs, regardless of
> amount."*
>
> scope: `vendor_age:new` · formed from corrections by three named assessors ·
> appears in no claims manual

None of these is a rule anyone wrote down. Each is what an experienced person
does, recovered from what they actually did. Promoting one into written policy
is the intended destination, not a failure of the system -- you can only write
the clause once you know it exists.

## What the ledger records

For every case: the case itself, the evidence assembled for it, the
recommendation and its citations, the human decision, any override, **the
why**, and the outcome once it is known. It lives in your database, in a
schema you can read, and it is what makes the next recommendation better --
and what an auditor can be shown.

## Three surfaces, one intelligence

- **Decision App** -- a ready-made workspace: case-working pages, live
  dashboards, a plain-English copilot. Live on day one, nothing to build.
- **Decision API** -- every recommendation, score, reason, and the learning
  loop itself, served over REST. Call it from any system you already run.
- **Embeddable recommendation UI** -- drop the recommendation and its
  reasoning straight into your existing LOS, CRM, or core screens. Your
  team never changes tools. See `bank-demo/` for a worked integration.

## What makes it different

- **A governed ontology, not an open-ended agent.** What the system may
  build and do is bounded up front; every write is schema-validated before
  it runs.
- **Cited, precedent-backed recommendations.** Every recommendation links
  back to the SOP passage or prior decision that produced it, so an
  approver checks the reasoning, not just the answer.
- **File-defined data sources.** Each deployment's MCP loads its source
  registry from a local `sources.json` -- no central service holds your
  connection strings. See `ARCHITECTURE.md` §3.
- **Connects to what you already run.** SQL databases, REST APIs, and your
  document stores through MCP -- no rip-and-replace to get started.

## The core concepts, in plain terms

Five ideas explain most of the system. None of them need code to understand,
and once they land the rest of this README reads easily. Each one points at
where to go deeper.

### 1. The ontology -- the rulebook for one deployment

The ontology is a single file, `sources.json`, that you write once per
deployment. It is the answer to: *what systems exist here, what do their
tables and columns actually mean, who is allowed to read them, and what --
if anything -- may be written back?*

Think of it as the difference between handing someone a database password and
handing them a job description. The password says "you can reach everything".
The job description says "these three tables, read-only, except one action you
may take, and here is what each column means".

A useful way to read it: the database knows a column is called `status` and
holds text. The ontology is where you say **what that means** -- that it is
the decision field, that `approved` and `paid` are good outcomes, that
`rejected` is a bad one. The system cannot infer intent from a schema, and it
does not try to guess. Anything it needs to know, you declare.

Two design choices are worth knowing up front, because they shape everything:

- **No secrets in the file.** A source declares an `env_prefix`, not a
  password. The container reads `{PREFIX}_HOST`, `{PREFIX}_USER` and so on
  from its own environment. The ontology is safe to commit and review.
- **A typo is a boot failure, not a shrug.** Source, dataset and column blocks
  reject unknown keys outright. Writing `artifact_roles` instead of
  `artifact_role` would otherwise silently switch fraud screening off for that
  column and nothing would ever say so. A registry that half-loads and starts
  anyway produces confidently wrong answers, which is worse than refusing to
  start.

Each department runs its own MCP container with its own file, so a container
only ever knows the systems its own file names. There is no central service
holding every tenant's connection strings.

Validate before booting:

```bash
make validate-sources FILE=demo-data/tenants/acme-bank/mcp/sources.json
```

Deep dive: `source-mcp-template/docs/sources-file.md` (the full field
reference), `source-mcp-template/registry_models.py` (the schema itself).

### 2. Governed writes -- why the AI cannot go off-script

This is the part people most want to understand, so here it is in full.

**The AI never writes SQL against your systems.** Not "we ask it not to" --
there is no field on the request where a statement could travel.

What you declare in the ontology is a **write action**: a named, human-authored
operation on one table. Its heart is a fixed parameterized statement you wrote
yourself. For example:

```json
{
  "id": "record_credit_decision",
  "verb": "update",
  "sql_template": "UPDATE loan_applications SET status=:status, decision_reason=:decision_reason, decided_by=:decided_by, decided_at=:decided_at WHERE application_id=:application_id",
  "key_fields": ["application_id"],
  "roles_allowed_write": ["dept_admin", "org_admin", "super_admin"],
  "input_schema": {
    "type": "object",
    "required": ["application_id", "status"],
    "properties": {
      "status":       { "type": "string", "description": "approved | rejected | under_review." },
      "decided_by":   { "type": "string", "x-citra-fill": "actor" }
    }
  }
}
```

Read what that guarantees. **The statement only `SET`s four columns, so those
are the only four columns any AI, any officer, or any API caller can ever
change through this action.** Not because something checks a per-column
"updatable" flag -- there is no such flag -- but because the statement is
fixed and the model never gets to write one. To make a fifth column writable,
a human edits the ontology and the change goes through review like any other
code.

What the model actually produces is not SQL but a small structured object:

```
{ dataset_id: "loan_origination.loan_applications",   <- chosen from a fixed list
  action_id:  "record_credit_decision",               <- chosen from a fixed list
  payload:    { application_id: "...", status: "rejected", decision_reason: "..." } }
```

Both ids come from an enumerated list bound to the app, so the model is
picking from a menu, not composing a command. That request goes to the MCP,
which independently re-checks, in order:

1. the caller's service credentials and user token are valid;
2. the action is actually registered on that dataset (unknown -> rejected);
3. the caller's **role** is allowed to write (default: department admin and
   above -- an empty allow-list does not mean "everyone");
4. the payload carries every required field, and the key fields are present;
5. then, and only then, the values are **bound as parameters** to your stored
   statement. Nothing is ever string-concatenated into SQL.

Two fields are stamped by the server and cannot be forged by the payload:
`x-citra-fill: actor` binds the verified identity from the token, and
`now` binds the server clock. So "who decided this, and when" is not
something the AI gets to assert.

**And a human still approves.** During a case the agent's proposed writes are
only *staged* -- collected as `planned_writes`, shown to the officer with the
exact values. Approval replays precisely those staged writes, with no second
model round-trip, so what was approved is what commits. If the plan changed
between display and approval, a hash check rejects it. Officers can edit
values before approving, but only fields the app marks editable, and an edited
payload is re-validated before it commits.

Two more guarantees worth stating:

- **Chat is structurally read-only.** The write tool is blocked at dispatch in
  the chat surface unconditionally -- not by prompt, by code.
- **Read before write.** An agent may not stage a write about a record it never
  actually read. This is enforced in the human-approval path; on the fully
  unattended path it currently ships in observe-and-log mode, so treat that
  one as reporting rather than blocking.

Being straight about the trust boundary: the platform does not parse or
sanity-check your `sql_template`. It executes the statement you registered. The
security property is that *only a human can author or change one*, and it is
reviewed like code -- not that the system validates the SQL you wrote. Also
note that on the SQL path an extra, undeclared field in the payload is simply
ignored rather than rejected; it cannot reach the database, because the
statement does not mention it.

Deep dive: `docs/write-actions.md`.

### 3. The data catalogue -- the menu the builder orders from

The builder cannot offer you a table it has never seen. The
`data-discovery-service` is what makes the ontology *browsable*.


<p align="center">
  <img alt="The claim triage queue: 465 open claims read from Postgres through the department MCP"
       src="assets/screens/03-claim-queue.png" width="100%">
</p>

<p align="center"><i>465 open claims, read live from the demo's Postgres through the department MCP. No copy, no sync, no vector store standing in for the system of record.</i></p>

It walks every registered MCP and, per table, asks three questions: what
columns do you have, what does the schema look like, and may I see a small
sample? From that it builds a catalogue row per table, stored in Mongo and
keyed by `(tenant, source, dataset)`.

Two things it does with the sample are worth knowing:

- **It flags personal data with rules, not a model.** Regexes and column-name
  hints spot emails, PAN, Aadhaar, SSN, routing numbers, card numbers and so
  on. It errs toward over-flagging: a wrongly-flagged column is still fully
  queryable, it just gets redacted in samples. Anything you declared yourself
  is never downgraded by the classifier.
- **It does not rename your columns.** The crawl calls no LLM. A cryptic
  column stays cryptic, because a helpfully renamed column silently breaks the
  query that has to run against the real name. Where you *do* want
  human-readable descriptions, there is a separate, explicitly human-reviewed
  path: a curator asks for drafted descriptions, reads them, and applies them.
  Nothing is written to the catalogue by a model on its own.

The catalogue also carries the ontology through verbatim -- the domain triple,
the fraud block, the decision-history and money definitions -- so the builder
sees meaning, not just column names.

One rule catches a whole class of silent failure: tables are addressed
verbatim as `<source_id>.<table>`, and publishing an app rejects a reference
that resolves to nothing. The alternative is an app that ships with panels
that are quietly empty forever.

Finally, a reconcile pass removes catalogue rows whose source or table no
longer exists, so a retired system stops being offered. It is deliberately
timid about this -- an empty registry is treated as an outage and prunes
nothing, and a source whose crawl errored is never pruned on that pass --
because wrongly deleting a live dataset is far worse than briefly keeping a
stale one.

### 4. Fraud screening -- making "we have seen this before" mean something

A duplicate file is not automatically suspicious. The same ID photo appearing
on two claims by the same person is *normal*. The same damage photo appearing
on two unrelated claims is a double-dip.

The system cannot tell those apart from the pixels. So the ontology tells it
what each document column **is**, via `artifact_role`:

| Role | What reuse means |
|---|---|
| `identity` | ID scan, headshot. Reuse by the same person is expected -- verification, never a flag. |
| `evidence` | Damage photo, inspection shot, invoice. Reuse across cases is the double-dip signal. |
| `supporting` | Brochure, generic terms PDF. Reuse is meaningless -- never fingerprinted at all. |
| `payment_proof` | Receipt, transfer slip. Behaves like evidence, and is the only document allowed to feed the payment-ledger cross-check. |

You can override the default for a column with `reuse_policy`
(`expected` / `suspicious` / `ignore`) -- but the resolution order is
deliberately one-way: it can relax screening explicitly, never weaken it by
accident. A column that says nothing falls back to the strict interpretation.

**Screening is opt-in, per table.** Set `fraud_screening.applies: true` to turn
it on, `false` to hard-disable it. Omit it entirely and it turns on only if at
least one column declares an `artifact_role` -- in other words, a source that
says nothing about fraud gets no fingerprinting at all. Turning it on also
needs a primary key and at least one screenable column; the validator tells
you at authoring time rather than leaving you with a screen that never fires.

Around that sit the checks you configure per table: which fields identify a
person or ring, the value field, an incident date, GPS coordinates and a
radius, date rules ("the incident cannot precede the policy by less than N
days"), and cross-document checks ("the invoice total must match the
surveyor's approved amount").

Worth being clear about what is *not* ontology-driven: the scoring thresholds
and signal weights are tuned by a human operator, not declared per source.

Deep dive: `docs/fraud-detection-primitives-plan.md`, and the worked starter
file `source-mcp-template/templates/insurance-claims-IN.sources.json`.

### 5. Country and vertical packs -- same code, local rules

First, the thing worth being precise about: **the engine has no industry logic
in it.** A source may declare a domain triple -- `vertical`, `sub_vertical`,
`country` -- but the block is entirely optional, and a source without one is
valid and fully functional. Nothing about a case, its evidence, or what may be
written back is hard-coded to a sector; all of it is declared. That is what
makes the same deployment work for a grid operator and a claims department.

What the triple buys you is convenience, not capability: a set of built-in
defaults so you do not have to write them yourself. Packs currently ship for
four verticals (banking, insurance, utility, field service) and two countries
(`IN`, `US`). Adding another is a new enum value and a defaults table -- an
addition, never a fork, and never a prerequisite for running here.

Concretely, `country` decides:

- **Currency and date order** -- `IN` -> INR and day-first; `US` -> USD and
  month-first. This matters more than it sounds: `03/04/2026` is 4 March in
  the US and 3 April in India, and getting it backwards silently corrupts
  every date-difference rule.
- **Which ID formats are checked, with real checksums** -- India gets PAN,
  IFSC, GSTIN and Aadhaar (including the Verhoeff check digit); the US gets
  SSN, EIN, ABA routing and ZIP. VIN and email are checked everywhere.

`sub_vertical` supplies sensible defaults where you were silent -- an
inspection workflow gets a 1 km GPS radius where a claims workflow gets a
wider one -- and warns at publish time if a workflow's characteristic check
is not armed.

One rule to remember: **the triple never turns screening on by itself.** It
selects packs; `fraud_screening` remains the only switch.

## How it is built

Five moving parts. Each one exists because the next one needs it, so it reads
as a single chain:

> You describe your systems once in **`sources.json`** (§1). The MCP that
> serves them **registers** itself on boot (§2), so `data-discovery-service`
> can crawl it into a **catalogue** (§3). The **builder** interviews you in
> chat and can only offer what that catalogue contains -- it drafts the app,
> tests itself against synthetic cases, and publishes (§4). You then use the
> app: it recommends, you approve or correct, and every correction is
> consolidated into **memory** (§5) that the next recommendation reads. The
> loop closes -- the ontology bounds what may be built, and your corrections
> decide what gets better.

`ARCHITECTURE.md` has the full service map; this is the decision path only.
The concepts above are the "what"; this is the "where in the code".

### 1. The ontology -- one `sources.json` per deployment

Concept above; this is where it lives. Each department gets its own MCP
container, built from `source-mcp-template`, with its `sources.json` mounted
read-only.

Beyond what the concept section covers, the file is also where two definitions
that the rest of the system reasons over are pinned:

- `decision_history` -- marks which dataset *is* the record of completed
  decisions, and which of its values count as good, bad or neutral outcomes.
  This is what feeds the learning loop.
- `value_semantics` -- the money definition every ROI figure is computed from:
  whether this workflow *recovers*, *prevents*, *sanctions* or *settles*
  value, which column carries the exposure, and how a realised amount is
  matched back. Because it lives in `sources.json`, a pilot's metric
  definitions are frozen by a git commit on day zero rather than argued about
  at the end.

On strictness: source, dataset and column blocks reject unknown keys, for the
reason given above. `connection` deliberately does not -- it is genuinely
polymorphic (SAP's `sysnr`/`client`, BigQuery's `project_id`) and a wrong key
there fails loudly at connect time anyway.

Model definitions: `source-mcp-template/registry_models.py` (the input
contract, and the authoritative one) and `source-mcp-template/models.py`
(what the MCP serves back).

### 2. Registration -- the MCP announces itself to `discovery-service`

On boot the MCP POSTs one entry per source to `discovery-service`
`/tools/register`, heartbeats every 60s, and deregisters on shutdown. Each
entry carries the tool id, the org and departments it serves, the endpoints to
reach it, and the visibility block copied straight from the ontology. The
registry is Mongo-persisted, so a restart does not lose it.

`GET /tools/available` is answered against the caller's JWT -- `org_id`,
`dept_ids`, roles -- so discovery is itself access-controlled: you cannot see a
tool you are not entitled to. Semantic (RAG) sources advertise an **empty**
query endpoint, because they are answered platform-side by the reader rather
than by the MCP; a naive consumer therefore cannot route a RAG read to an
address that would 404.

### 3. The catalogue -- `data-discovery-service` crawls what was registered

The builder cannot offer a dataset it has never seen. This service walks the
registered MCPs and, for each one: `GET /datasets`, then per dataset
`GET /datasets/{id}` and `/datasets/{id}/sample`; runs a rule-based PII and
semantic-type classifier over the sample; and computes a schema fingerprint
used to detect when a physical schema has actually changed. The result is
upserted into `data_catalogue`, keyed `(tenant_id, source_id, dataset_id)`. A
reconcile pass then deletes rows the registry no longer backs, so a retired
source stops being offered.

**The crawl calls no LLM, and never renames a column** -- names are carried
verbatim from the source, because a helpfully renamed column breaks the query
that must run against the real one. LLM-drafted *descriptions* exist, but only
behind an explicit two-step curator flow: `POST /catalogue/{id}/draft-descriptions`
returns proposals and writes nothing, and a human applies them with
`PUT /catalogue/{id}/descriptions`. Samples are redacted before any such call.

Datasets are addressed verbatim as `<source_id>.<table>`. That exact string is
what an app's `data_source.ref` must equal -- publishing rejects a ref that
resolves to nothing rather than shipping an app whose panels are silently
empty.

### 4. The builder -- one spec, three surfaces

`smart-app-service` turns a plain-English description into an app spec, using
the catalogue as its dataset palette and the ontology as the envelope of what
may be built.

**It is a conversation, not a form.** A builder pod runs an agent that
interviews you the way a business analyst would -- it asks at most three
clarifying questions up front, and proposes rather than interrogates. Its
competence is packaged as **20 skills** in `smart-app-service/skills/`, each a
`SKILL.md` the agent loads when that part of the job comes up: discovering
sources (`citra-mcp-discover`), drafting the agent's brain
(`citra-agent-spec`), designing panels and charts (`citra-ui-panels`,
`citra-ui-charts`), the safety rules it may not break (`citra-safety-rules`),
testing itself (`citra-self-test`), publishing (`citra-app-publish`).

The build runs in phases, and two of them are gates rather than steps:

| Phase | What happens |
|---|---|
| **0 — Understand** | The agent reads the runtime it is authoring for *before* composing. It writes specs for a renderer, executor and validators that already exist. |
| **1 — Internship** | Discovery over the catalogue from §3. **If the catalogue is empty the build stops here** and says so -- it will not invent a dataset to keep going. |
| **1.5 — Grounding** | Optional; only when the goal is repetitive decisions. |
| **2 — Expertise** | Authors the AgentSpec -- tools, grounding, policy gate, outcome -- then **self-tests it**. |
| **3 / 3.5 — UI** | Page list agreed first, then one page at a time, each confirmed before it is composed. Skipped entirely for a headless build. |
| **3.6 — Backstop** | Optional second opinion from a runtime-verifier sub-agent. |
| **4 — Deploy** | Publish. |

**The test step is real and it blocks.** `citra-self-test` runs synthetic cases
against the drafted agent and scores them *before* publishing, so prompt gaps,
missing tools and ambiguous instructions surface while they are still cheap to
fix. For any app with an action tool -- a write, a queue decision -- a green
run is **required to publish**. The single exception is a pure read-only
dashboard, which has no agent decisions to exercise. A static-check pass runs
again after the app spec is composed, because six of its checks read a spec
that does not yet exist at self-test time.

You are not locked in afterwards: `POST /apps/{slug}/edit` reopens the build,
and apps live in a **test environment first** -- `POST /apps/{slug}/promote-to-prod`
is what moves one to production, so you exercise it against real data before
anyone depends on it.

Publishing validates the whole spec before anything goes live:
refs must resolve, and a `case_signature` facet that reads a column no panel
projects is rejected, because that facet would resolve to `__unknown` on every
case and every rule scoped to it would be dead on arrival.

One published spec is served three ways:

- **Decision App** -- `citra-app-runtime` renders the case-working pages,
  dashboards and copilot inside the Citra UI shell.
- **Decision API** -- `GET /apps/{slug}/decision-contract` returns the app's
  own request/response schema, the endpoints to call, and the auth and
  governance rules, so any system can drive it headlessly:
  `POST /apps/{slug}/run` for a grounded recommendation, then
  `POST /apps/{slug}/run/{correlation_id}/approve` for the schema-validated
  commit.
- **Embeddable UI** -- `GET /apps/{slug}/embed/snippet` yields a script tag;
  the host page calls `Citra.init({ getToken })` then
  `citra.mount(selector, { embed, recordId, onDecision })`.
  The card renders in a shadow root, so the host's CSS and the card's cannot
  reach each other. `bank-demo/` is a complete worked integration.

### 5. The memory -- what makes the next recommendation better

Every correction an officer makes is recorded. A background job
(`consolidation.py`, leader-elected and off the officer's request path) does
three things with them, and only one of them writes text:

- **REINFORCE** -- the correction matches an existing clause: provenance and
  counters only, no LLM call, no text change.
- **CREATE** -- a cluster of roughly three related corrections matches nothing:
  one LLM call, once, ever.
- **MERGE** -- two clauses are near-duplicates: keep the more general.

It consolidates; it does not summarise. A clause's text is written once at
birth and never rewritten, so the Nth correction cannot degrade the (N-1)th
lesson. Clustering is lexical rather than embedding-based on purpose -- the
hard partition by reason code plus a facet-overlap requirement already
separates lessons, and a synchronous embedding call would add a network
failure mode inside the batch for a marginal gain.

At run time, `select_clauses` finds every clause whose `scope_facets` is a
**subset** of the case's facets (a Mongo `$setIsSubset` residual on a multikey
index; globally-scoped clauses always match), then sorts by specificity first
and score second. That sort *is* the backoff: a thin `(theft ∧ photo ∧ us ∧
>25k)` cell falls through to `(theft ∧ photo)`, then `(theft)`, with no
special-casing and no cold-start cliff. Dedupe keeps the most specific
survivor, and the block is filled to an injection budget. If the store fails,
it logs loudly and returns an empty block so the run still proceeds --
learning degrading must never take a decision down with it.

**Managing the memory** is a first-class surface, not a database chore. Status
governs how a clause is used:

| Status | Behaviour |
|---|---|
| `active` | corroborated team judgement -- asserted and cited |
| `candidate` | one officer's judgement, injected but **labelled** as uncorroborated (capped at 3 per case) |
| `dissented` | officers acted against it often enough that it is rendered as a disagreement notice, never as a rule |
| `sop_conflict` | contradicts the written SOP; surfaced for adjudication, not injected |
| `quarantined` | suspended by an admin -- e.g. taught by someone who has since left |
| `orphaned` | scoped to a facet family the app no longer emits, so it can never fire |
| `retired` / `superseded` | withdrawn |

A lone officer's experience is used immediately and labelled rather than
hidden, so a one-officer branch office still learns -- but promotion to
`active` counts **distinct** officers, which is what stops one prolific
reviewer quietly authoring the app's policy. Dissent is stored, never silently
resolved. Every clause can be inspected (`/apps/{slug}/memory/clauses`), traced
to the corrections and officers that formed it
(`/memory/clauses/{id}/provenance`), listed by officer, retired, quarantined,
resolved against the SOP, or exported wholesale -- the ledger is yours, in your
database, in a schema you can read.

---

## Quickstart

### Prerequisites

| Need | Why |
|------|-----|
| **Docker Engine 24+** with **Compose v2** | runs the whole stack. Compose v1 cannot parse the `include:` this uses |
| **16 GB+ RAM** (32 GB recommended) | Milvus plus the service fleet |
| **Python 3.9+**, with **`venv`** and **`pip`** | the setup and seed scripts. The seed builds a venv and installs into it |
| **curl** | the installer polls service health with it |
| **An OpenAI-compatible LLM key** | recommendations and NL->SQL -- OpenRouter, OpenAI, DeepSeek, or your own vLLM |
| **Internet access on first run** | pulling base images, and the seed's `pip install` |

**Debian and Ubuntu need three packages, not one.** `python3` there ships
without `ensurepip`, so `python3 -m venv` fails on a machine where Python is
plainly installed — *"the virtual environment was not created successfully
because ensurepip is not available"*. Install all of:

```bash
sudo apt install python3 python3-venv python3-pip curl
```

**What you do _not_ need on the host**, despite the stack using them:

| | |
|---|---|
| **Node.js** | only ever run inside the containers (`docker compose exec ... node`) |
| **git** | needed to clone, not to build — the release tarball is self-contained |
| **make** | convenience only; every target is a one-line script call, see below |
| **openssl** | used for secrets if present, falls back to `/dev/urandom` |

`docker version` should print a **Server** section. If it does not, Docker is
not running.

You do not have to check any of this by hand: `make wizard` and `make setup`
run a preflight first and name whatever is missing, before writing anything.

### Easiest: the wizard

```bash
git clone https://github.com/Trustedwear-Tech/citra-decision-system.git
cd citra-decision-system
make wizard
```

It checks your host first, asks for one OpenRouter key, writes `.env`, brings up
the full stack, and seeds the `acme-bank` demo. If a prerequisite is missing it
says which one and stops before writing anything, rather than failing halfway
through with `docker: command not found`.

A release tarball works identically -- it is self-contained, with the wizard and
every setup script inside:

```bash
curl -sSL https://github.com/Trustedwear-Tech/citra-decision-system/archive/refs/tags/v0.3.0.tar.gz | tar xz
cd citra-decision-system-0.3.0
make wizard
```

> **No `make`?** It is not installed by default on Windows, and the targets are
> thin wrappers -- run the script directly instead. Every `make X` below has a
> `bash scripts/quickstart/X.sh` equivalent:
>
> ```bash
> bash scripts/quickstart/wizard.sh
> ```

### Or by hand -- two phases

```bash
# 1. SETUP -- generate .env with fresh secrets, start the data stores, and
#    create the DB resources (Mongo replica set, demo Postgres, MinIO bucket).
make setup                      # or: bash scripts/quickstart/setup.sh

# 2. Set your key in the generated .env  ->  LLM_API_KEY=sk-...

# 3. START -- every service, the super-admin, and the acme-bank demo.
make start                      # or: bash scripts/quickstart/start.sh
```

`make install` runs both. `make ps`, `make logs` and `make down` manage the
running stack; `make down ARGS=-v` also wipes the volumes. Without `make`, the
equivalents are `docker compose -f docker-compose.quickstart.yml ps` / `logs`
/ `down`. See the `Makefile` for the full target list.

> **Two phases, many containers.** `setup` initialises the databases; `start`
> brings up the service fleet -- each its own image, so you can scale, restart
> and debug them individually -- and on first run also builds the sandbox
> images the builder needs, which takes a few minutes and is cached after.

### What gets built, and in what order

Everything is built from source on your machine. Nothing is pulled from a
private registry, and there is no image you cannot rebuild yourself.

**1. The fleet — fourteen services.** `docker compose` builds these from
`docker-compose.dev.yml`, which `docker-compose.quickstart.yml` includes. Six of
them (`citra-service`, `smart-app-service`, `duckdb-query-service`,
`reranker-service`, `discovery-service`, `data-discovery-service`,
`playwright-render-service`) build with the **repository root** as their context,
because they copy the shared packages out of `citra-common/`. The rest build from
their own directory.

**2. The sandbox images — three, and one of them depends on another.** These are
*not* built by compose, because compose never runs them: `action-sandbox-host`
spawns them per user, at runtime, when someone builds a Decision App or executes
code in chat. `start.sh` builds them on first run via
`scripts/quickstart/build-sandboxes.sh`; you can also run it directly:

```bash
bash scripts/quickstart/build-sandboxes.sh
```

They form a chain, which is why the builder is a separate step rather than
another matrix entry:

```
ghcr.io/openclaw/openclaw:<pinned digest>      the upstream agent runtime
        │
        └── citra-agent-sandbox-base            infrastructure/action-sandbox/Dockerfile
                │                               neutral base: toolkit, shims, Chart.js
                │
                └── citra-app-builder           smart-app-service/builder-sandbox/Dockerfile
                                                adds the builder persona + workspace seed

quick-chat-sandbox                              Citra-Service/Dockerfile.quick-chat-sandbox
                                                independent, FROM python:3.11-slim
```

`citra-app-builder` is built `FROM citra-agent-sandbox-base`, so the base must
exist first — the script builds them in that order and skips the consumers if
the base fails, rather than producing a confusing error two layers down.

**`citra-app-builder` is not `smart-app-service`.** They are easy to confuse
because one is built from the other's directory. `smart-app-service` is a
long-running service in the fleet, `FROM python:3.11-slim`; it is the thing you
talk to when you build or run a Decision App. `citra-app-builder` is an
**ephemeral, per-user container** it spawns to do the building, isolated on its
own no-egress network. Rebuilding one does not rebuild the other.

The script also creates the two egress networks the host attaches sandboxes to.
`citra-action-egress` is `--internal` (no route off the box) and
`citra-action-approved-egress` deliberately is not; making the second one
internal breaks every spawn.

If the sandbox build fails, `start.sh` warns and carries on. That is deliberate:
**running** Decision Apps is unaffected, only *building* them and code execution
in chat need these images. Re-run the script once it is fixed.


### Running it again, and after a reboot

Every service is declared `restart: unless-stopped`, so **the stack comes back
by itself** when Docker starts — after a reboot you do not need to run anything.
The one exception is `mongodb-init-rs`, which is `restart: no` on purpose: it
initialises the Mongo replica set once and is meant to exit.

For everything else:

| | |
|---|---|
| `make stop` | stop the containers, keep them — fastest to resume |
| `make up` | bring them back, no rebuild and **no re-seeding** |
| `make down` | stop and remove the containers; **data volumes survive** |
| `make down ARGS=-v` | also wipe the volumes — this destroys the demo data |
| `make start` | full phase 2 again: services, super-admin, and re-seed the demo |

`make up` is the one you want after a `make down`. `make start` also works but
re-runs the seed, which is slower and unnecessary if the data is still there.

If you changed source code, `make up` will not rebuild — use
`docker compose -f docker-compose.quickstart.yml up -d --build <service>`.

### What the demo gives you

A bank with five departments and fourteen officer personas, a Postgres
system-of-record holding ~211,000 rows across 16 tables, an MCP serving it,
a SOP library in Milvus, and four published Decision Apps: loan triage,
collections priority, claims triage, and sales performance.


<p align="center">
  <img alt="The Decision Apps list after installing the demo: claim triage, collections priority, loan triage and a sales dashboard"
       src="assets/screens/02-decision-apps.png" width="100%">
</p>

<p align="center"><i>What is on your home screen after <code>make wizard</code>.</i></p>


<p align="center">
  <img alt="The SOP Library listing the acme-bank Policy Library, readable org-wide"
       src="assets/screens/panels/12-sop-library.png" width="100%">
</p>

<p align="center"><i>The rules layer. Recommendations cite these documents by section, and your SOPs always win over anything the app has learned from officers.</i></p>

More screens — memory, learning batch, money impact, screening health, the
kill switches — are in [`assets/screens/panels/`](assets/screens/panels/),
captured from a running install by
[`scripts/quickstart/capture_panels.py`](scripts/quickstart/capture_panels.py).

### Signing in

| What | Value |
|------|-------|
| Web UI | http://localhost:8081 |
| Super-admin | `admin@citra-ai.com` / `ADMIN_PASSWORD` from `.env` (printed by `make start`) |
| Home org | **Citra AI** (`citra-ai`) -- can impersonate into the demo org |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |

Sign in as the super-admin, then **impersonate** an `acme-bank` persona (user
menu -> *Login as User*) to see what an officer sees. Open a Decision App and
the loop is all on one screen: a recommendation with its citations, approve or
override with a reason, the governed write back to Postgres, and the outcome
folded into memory for the next case.

`ALLOW_DEV_LOGIN=true` in `.env` also enables a passwordless local sign-in for
any seeded persona. It is local-only and fail-closed in production.

### Driving it headlessly

The same engine with no UI at all. Start from the app's own contract -- it is
self-describing, so you do not have to guess the request shape:

```bash
# Schema, endpoints, evidence requirements and governance rules for THIS app
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/loan-application-triage/decision-contract

# 1. Recommend -> reasoning, citations, cited_precedents, planned_writes, plan_hash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/loan-application-triage/run -d '{...}'

# 2. Approve -> the schema-validated commit, keyed by the run's correlation_id
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/loan-application-triage/run/$CORRELATION_ID/approve \
  -d '{"decision":"approve","expected_plan_hash":"...","decision_reason":"..."}'
```

Two governance details worth knowing before you build against it:

- **`expected_plan_hash` is a display-equals-commit guard.** `/run` returns a
  hash of the writes as you displayed them; echo it back on approve and a plan
  that changed in between is rejected with a 409 rather than quietly
  committed.
- **Per-item review is server-enforced.** When an app's `item_review_gate` is
  `hard`, every non-case `item_finding` must be dispositioned via
  `POST /apps/{slug}/items/{item_id}/feedback` before approve will succeed --
  and a rejection requires a reason, because that reason is what trains the
  memory.

`POST /apps/{slug}/tool/{tool_name}` records a decision made **without** the AI,
so a human-direct call still lands on the same ledger.

### Pointing it at your own data

You are writing one ontology file. You do not have to write it by hand.

**Step 1 -- generate the skeleton from a live database.** Point the
introspection script at a system you already run and it writes the datasets,
columns, types, primary keys and foreign-key relationships for you, plus
enum/range hints for low-cardinality and numeric columns:

```bash
python scripts/quickstart/introspect_source.py --help
```

It speaks PostgreSQL, MySQL, SQL Server, MongoDB, OData/SAP, Salesforce and
REST. With `--describe` it will also draft column descriptions, semantic types
and PII flags with an LLM -- those are exactly the fields the query planner
reasons over, so they are worth having, and worth reading before you keep them.

**Step 2 -- add the meaning that no database can tell you.** This is the part
that is genuinely yours to decide, and it is short:

| What you are deciding | Where it goes |
|---|---|
| Who this deployment is, and where | `domain: { vertical, sub_vertical, country }` on each source. Drives currency, date order and which ID checksums run. |
| Who may read this system | `visibility.roles_allowed`, plus `cross_org_ids` / `public_within_org` if you need them. |
| How to reach it, without secrets in the file | `connection.env_prefix` -- the container reads `{PREFIX}_HOST`, `{PREFIX}_USER`, … from its own environment. |
| Which table holds completed decisions | `decision_history` on that dataset, naming the outcome column and which values are good / bad. This is what the learning loop feeds on. |
| What "value" means for this workflow | `value_semantics` -- recovered, prevented, sanctioned or settled, and which column carries the exposure. Pin it on day zero, not at the end of the pilot. |
| **Whether this table needs fraud screening** | `fraud_screening.applies: true` on that dataset -- plus the identity fields, value field, incident date, and GPS radius if location matters. Omit the block entirely and screening stays off unless a column declares an `artifact_role`. |
| **What each document column is** | `artifact_role` per column: `evidence`, `identity`, `supporting`, `payment_proof`. Without this, "we have seen this file before" cannot be interpreted. |
| **What may be written back, if anything** | `write_actions` on the dataset, each with a fixed parameterized `sql_template`. Only the columns that statement sets can ever change. Omit it and the table is read-only. |

Fraud screening is per table, and off by default. A table that says nothing
about fraud gets no fingerprinting at all -- so turn it on only where the
question "has this artifact appeared before?" is actually meaningful.

**Step 3 -- validate before you boot.** The registry rejects unknown keys, so
a typo is caught here rather than silently disabling a feature at runtime:

```bash
make validate-sources FILE=path/to/your/sources.json
```

**Step 4 -- restart that MCP and re-crawl** so the catalogue picks up the new
tables and the builder can offer them.

Starter files worth copying rather than starting blank:
`source-mcp-template/templates/` has one per vertical and country --
`insurance-claims-IN`, `banking-loan_recovery-IN`, `utility-power_recovery-IN`,
`insurance-claims-US`, `field_service-equipment_inspection-US`. The insurance
ones are the best worked example of a fully-armed fraud block.

`docs/change-the-demo.md` walks the whole path end to end.

### AI models

The wizard asks for **one OpenRouter key** and wires it to everything:

| Role | Default | Why |
|---|---|---|
| Reasoning / NL->SQL | `deepseek/deepseek-v4-pro:nitro` | open weights |
| Embeddings | `baai/bge-m3` at 768 | open weights; the client requests `dimensions` so it returns 768 rather than its native 1024, matching the Milvus collection |
| Vision | `qwen/qwen3-vl-32b-instruct` | open weights |
| Image generation | *off* -- Runware if you want it | not served by OpenRouter |

One key, one bill, one thing that can be wrong. Both defaults are open-weights,
so nothing here depends on a proprietary model.

Citra talks to any OpenAI-compatible API, so **swapping is an `.env` edit, not a
migration**: point `LLM_BASE_URL` / `EMBEDDING_BASE_URL` / `VISION_BASE_URL` at
your own vLLM or TGI endpoint and no prompt leaves your network. Self-hosting
`bge-m3` is the same edit -- keep 768, or set `EMBEDDING_DIMENSION=0` for its
native 1024 and match `MILVUS_VECTOR_DIM`.

> **Changing the embedding model means re-ingesting**, even at the same
> dimension. Vectors written by one model do not share an embedding space with
> another model's queries, so old rows quietly stop matching rather than
> failing. Re-run the seed (or your own ingestion) after any change.

The model is a commodity input you can swap. Your decision memory stays put.

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| A service is unhealthy or restarting | `make logs` -- usually a missing key in `.env` |
| Recommendations error or hang | `LLM_API_KEY` unset, or the model / base URL is wrong |
| Mongo never becomes healthy | the replica set is initiated by the one-shot `mongodb-init-rs` container; check `docker logs citra-mongodb-init-rs` |
| Milvus exits or OOMs | raise Docker's memory to 8 GB+; it is the heaviest container |
| "Milvus collection does not exist" | `docker compose -f docker-compose.quickstart.yml exec citra-service python scripts/setup_milvus_schema.py` |
| Uploads fail | confirm the bucket exists in the MinIO console (http://localhost:9001) |
| Builder's dataset palette is empty | the catalogue crawl found nothing -- check the MCP registered: `docker logs citra-ds-mcp-demo-acme-bank` should show `[REGISTRATION] Registered tool:` with no failures |
| Demo data missing after impersonating | the demo MCP must be up: `docker compose -f demo-data/tenants/acme-bank/mcp/docker-compose.yml ps` |
| A port is already allocated | another Citra stack is running; override the published port in `.env` (e.g. `MINIO_API_PORT`) |

> All credentials in `.env` are local development defaults. Change every
> password and secret before this stack is reachable on any network.

## The services, and which one is which

Twelve application containers plus the data stores. You open exactly one of
them; the rest are called on your behalf.

| | Service | Host port | What it is |
|---|---|---|---|
| **UI** | **Citra-UI** | **8081** | The page you actually open. Expo / React Native web shell -- sign-in, chat, documents, My Apps. |
| **UI** | citra-app-runtime | 3100 | Next.js renderer for a published Decision App. Runs *inside* the shell -- never opened directly. |
| **API** | **smart-app-service** | **9100** | The engine, and the API you integrate against: authors apps, runs the agent loop, records decisions, serves the embed. |
| | Citra-User-Service | 7004 | Auth, orgs, departments, users. Issues the JWT every other service verifies. |
| | Citra-Service | 8085 | Chat, documents, the SOP library, the RAG reader. |
| | discovery-service | **9010** → 9000 | Registry of running MCPs; each self-registers on boot. |
| | data-discovery-service | 8095 | Crawls registered MCPs into the catalogue the builder picks datasets from. |
| | citra-mcp-service | 9090 | Sandbox toolbelt (web, files, OCR) for builder pods. |
| | action-sandbox-host | 7090 | Spawns the builder pod that authors an app. |
| | duckdb-query-service | 7301 | Analytics over structured files. |
| | reranker-service | 7302 | Retrieval reranking. |
| | playwright-render-service | 3001 | Headless render. |

Plus one **dept-MCP per tenant** (18504+), which is the only thing that touches
your systems of record. The data stores -- Mongo, Milvus, MinIO, Redis ×2, and
the demo's Postgres -- are not published to your machine at all, except MinIO's
console on 9001.

**The request path.** Your browser talks to Citra-UI on 8081; it gets a token
from Citra-User-Service and calls smart-app-service for everything about an
app. A Decision App's pages are rendered by citra-app-runtime and framed by the
shell, which is why *"the shell loads but the app area is blank"* localises to
the runtime rather than the shell. The agent reaches your data only through the
tenant's dept-MCP -- no service holds your connection strings.

> **discovery-service is the one service whose host port is not its container
> port.** Sibling containers call `discovery-service:9000`; from your machine it
> is **9010**. This is worth getting right because it does not fail loudly: a
> different Citra stack answers on host 9000 with the same
> `{"status":"ok","tool_count":N}` shape, so the wrong port can report a
> healthy pass against a stack that is not yours.

`ARCHITECTURE.md` has the same map with the shared packages and conventions.

## Building on it

One published spec, three ways to consume it -- the surfaces are described
under *Three surfaces, one intelligence* above; this is the mechanics.

### 1. Build a Decision App

**In the UI.** Sign in at http://localhost:8081, open **My Apps**, and describe
the app in plain English. A builder pod drafts the spec against the catalogue,
asks what it cannot infer, and publishes when you accept.

One question it asks matters more than the others: the **surface** -- a full
app, an embedded card, or headless. Pick the embedded surface if you intend to
embed it. It cannot be bolted on afterwards; an app built without an embed page
returns a 409 at step 3 below and has to be rebuilt.

**Headless.** The same engine, no UI:

```bash
# 1. Open a build session  ->  session_id
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:9100/build -d '{...}'

# 2. Drive the build conversationally
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/build/$SESSION_ID/chat/stream -d '{...}'

# 3. Validate the whole spec and go live
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:9100/publish -d '{...}'
```

`POST /apps/{slug}/edit` revises a published app, `GET /apps/{slug}/spec/lint`
checks a spec without publishing, and `POST /apps/{slug}/promote-to-prod`
copies test to prod.

### 2. Call it from your own system

Start from `GET /apps/{slug}/decision-contract` -- the app describes its own
request shape, endpoints and governance rules, so you are not guessing. The
loop itself, and its two governance guards, are under *Driving it headlessly*
above.

Rather than writing that HTTP by hand:

| | Package | Source |
|---|---|---|
| TypeScript | `@citra/decision-api` | `decision-api-sdk/typescript/` |
| Python | `citra-decision-api` | `decision-api-sdk/python/` |

`decision-api-sdk/INTEGRATION.md` is the one to read: auth, the governed loop,
rendering lists / details / media, item findings and feedback, plus raw-HTTP
recipes for Kotlin, Swift and curl where there is no SDK.
`decision-api-sdk/API-REFERENCE.md` is the endpoint list.

### 3. Embed the card in a screen you already have

Ask the app for its snippet -- nothing to look up or assemble:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/$SLUG/embed/snippet
```

It returns the embed key, the script URL, the record contract, and this, filled
in:

```html
<div id="citra-decision"></div>
<script src="https://apps.example.com/v1/citra.js"></script>
<script>
  const citra = Citra.init({ getToken: () => yourApp.citraToken() });
  citra.mount('#citra-decision', {
    embed:    'emb_live_...',
    recordId: yourApp.currentRecordId(),
    onDecision: (d) => yourApp.onCitraDecision(d),
  });
</script>
```

The card renders in a shadow root, so the host page's CSS and the card's cannot
reach each other. The key's prefix carries the environment (`emb_test_` /
`emb_live_`), so a UAT screen and a production screen can point at different
environments at the same time.

Three things that bite in this order:

- **409, "no embed page"** -- built without the embed surface. Rebuild it (§1).
- **409, "no embed key"** -- published before keys existed; republish to mint one.
- **A script URL you do not serve.** `APPS_BASE_URL` defaults to
  `https://apps.citra-ai.com`, so a self-hosted install hands out a snippet
  pointing at an origin that is not yours. Set it to your own before you copy
  the snippet anywhere.

`bank-demo/` is a complete worked integration to read against.

## Configuration

**There is one configuration file: `.env` at the repository root**, copied from
`.env.example`. Every service is fed it by `docker-compose.quickstart.yml`
(`env_file: [.env]`) -- services do not read per-directory `.env` files, so
there is exactly one place to look and one place to change.

`.env.example` ships with working local defaults; the only value you must set
is `LLM_API_KEY`. Going beyond a local evaluation means changing three things,
and each has a commented alternative in the file next to the value it replaces:

- **Your own domain.** Point `FRONTEND_URL`, `WEBSITE_URL`, `APP_URL`,
  `BASE_URL`, `CORS_ALLOWED_ORIGINS` and `CITRA_UI_ORIGIN` at it, and set
  `FORCE_HTTPS=true`. Turn `ALLOW_DEV_LOGIN` off -- it is a local-only
  passwordless path.
- **Your own model endpoint.** Point `LLM_BASE_URL` / `EMBEDDING_BASE_URL` /
  `VISION_BASE_URL` at your own vLLM (or any OpenAI-compatible) server instead
  of a hosted provider, and no prompt or document leaves your network. Note
  that changing the embedding model or `EMBEDDING_DIMENSION` means
  re-ingesting: the Milvus collection is created at that dimension.
- **Real secrets.** `make setup` generates fresh random values for
  `JWT_SECRET`, the MCP keys, and the signing and encryption keys. The
  database and object-store passwords are still the shipped defaults --
  change them before the stack is reachable on a network, or move them into
  Vault (`VAULT_ADDR`, commented at the end of `.env.example`).

Two directories keep their own env file, because they are not part of the
compose stack and so are never fed the root `.env`: `Monitoring-Service/`
(runs standalone) and `bank-demo/` (a separate Next.js app started with
`npm run dev`).

See `docs/change-the-demo.md` to point it at your own data sources instead of
the bundled demo, and `ARCHITECTURE.md` for how the pieces fit together --
the service map, the file-defined MCP, and the conventions this tree holds
itself to.

## Requirements

- Docker Engine 24+ and Compose **v2**, plus Python 3.9+ with `venv`/`pip`
  and `curl` on the host -- see [Prerequisites](#prerequisites) for the full
  list and the Debian/Ubuntu caveat. Node.js and git are *not* host
  requirements.
- Enough disk/RAM on one host to run the stack described in `ARCHITECTURE.md`
  (Mongo, Milvus, Redis, MinIO, plus the application services) -- comfortable
  on a single modern developer machine for evaluation; size a dedicated host
  for anything beyond that.
- An OpenAI-compatible model endpoint. OpenRouter (or any compatible hosted
  provider) works to evaluate; production deployments typically move to a
  self-hosted endpoint so no prompt or document ever leaves your network.

## License

**Apache License 2.0** -- open source, no strings.

Use it, modify it, run it in production, offer it as a service, fold it into a
commercial product. No non-production restriction, no Change Date, no licence to
buy. See `LICENSE` and `NOTICE`.

This was previously Business Source License 1.1, which reserved production use.
That restriction is gone and does not come back: an Apache grant is
irrevocable, so every version from v0.1.1 onward is permanently open source.

Commercial licensing and support contracts: contact@citra-ai.com

## Support

Community issues are handled best-effort with no response-time commitment.
An SLA is available to commercial customers. See `SECURITY.md` to report a
vulnerability and `CONTRIBUTING.md` to contribute.

## Community

**Discord:** https://discordapp.com/channels/1519703038724669551/1535992242433433700
-- the Decision System channel. Ask setup questions, report what broke on
your box, or tell us what a real deployment needs that this repo doesn't
have yet.

## About

Citra is a product of Trustedwear Tech Private Limited, incubated at IIT
Patna and backed by Startup India and MeitY. Talk to us: contact@citra-ai.com
-- https://citra-ai.com
