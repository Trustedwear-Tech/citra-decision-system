<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## The core concepts, in plain terms

> **The demo is a hypothetical Indian bank**, so screenshots show rupee
> amounts and Indian digit grouping. Nothing in the platform is tied to
> that: currency, date order and ID checksums come from the country pack,
> and packs ship for `IN` and `US` today.

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

Moved to its own page, with the sandbox it belongs beside:
**[Governance and the sandbox](Governance-and-the-sandbox)**.

In short: the AI never writes SQL against your systems, every write is
schema-validated against the ontology before it runs, and a person approves
it.

### 3. The data catalogue -- the menu the builder orders from

The builder cannot offer you a table it has never seen. The
`data-discovery-service` is what makes the ontology *browsable*.


<p align="center">
  <img alt="The claim triage queue: 465 open claims read from Postgres through the department MCP"
       src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/screens/03-claim-queue.png" width="100%">
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
