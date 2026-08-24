<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Acme Bank & Insurance — demo tenant build plan

Status: **PLAN ONLY — nothing built.** Written 2026-07-28.

Clone the `acme-power` demo tenant into a second, India-flavoured BFSI demo:
lending, collections, insurance claims and sales. Same machinery, different
data. When it is proven in dev we switch the demo over from `acme-power`.

Read `demo-data/tenants/acme-power/SPEC.md` alongside this — everything here is
"the acme-power pattern, with these substitutions". Anything not mentioned is
deliberately identical.

---

## 0. Why this is mostly a data exercise, and where it is not

Four of the five moving parts are already generic and need only new fixtures:

| Part | Effort |
|---|---|
| Org / depts / users | **fixtures only** — `seed_tenant.py` is tenant-pluggable (`--tenant <id>`) |
| Postgres schema + rows | **new** — the schema is the domain, so this is the real work |
| MCP source registry | **fixtures only** — `build_mcp_sources.py` pattern, new tables |
| RAG SOP corpus | **new content** — 12 BFSI documents, India regulatory flavour |
| Decision Apps | **new** — 5 apps, and this time each ships WITH `case_signature` |

The one piece that is **not** additive, and the reason this is a switch-over
rather than a coexistence plan:

> **The platform runs ONE org at a time.** `data-discovery-service` pins
> `ORG_ID` to a single scalar and `main.py` refuses to boot the crawler unless
> it resolves to a real `orgs` row. So acme-power does not stay alongside
> acme-bank: at cut-over its registrations and data are **removed**, not left
> dormant. The repo fixture stays so the tenant can be rebuilt later; the
> databases do not. See §7.5.

**Order of work: dev first, proven, then prod.** Nothing touches the prod
database until the dev demo passes its E2E.

---

## 0.5. Phase 0 — purge the stale `dept_sources` instructions ✅ DONE 2026-07-28

Cleaned up before cloning anything, so the new tenant cannot inherit it.
Corrected across all three tenants (acme-power, acme-manufacturing, public-sector):
bring-up steps, folder tables, the generator's date-helper comment, and the
prose that described the registry as living in Mongo. Dated run reports were
left alone. Both `build_mcp_sources.py` generators still compile.

`dept_sources` as a mongoimport step is **dead**. Sources now live in
`sources.json`, mounted read-only as the MCP's `SOURCES_FILE`; the MCP
publishes to discovery on boot. `build_mcp_sources.py` already says so
("This file IS the MCP's source registry. No mongoimport.") — but several
docs still instruct the opposite, and copying one into the new tenant would
produce a phantom double registration.

Files carrying live-but-stale instructions:

| File | What to remove |
|---|---|
| `demo-data/tenants/acme-power/SPEC.md` | §10 step 3 — the `mongoimport … --upsertFields org_id,dept_id,source_id` step |
| `demo-data/tenants/acme-power/README.md` (~line 101) | the `mongoimport --uri … --db dev` block |
| `demo-data/tenants/acme-manufacturing/mcp/README.md` | step 5 "mongoimport … into dept_sources" and the two other references |
| `demo-data/tenants/public-sector/mcp/README.md`, `mcp/docker-compose.yml` | same instruction in comments |
| `demo-data/tenants/*/mcp/docker-compose.yml` (acme-power, acme-manufacturing) | header comments describing the registry as mongoimported |

Leave alone: `E2E-RUN-*.md`, `STATUS.md`, `demo-data/results/*` — dated run
records, not instructions. Also leave `build_mcp_sources.py`'s
extended-JSON date helper (the format is still what discovery expects) but
correct its comment, which explains itself in terms of mongoimport.

Each replacement says the same thing: *the generated `sources.json` IS the
registry; regenerate it and restart the MCP.*

---

## 1. Identifiers to fix before any file is written

Mirrors SPEC.md §1. All **decided**.

