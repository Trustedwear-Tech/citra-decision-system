<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# "Real Money Saved" — ROI / Value KPIs Plan

> Status: PLAN (2026-07-21) · Owner: rohit@trustedweartech.com
> Companion: `wedge-strengthening-plan.md` (adoption metrics),
> `vertical-country-ontology-plan.md` (domain packs), `citra-self-improving-loop-plan.md`
> (decision ledger + outcome poller).

## 0. The problem, precisely

"How much money did Citra save/recover?" is answerable only at the intersection
of FOUR things, each owned by a different layer:

| Ingredient | Owner today | Builder can see it? |
|---|---|---|
| **Domain semantics** — what a recovery/sanction/claim-denial IS, which column holds the ₹/$ amount | ontology (sources.json) | ✅ via catalogue |
| **Decision ledger** — which cases Citra touched, what was recommended, what the officer did | `decision_records` in Citra Mongo (per app) | ❌ **not in the catalogue** |
| **Outcomes** — did the decision stick, what actually happened at the SoR | outcome poller (Stage 4) stamps `outcome.label` | ❌ same ledger |
| **Realized value** — the amount attached to the outcome (payments received, loss avoided) | **nobody** — labels exist, amounts don't | ❌ doesn't exist |

The builder's UI strength (generate the KPI page the user asks for, in their
vocabulary, against their schema) is real — but today it can only generate
pages over SoR data. The money story needs the ledger and value joined in.

**What already exists to build on (inventory, verified in code):**
1. **Platform data sources are an established pattern** — `DataSource.type`
   already includes `workflow_staging` and `smart_app_records` with their own
   resolvers in `panel_data.py`. Adding a third platform type is plumbing, not
   architecture.
2. **`value_fields` already exists in the ontology** (`fraud_screening.value_fields`,
   documented as "advisory, not consumed") — the natural seed for value semantics.
3. **`decision_history` already declares outcome semantics** (`outcome_field`,
   `good_values`/`bad_values`, `key_field`, `settling_window_days`) — the poller
   uses it to stamp labels. Value is the missing sibling.
4. **Source-side aggregation is solved** — dashboard metrics already push
   COUNT/SUM to the source (`_resolve_dashboard_metrics`), so "how much amount
   sanctioned this month" is a SoR aggregate the platform can compute correctly
   over the WHOLE table today. The gap is only aggregates over the ledger×SoR JOIN.
5. **The app-owned overlay** (Mongo columns on SoR records by id — notes,
   comments, officer-entered fields) is built with a governed write path; KPI
   pages can aggregate overlay fields the same as SoR fields.
6. **E3's `key_values` stamp** (2026-07-20) gives every new decision record an
   indexed join key to its SoR record — exactly the join the value computation
   needs.

## 1. Architecture: two tiers + one canonical spine

Named explicitly (from the strategy discussion):

- **Platform tier** (exists, zero-config, measures *Citra*): Success Rate,
  Screening Health, Memory impact. Product-performance metrics.
- **App tier** (builder-generated, measures *the business*): recovery ₹,
  cost per case, sanction throughput — only exists if built, per app, per
  ontology.
- **NEW — the canonical value spine**: ONE server-side computation of the
  headline money numbers, from the ledger × ontology value semantics, exposed
  as (a) an endpoint the main UI renders as a "Money impact" card and (b) a
  platform dataset the builder binds KPI pages to.

**Why a canonical spine and not just builder-generated aggregation:** the
credibility argument from the discussion. Generated flexibility is a strength
in operations and a liability in a business case — if the sponsor can ask the
same question three ways and get three numbers, a CFO discounts all of them.
So: headline money = ONE computation, defined in the ontology (versioned,
auditable, frozen at day zero); builder-generated pages visualize freely ON
TOP of that spine plus SoR aggregates, but cannot silently redefine "recovered".

## 2. Ontology extension — `value_semantics` (the day-zero freeze lives HERE)

A sibling of `decision_history` on the dataset block (same authoring grain,
same carry-through path — registry → describe → catalogue → builder):

