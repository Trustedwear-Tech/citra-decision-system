<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## What a "learned judgement" actually is

> **The demo is a hypothetical Indian bank**, so screenshots show rupee
> amounts and Indian digit grouping. Nothing in the platform is tied to
> that: currency, date order and ID checksums come from the country pack,
> and packs ship for `IN` and `US` today.

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
       src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/panels/17-app-memory.png" width="100%">
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