| Thing | Value | Note |
|---|---|---|
| Tenant / org id | `acme-bank` | decided — short, hyphen-light |
| Org display name | `Acme Bank & Insurance Ltd` | carries the full title |
| Domain | `acme-bank-demo.citra.ai` | matches the acme-power convention |
| Row-tag tenant_id | `acme-bank-demo` | |
| Departments | `lending`, `collections`, `claims`, `sales_distribution`, `central_ops` | §2 |
| Postgres container | `citra-ds-acme-bank-postgres`, host port **15444** → 5432 | decided — **separate container**, full isolation from acme-power |
| Postgres DB | `acme-bank` | |
| Postgres user / pass | `acme_bank` / `acme_bank_demo_pw` | |
| SQL env_prefix | `ACME_BANK_SQL` | |
| MCP container | `citra-ds-mcp-demo-acme-bank` | |
| MCP host port | `18504` → container `8090` | 8503 is acme-power |
| Docker network / project | `acme-bank-demo` | pinned, as acme-power does |
| MCP_API_KEY | `demo-acme-bank-mcp-key-local-only` | dev only |
| Milvus collection | *(none new)* | dept libraries now share ONE collection, isolated by `org_id` — see §5 |
| Deterministic seed | `20260728` | new seed so rows differ from acme-power |
| Faker locale | `en_IN` | the India flavour |
| Currency / units | INR, lakh/crore in copy | |

---

## 2. Departments and the five sources

acme-power has 4 structured + 1 semantic. Keep that shape exactly.

| source_id | type | dept_id | tables |
|---|---|---|---|
| `loan_origination` | structured | `lending` | `customers`, `loan_applications`, `bureau_pulls`, `disbursements` |
| `loan_servicing` | structured | `collections` | `loan_accounts`, `repayment_schedule`, `delinquencies`, `collection_activities` |
| `insurance_claims` | structured | `claims` | `policies`, `claims`, `claim_documents`, `surveyor_reports` |
| `sales_crm` | structured | `sales_distribution` | `leads`, `branches`, `agents`, `opportunities` |
| `acme_bank_policy_library` | semantic | `central_ops` | RAG corpus (§5) |

All four structured sources share ONE Postgres database, exactly as acme-power's
four share theirs. `connection` block for each:

```json
"connection": { "type": "postgres", "env_prefix": "ACME_BANK_SQL" }
```

`domain` block per source — this feeds the platform-emitted facets, so get it
right at authoring time:

```json
"domain": { "vertical": "bfsi", "sub_vertical": "lending|insurance|collections", "country": "IN" }
```

**Departments (`tenant.json`):**

| dept_id | name |
|---|---|
| `lending` | Loan Origination & Underwriting |
| `collections` | Collections & Recovery |
| `claims` | Insurance Claims |
| `sales_distribution` | Sales & Distribution (Branch, Agency, Bancassurance) |
| `central_ops` | Central Operations, Risk & Compliance |

---

## 3. Postgres schema — the actual work

New `schema.sql` + `seed_postgres.py`, same determinism discipline
(`random.seed(20260728)`, `Faker(["en_IN"])`, dates anchored to `now()` so the
demo always looks current).

**India-flavoured field conventions**, applied throughout:

- money in **INR** (`NUMERIC(14,2)`), amounts sized to Indian retail lending
- identifiers: masked **PAN** (`ABCDE1234F` → `ABCXX1234F`), masked Aadhaar
  (last 4 only), **IFSC** + branch code, **UTR** for transfers
- geography: Mumbai, Pune, Bengaluru, Hyderabad, Chennai, Ahmedabad, Jaipur,
  Lucknow, Indore, Kochi; states and PIN codes consistent with the city
- regulatory vocabulary: **DPD buckets** (0/30/60/90+), **NPA classification**
  (standard / SMA-0 / SMA-1 / SMA-2 / sub-standard), **IRDAI** claim TATs

### Table sketch (columns to be finalised in the tenant SPEC.md)

**lending**
- `customers` — customer_id, name, pan_masked, aadhaar_last4, mobile_masked, city, state, pin, occupation, employer, monthly_income_declared, existing_emi, cibil_score, kyc_status, segment
- `loan_applications` — application_id, customer_id, product (home/personal/auto/LAP/business), amount_requested, tenure_months, roi_offered, applied_at, branch_code, sourcing_channel (branch/DSA/digital), status (new/under_review/approved/rejected/disbursed), decision_reason
- `bureau_pulls` — pull_id, application_id, bureau (CIBIL/Experian), score, enquiries_6m, active_loans, overdue_amount, pulled_at
- `disbursements` — disbursement_id, application_id, amount, disbursed_at, utr, account_masked