```jsonc
"decision_history": { ... existing outcome mapping ... },
"value_semantics": {
  // What one unit of realized value IS, in this dataset's terms.
  "value_kind": "recovered",            // recovered | prevented_loss | sanctioned | settled  (closed enum)
  // The record's OWN amount column (exposure / claim amount / sanction amount).
  "exposure_field": "outstanding_amount",
  // Where value is REALIZED (often a different dataset — the payments ledger):
  "realization": {
    "dataset": "billing.payments",       // catalogue-validated, like verify_against
    "match_field": "consumer_id",        // realization rows join the case by this
    "amount_field": "amount",
    "date_field": "payment_date",
    "window_days": 90                    // realized only if within N days of the decision
  },
  // THE ATTRIBUTION RULE — pre-agreed, in writing, versioned in git:
  "attribution": "approved_recommendation",  // approved_recommendation | any_citra_touched | approved_within_window
  // For prevented_loss (fraud/claims): value = exposure of cases decided bad.
  "prevented_when": ["rejected", "denied", "failed"]   // decision outcomes that count as prevention
}
```

Rules, consistent with everything else in the ontology: closed enums,
`extra=forbid`, every named column/dataset validated against the catalogue at
publish (the `verify_against` validation pattern reused), vertical packs may
supply defaults (insurance claims ⇒ `prevented_loss`, collections ⇒
`recovered` with payments realization) but explicit always wins. **Because it
lives in sources.json, the pilot's metric definitions are frozen by a git
commit at day zero** — the "defined before anyone knew the result" property
the CFO conversation needs, with an audit trail for free.

Per-vertical presets (ship in the deploy templates):
| Vertical | value_kind | Realization |
|---|---|---|
| banking/loan_recovery, utility/power_recovery | `recovered` | payments ledger post-decision within window |
| insurance/claims | `prevented_loss` | none — exposure of DENIED fraudulent claims |
| banking/loan_origination | `prevented_loss` (bad sanction avoided) + `sanctioned` throughput | none / disbursement ledger |
| field_service | usually none (operational KPIs only) | — |

## 3. Outcome job extension — stamp `outcome.value`

The Stage-4 poller already re-reads the SoR by key and stamps
`outcome.label`. Extend the same pass:

- When the app's dataset declares `value_semantics.realization`: read the
  realization rows for the case key (structured read plane — `_read_row_by_key`
  family / a small aggregate), SUM `amount_field` within `window_days` of the
  decision, stamp `outcome.value = {amount, currency (from domain), kind,
  realized_at, definition_version}`.
- When `value_kind = prevented_loss`: on a decision whose committed outcome ∈
  `prevented_when`, stamp `outcome.value.amount = exposure_field` of the record
  (read at decision time and frozen — exposure at the moment of decision, not
  today's balance).
- `definition_version` = hash of the `value_semantics` block that computed it —
  a number can always be traced to the definition that produced it, and a
  definition change mid-pilot is VISIBLE (old rows keep the old version).
- Backfill command for existing ledgers (same shape as the E3 stamp: new
  records get it at write, a one-shot job walks history).

Currency comes from the domain triple — no per-metric currency authoring.

## 4. The ledger becomes builder-visible — `decision_ledger` platform dataset

New `DataSource.type: "decision_ledger"` resolved in `panel_data.py` (the
`workflow_staging`/`smart_app_records` pattern), row shape = the flattened
learning-facing projection of `decision_records`:

```
decision_id · slug · case key_values · mode (accepted/with-changes/rejected/auto)
· decision (text) · decided_at · outcome.label · outcome.value.amount
· outcome.value.kind · retrieval_count · definition_version
```

- Scoped per app by default (`ref: <slug>` — own app), org-wide for org_admin
  audiences; tenant-scoped always; PII-light by construction (keys + amounts,
  not case payloads).
- Described in the builder's tool catalogue + `citra-app-spec` skill: **"for
  money/ROI pages, bind the KPI tiles to `decision_ledger` (headline numbers)
  and SoR aggregates (operational counts); never recompute 'recovered' from
  raw payments in the app — the ledger row already carries the canonical
  value."** That sentence is what keeps generated pages consistent with the
  spine.
- The overlay participates naturally: overlay fields ride the SoR rows the
  ledger joins to, so "notes"/officer-entered adjustments are aggregatable.

This is the piece that turns "user asks for an ROI page" into a one-prompt
builder job: the schema is in the catalogue, the semantics are in the ontology,
the numbers are pre-computed on the rows.

## 5. Canonical endpoints + main-UI card

- `GET /org/value-stats?period=` and `/apps/{slug}/value-stats`: aggregates
  over decision_records `outcome.value` — recovered ₹, prevented ₹, by app, by
  month, split by mode (accepted vs auto), **with the attribution rule and
  definition_version echoed in the response**. Same tenant-scoping discipline
  as decision-stats (both org_id and tenant_id claims).
- Main Citra-UI: a **"Money impact"** admin card next to Success Rate — the
  two-tier story complete on the home panel: *what Citra did* (Success Rate,
  Screening Health, Memory) and *what it was worth* (Money impact). Same
  honest empty-states as Memory impact ("no value semantics declared for any
  app — annotate sources.json"; "N decisions awaiting realization window").
- A **baseline block** in the response when `decision_history` history exists:
  the same metric computed over the pre-Citra period (historical rows) — the
  before/after the pilot report needs, computed by the same definition.

## 6. Aggregation mechanics (answering the "how much amount sanctioned" challenge)

Three aggregation lanes, each already having a home:
1. **Pure SoR aggregates** (amount sanctioned this month, total outstanding):
   source-side COUNT/SUM push — exists (`_resolve_dashboard_metrics`); extend
   the pushable functions if a vertical needs AVG/MIN/MAX. The app "generates
   it anyway" — correct, and it stays at the source where it's cheap and whole-table-true.
2. **Ledger aggregates** (money attributed to Citra): Mongo aggregation
   pipeline inside value-stats / the decision_ledger resolver — small
   collections, indexed by (tenant, created_at) and (tenant, key_values).
3. **Join aggregates** (ledger × SoR, e.g. recovery rate on touched vs
   untouched accounts): NEVER live-joined at page load — the poller
   pre-computes the value onto the ledger row (§3), so page-time "joins" are
   just ledger aggregates. This is the deliberate design choice that keeps KPI
   pages fast and the numbers stable.

## 7. Pilot / POC playbook (the sales motion from the discussion)

1. **Day zero, before any measurement**: agree the 3–5 judged metrics in
   writing; author them as `value_semantics` (+ attribution) in sources.json;
   commit. The freeze is a git hash.
2. **First working session**: build the customer's KPI page live with the
   builder, on their schema, bound to `decision_ledger` + their SoR aggregates
   — the demo moment that sells the builder, and the sponsor watches their own
   number from week one instead of getting a report assembled at day 90.
3. **During pilot**: officers work cases; poller stamps labels + values;
   Money-impact card and their KPI page move on their own.
4. **Day 90**: the report is `/org/value-stats` for the pilot window + the
   baseline block, with `definition_version` proving the definitions never
   moved. Any extra views the sponsor asked for along the way were generated
   against the same spine, so every number reconciles.

**POC target**: acme-power theft/recovery. Annotate
`field_operations.theft_cases` (or recovery dataset) with `value_kind:
recovered`, realization = `billing.payments` by consumer_id, window 90d;
backfill; show the Money-impact card + a builder-generated "Recovery ROI" page
in the demo flow. The seeded data already contains payments — realization will
produce non-zero numbers.

## 8. Build phases

| Phase | Scope | Est |
|---|---|---|
| **V1** | Ontology `value_semantics` (registry + mirrors + schema + template presets + validation) | ~2 d |
| **V2** | Poller stamps `outcome.value` (+ definition_version, exposure freeze, prevented_loss path) + backfill command | ~3 d |
| **V3** | `decision_ledger` platform DataSource + panel_data resolver + builder skill/tool-catalogue teaching | ~2–3 d |
| **V4** | `/org/value-stats` + `/apps/{slug}/value-stats` (attribution + baseline + definition echo) + Money-impact card in Citra-UI | ~3 d |
| **V5** | acme POC wiring: annotation, backfill, builder-generated Recovery ROI page, demo script | ~1–2 d |

Order rationale: V1+V2 create the value substrate (nothing user-visible yet but
every decision starts accruing value from deploy day — the earlier this ships,
the longer the pilot history); V3 unlocks the builder story; V4 is the
credibility spine + exec surface; V5 proves it end-to-end on the demo tenant.

## 9. Non-goals / guardrails

- **No invented baselines**: if there is no pre-Citra history, the report says
  so — never a synthetic counterfactual.
- **No page-time redefinition of money**: builder pages may slice/visualize
  ledger values, never recompute them from raw sources (skill rule + the
  canonical endpoint make the lazy path the correct path).
- **Attribution is declared, not argued**: one enum in the ontology, agreed at
  day zero; changing it is a visible ontology change with a new
  definition_version — never a silent re-cut of history.
- **Value stamping is fail-loud**: a realization read that errors stamps
  `outcome.value_error`, never a silent zero (a zero means "genuinely nothing
  realized", not "the read failed").