**collections**
- `loan_accounts` — loan_account_no, customer_id, product, principal, roi, emi_amount, tenure_months, disbursed_on, outstanding, dpd, bucket, npa_class, branch_code
- `repayment_schedule` — schedule_id, loan_account_no, installment_no, due_date, emi_due, paid_amount, paid_on, status (paid/partial/unpaid/bounced), bounce_reason (NACH return codes)
- `delinquencies` — delinquency_id, loan_account_no, as_of, dpd, bucket, outstanding_overdue, last_paid_on, risk_flag
- `collection_activities` — activity_id, loan_account_no, agent_id, channel (call/SMS/field/legal), attempted_at, outcome (PTP/no-contact/dispute/refused), ptp_date, ptp_amount, notes

**claims** — *general insurance only (decided): motor, health, property. No
life, so no persistency / underwriting-mortality tables.*
- `policies` — policy_no, customer_id, line (motor/health/property), sum_insured, premium, start_date, end_date, status, nominee_name
- `claims` — claim_id, policy_no, loss_date, intimated_at, claim_type, claimed_amount, approved_amount, status (intimated/under_survey/approved/rejected/settled), rejection_reason, surveyor_id, tat_days
- `claim_documents` — document_id, claim_id, doc_type (FIR/estimate/invoice/discharge_summary/photo), file_url, uploaded_at, verified
- `surveyor_reports` — report_id, claim_id, surveyor_id, visited_on, assessed_amount, findings, photos_url, recommendation

**sales_distribution**
- `branches` — branch_code, name, city, state, region, cluster_head
- `agents` — agent_id, name, branch_code, channel (branch/DSA/bancassurance/agency), licence_no, active
- `leads` — lead_id, name, mobile_masked, city, product_interest, source (walk-in/digital/referral/campaign), created_at, status (new/contacted/qualified/converted/lost), assigned_agent_id, sla_due_at
- `opportunities` — opportunity_id, lead_id, product, expected_value, stage, probability, expected_close

### Volumes (target ≈ 250–300k rows, ~3 min seed — matching acme-power)

| Table | Rows |
|---|---|
| customers | 10,000 |
| loan_applications | 12,000 |
| bureau_pulls | 12,000 |
| disbursements | 7,000 |
| loan_accounts | 7,000 |
| repayment_schedule | 120,000 |
| delinquencies | 3,000 |
| collection_activities | 15,000 |
| policies | 9,000 |
| claims | 4,000 |
| claim_documents | 9,000 |
| surveyor_reports | 2,500 |
| branches / agents | 60 / 400 |
| leads | 6,000 |
| opportunities | 3,000 |

### Needle rows (deterministic demo paths — non-negotiable)

acme-power's demo works because specific rows are guaranteed to exist. Same
discipline; these are the ones the scripted demo will drive:

- `LAN-NEEDLE-001` — loan application where **declared income looks healthy but
  the bureau/tax picture does not corroborate it at the stated identifiers.**
  This is the canonical officer-judgement case from
  `docs/sop-rules-officer-judgement-plan.md` and should be the flagship
  "the app learned something the SOP never wrote down" moment.
- `LON-NEEDLE-002` — loan account at **DPD 61**, one bounced NACH, one broken
  PTP → the Collections app's priority case.
- `CLM-NEEDLE-003` — motor claim whose **estimate photo is byte-identical to a
  prior claim's** (reuse the acme-power duplicate-artifact trick, which the
  fraud screen already detects) → claim triage + fraud evidence.
- `CLM-NEEDLE-004` — health claim **intimated 40 days after loss date**, past
  the policy's intimation window → exclusion path.
- `LED-NEEDLE-005` — high-value lead, SLA breached, unassigned → sales routing.

---

## 4. Three Decision Apps + one dashboard app

Decided: **sales is a dashboard, not a decision.** A dashboard is not a separate
kind — it is a `page.kind: "dashboard"` inside a normal `kind: "app"`. So sales
becomes an app whose pages are dashboards ("what we sold"), with no officer
decision, no review gate, and therefore no `case_signature`. The leadership
briefing folds in as another page of that same app rather than a fifth app.

| # | App | Slug | Shape |
|---|---|---|---|
| 1 | Loan Application Triage | `acme-bank-loan-triage` | **decision** — approve / refer to credit / reject, with reason |
| 2 | Collections Prioritisation | `acme-bank-collections-priority` | **decision** — who to call today, channel, PTP follow-up |
| 3 | Insurance Claim Triage | `acme-bank-claim-triage` | **decision, multimodal** — settle / survey / investigate over documents + photos |
| 4 | Sales & Distribution | `acme-bank-sales` | **dashboard pages** — what we sold by product / branch / agent / channel, plus a leadership page. No decision, no signature. |

**Apps 1–3 ship with `case_signature` from day one.** This is the
lesson from the prod migration on 2026-07-27: 12 of 16 prod apps have no
signature, and without one their officer corrections are stored **uncoded** and
can never form a judgement — the app records feedback forever and learns
nothing. Draft signatures (facets from the app's own bound columns):

- **Loan triage** — `product` (enum), `amount_band` (band), `bureau_band`
  (band on cibil_score), `income_proof` (presence), `sourcing_channel` (enum).
  Reason codes: `income_not_corroborated`, `bureau_adverse`, `document_missing`,
  `policy_exclusion`, `amount_incorrect`, `data_stale_or_wrong`, `other`.
- **Collections** — `bucket` (enum), `outstanding_band` (band), `ptp_history`
  (presence), `product` (enum). Reason codes: `wrong_priority`,
  `already_paid`, `dispute_raised`, `wrong_contact`, `legal_hold`, `other`.
- **Claim triage** — `line` (enum), `claimed_amount_band` (band), `fir`
  (presence), `intimation_delay_band` (age_band), plus the platform fraud
  signals (`exact_duplicate`, `shared_identifier`). Reason codes:
  `evidence_insufficient`, `exclusion_applies`, `amount_incorrect`,
  `fraud_false_positive`, `document_mismatch`, `other`.
Author them through the builder (it now writes signatures by default and the
publish gate warns when a review-gated app lacks one) rather than hand-writing
JSON, so the flow itself gets exercised.

---

## 5. RAG SOP corpus — 12 documents

`demo-data/tenants/acme-bank/raw/policy/*.md`, ingested by a copy of
`ingest_docs.py` with `source_id=acme_bank_policy_library`, `dept=central_ops`,
`org_id=acme-bank`.

**No new Milvus collection.** Since 2026-07-10 dept libraries all share ONE
collection, isolated by scalar `org_id` + `dept` + `source_id`. The ingest
script resolves it via `shared_dept_collection()`. This removes the old
per-tenant collection-naming hazard entirely.

Proposed documents (synthetic, India-realistic — RBI/IRDAI shaped, no verbatim
regulatory text):

1. `retail_credit_policy.md` — eligibility, FOIR/DBR caps, LTV per product, income-proof matrix for salaried vs self-employed
2. `income_verification_sop.md` — payslip/bank-statement/ITR checks, **what to do when declared income and tax filings disagree** (the flagship case's SOP context)
3. `kyc_aml_sop.md` — CKYC, PAN/Aadhaar handling, re-KYC triggers, PEP screening
4. `fair_practices_code.md` — RBI Fair Practices Code adaptation: disclosure, grievance, recovery-agent conduct
5. `collections_recovery_sop.md` — bucket-wise strategy, call windows, PTP handling, field-visit rules, legal escalation
6. `npa_classification_circular.md` — SMA/NPA staging, provisioning, upgrade rules
7. `motor_claim_settlement_sop.md` — intimation TAT, surveyor appointment thresholds, salvage, cashless vs reimbursement
8. `health_claim_settlement_sop.md` — pre-auth, waiting periods, exclusions, discharge-summary requirements
9. `claims_fraud_indicators_circular.md` — reused/edited photos, mismatched identifiers, late intimation, repeat claimants
10. `grievance_redressal_policy.md` — internal ombudsman, escalation ladder, TATs
11. `sales_conduct_and_suitability.md` — mis-selling prevention, suitability, bancassurance conduct
12. `data_protection_and_customer_consent.md` — DPDP-aligned consent, retention, masking standards

Every chunk tagged `tag=demo`, `industry=bfsi`,
`source_id=acme_bank_policy_library`, `org_id=acme-bank`, `doc_path`, `page`.

---

## 6. Personas (`users.json`)

Same shape as acme-power (placeholders, no passwords, reached via **Impersonate
User → Demo personas**). Eight, one per demo path:

| Persona | Dept(s) | Role |
|---|---|---|
| Credit Officer (Pune branch) | `lending` | `user` |
| Credit Manager (West region) | `lending` | `dept_admin` |
| Collections Officer | `collections` | `user` |
| Collections Manager | `collections`, `lending` | `dept_admin` |
| Claims Officer (Motor) | `claims` | `user` |
| Claims Manager | `claims` | `dept_admin` |
| Branch Sales Manager | `sales_distribution` | `dept_admin` |
| COO / Central Ops | `central_ops`, all | `org_admin` |

At least **three officers must share each department** that owns a Decision App
— otherwise team judgements can never form (promotion needs 3 distinct
officers) and the memory demo has no upgrade path to show.

---

## 7. Build order — DEV

Follows SPEC.md §10, minus the step Phase 0 deletes.

| # | Step | Command |
|---|---|---|
| 0 | **Purge stale `dept_sources` docs** | §0.5 — do this first |
| 1 | Scaffold the tenant folder | copy `demo-data/tenants/acme-power/` → `acme-bank/`, strip data |
| 2 | Write the tenant `SPEC.md` | identifiers + schema frozen before code |
| 3 | Postgres container + schema + seed | `docker compose up -d citra-ds-acme-bank-postgres`, then `python scripts/seed_postgres.py` |
| 4 | Generate the source registry | `python scripts/build_mcp_sources.py` → `mcp/sources.json` |
| 5 | Bring up the MCP | `docker compose up -d --build citra-ds-mcp-demo-acme-bank`; `:18504/health` must show 5 sources |
| 6 | Ingest the SOP corpus | `python scripts/ingest_docs.py` (Citra-Service venv) |
| 7 | Seed org + depts + users | `python demo-data/scripts/seed_tenant.py --tenant acme-bank` |
| 8 | Author the 4 apps | via the builder; 1–3 must carry `case_signature` |
| 9 | Seed demo memory | `scripts/seed_memory.py --apply` — evidence only, judgements formed by consolidation |
| 10 | E2E | `python scripts/acme_bank_e2e.py` — mirror of `acme_power_e2e.py`, must pass before §7.5 |

Note the ordering change from acme-power: **the data-discovery flip is no longer
a build step.** It is the cut-over (§7.5), because it takes acme-power down.

---

## 7.5. Cut-over — one org in, one org out

Decided: we cannot keep two orgs at a time, so cut-over **removes acme-power
from the running system**. The repo fixture
(`demo-data/tenants/acme-power/`) stays, so the tenant can be rebuilt from
scratch; its runtime data does not.

Run in DEV first, verify, then repeat against PROD.

**A — point discovery at the new org**
1. `data-discovery-service`: `ORG_ID=acme-power` → `ORG_ID=acme-bank`, restart.
   Record the old value; this is the one-restart rollback.
2. `POST /crawl/run` → catalogue + Milvus recall index rebuilt for acme-bank.
3. Confirm the builder's dataset search returns acme-bank datasets and no
   acme-power ones.

**B — deregister acme-power**
4. `discovery-service`: remove acme-power's MCP registration and its source
   entries (the MCP re-registers on boot, so stop `mcp-demo-acme-power` first
   or it will re-appear).
5. `data-discovery-service`: delete acme-power's `data_catalogue` rows and its
   vectors from the recall index — otherwise the builder can still surface
   datasets that no longer resolve.

**C — delete acme-power data (dev, then prod)**
6. Postgres: stop + remove `acme-power-postgres` and its volume.
7. Milvus: delete the shared dept-library rows where `org_id = "acme-power"`
   (scalar-filtered delete — the collection itself is shared and must stay).
8. Mongo: drop acme-power's rows from `dept_sources` (if any remain),
   `data_catalogue`, `smartapp_apps` + agents, `smartapp_clauses`,
   `smartapp_corrections`, `decision_records`, `item_decision_records`.
9. Citra-User-Service: deactivate the acme-power org, its depts and personas.

**⚠ Step 8–9 are irreversible.** Precedent: the dev purge on 2026-06-28 that
reduced dev to `citra-ai` + `acme-power` could not be undone. Before running
either, take a Mongo dump of the acme-power collections and keep it until the
new demo has been shown at least once. Confirm explicitly before execution —
this plan does not authorise it.

**Order matters:** A before B (discovery must already point elsewhere, or the
crawler will re-file acme-power), and B before C (deleting the data under a
live registration leaves the builder pointing at datasets that 500).

---

## 8. Gotchas that will bite, and the decisions they force

**1. Separate Postgres container (decided).** `citra-ds-acme-bank-postgres` on host port
`15444`, its own volume and role. Full isolation: acme-power's container can be
destroyed at cut-over without touching the new demo. Port 15444 must be free —
check before step 3, since it is baked into `sources.json` and the compose.

**2. The stale `dept_sources` mongoimport is removed in Phase 0**, before the
clone, so the new tenant cannot inherit it. See §0.5.

**3. Short org id (decided): `acme-bank`.** `acme-power`'s hyphen has bitten us
before (Milvus naming, per-source timeouts). The shared dept-library collection
removes the worst of it, but keep ids short anyway.

**4. Single-org is a removal, not a coexistence.** Flipping `ORG_ID` is step A
of the cut-over (§7.5), and it is followed by deregistering and deleting
acme-power. Keep the old `ORG_ID` value written down: until step C runs, the
whole thing is still one restart from being reversed.

**5. Apps must carry `case_signature` at publish.** Otherwise corrections are
uncoded and no judgement can ever form. The publish gate warns; treat that
warning as an error for this tenant.

**6. Three officers minimum per app-owning department**, or the demo can only
ever show "individual judgement" and never the promotion to team.

**7. Prod memory is per-app and starts empty.** The new tenant's judgements
must be seeded through the evidence path (`seed_memory.py` pattern), never by
inserting clauses — hand-written clause text breaks the provenance the Memory
screen shows underneath every judgement.

**8. The point of no return is §7.5 step C.** Up to and including step B,
rollback is: restore `ORG_ID`, restart `mcp-demo-acme-power`, re-crawl. Once
the Postgres volume and the Mongo rows are gone, acme-power can only be
rebuilt from the repo fixture — which means re-seeding 287k rows, re-ingesting
the policy corpus, re-authoring the apps, and losing every decision record and
judgement it accumulated. Take the dump first.

---

## 9. Effort estimate

| Phase | Scope | Rough size |
|---|---|---|
| 0 | Purge stale `dept_sources` instructions | small |
| A | Tenant SPEC + folder scaffold | small |
| B | Postgres schema + deterministic seeder (~250k rows, needles) | **largest single piece** |
| C | `sources.json` builder + MCP bring-up | small |
| D | 12 SOP documents | medium (content writing) |
| E | tenant.json + users.json + seed | small |
| F | 3 Decision Apps (with signatures) + 1 dashboard app | medium |
| G | Memory seed + E2E test script | small |
| H | Cut-over in DEV, verify | small |
| I | Repeat the whole build + cut-over against PROD | medium |

B and D are the real work; everything else is mechanical.

---

## 10. Decisions taken, and what is still open

**Decided (2026-07-28):**

- Org id `acme-bank`; display name `Acme Bank & Insurance Ltd`
- **Separate** Postgres container, port 15444
- Insurance scope: **general only** (motor, health, property) — no life
- Sales is a **dashboard page inside an app**, not a Decision App; the
  leadership briefing folds into it
- Stale `dept_sources` instructions are purged **first**, as Phase 0
- acme-power is **removed at cut-over** — deregistered from discovery and
  data-discovery, and its data deleted in dev and prod. The repo fixture stays.
- **Dev first, tested, then prod**

- **acme-power: code kept, ALL data deleted.** The repo fixture
  (`demo-data/tenants/acme-power/`) stays so the tenant can be rebuilt; every
  runtime trace goes — Decision Apps, decision records, learned judgements,
  corrections, catalogue, sources, Postgres, Milvus rows, org and personas.
  **Only acme-bank runs in the system.** §7.5 step C is the full delete, in
  both dev and prod.
- **Dedicated Postgres for acme-bank** (`citra-ds-acme-bank-postgres`, port 15444). The
  acme-power container is destroyed at cut-over, not shared.

**Still open:**

1. **Backup before the irreversible step** — Mongo dump + `pg_dump` kept until
   the new demo has been shown at least once? Assumed yes unless you say
   otherwise; it costs minutes and buys back a rebuild that would cost a day.
2. **Prod timing** — immediately after dev passes, or held for a demo date?
