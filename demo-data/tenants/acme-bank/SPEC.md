<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Acme Bank & Insurance demo tenant — canonical build spec

Single source of truth for the `acme-bank` demo tenant. Every script, source
definition, Decision App and persona below MUST use the exact identifiers in
this file. Mirrors the structure of `demo-data/tenants/acme-power/`.

India-flavoured BFSI demo: retail lending, collections, general insurance
claims, and a sales dashboard. Synthetic data shaped to how an Indian bank +
general-insurance arm actually operates — INR, PAN/Aadhaar masking, IFSC,
DPD buckets, SMA/NPA staging, RBI/IRDAI-shaped SOPs.

Plan and rationale: `docs/acme-bank-demo-plan.md`.

---

## 1. Identifiers (do not deviate)

| Thing | Value |
|---|---|
| Tenant / org id | `acme-bank` |
| Org display name | `Acme Bank & Insurance Ltd` |
| Domain | `acme-bank-demo.citra.ai` |
| Row-tag tenant_id | `acme-bank-demo` |
| Departments | `lending`, `collections`, `claims`, `sales_distribution`, `central_ops` |
| Postgres DB | `acme_bank` (underscore — Postgres identifier, matches the container env) |
| Postgres user / pass | `acme_bank` / `acme_bank_demo_pw` |
| Postgres container | `citra-ds-acme-bank-postgres` (host `15444` → `5432`) |
| SQL env_prefix | `ACME_BANK_SQL` |
| Source registry | `mcp/sources.json` — mounted read-only as the MCP's `SOURCES_FILE`; there is NO import step |
| Dept-library collection | resolved by `shared_dept_collection()` — ALL dept libraries share one Milvus collection, isolated by `org_id` + `dept` + `source_id` |
| MCP container | `citra-ds-mcp-demo-acme-bank` |
| MCP host port | `18504` → container `8090` |
| Docker network / compose project | `acme-bank-demo` |
| MCP_API_KEY | **= data-discovery-service's `SERVICE_API_KEY`** — that service forwards one global key to every dept-MCP, so a tenant-unique key just 403s the crawler and leaves the catalogue with no structured datasets |
| Discovery URL | `http://host.docker.internal:9000` |
| AUTHZ_ENFORCE | `true` (fail-closed) |
| Deterministic seed | `20260728` |
| Faker locale | `en_IN` |
| Currency | INR (`NUMERIC(14,2)`) |

**Single-org platform.** `data-discovery-service` pins `ORG_ID` to one org.
Bringing acme-bank up means acme-power comes down — see the cut-over in
`docs/acme-bank-demo-plan.md` §7.5. Do not run both.

---

## 2. The 5 sources (entries in `mcp/sources.json`)

| source_id | type | dept_id | backend | datasets (tables) |
|---|---|---|---|---|
| `loan_origination` | structured | `lending` | Postgres | customers, loan_applications, bureau_pulls, disbursements |
| `loan_servicing` | structured | `collections` | Postgres | loan_accounts, repayment_schedule, delinquencies, collection_activities |
| `insurance_claims` | structured | `claims` | Postgres | policies, claims, claim_documents, surveyor_reports |
| `sales_crm` | structured | `sales_distribution` | Postgres | branches, agents, leads, opportunities |
| `acme_bank_policy_library` | semantic | `central_ops` | Milvus | RAG corpus — §6 |

All four structured sources share ONE Postgres database. `connection` block for
each:

```json
"connection": { "type": "postgres", "env_prefix": "ACME_BANK_SQL" }
```

`domain` per source. **This is a CLOSED enum** (`source-mcp-template/
registry_models.py`) and the MCP refuses to boot on anything outside it — an
invented `"bfsi"` vertical failed startup validation on the first try, which is
the guard working as designed. Allowed pairings:

| vertical | sub_vertical |
|---|---|
| `banking` | `loan_origination`, `loan_recovery` |
| `insurance` | `claims`, `underwriting` |
| `utility` | `power_recovery`, `metering_inspection` |
| `field_service` | `equipment_inspection` |

`country` is ISO-3166 alpha-2 from `{IN, US}` and gates the locale pack (ID
checksums, date order, currency). All three are emitted as automatic facets on
every case, so they are part of the memory contract too.

| source | domain |
|---|---|
| `loan_origination` | banking / loan_origination / IN |
| `loan_servicing` | banking / loan_recovery / IN |
| `insurance_claims` | insurance / claims / IN |
| `sales_crm` | banking / loan_origination / IN — sales is the front of origination |

Each dataset entry: `id` = `<source_id>.<table>`, `physical_name` = table name,
`kind` = `sql`, `read_via` = `{ "kind": "sql", "target": "<table>" }`, plus
declared `columns[]` so the catalogue stays complete when Postgres is down.

`visibility` for every source:

```json
"visibility": { "roles_allowed": ["user","dept_admin","org_admin","super_admin"],
                "cross_org_ids": [], "public_within_org": false }
```

`acme_bank_policy_library` sets `public_within_org: true` (every app grounds on
it). All sources: `org_id: "acme-bank"`, `is_active: true`, `is_demo: true`,
`query_timeout_seconds: 30`.

---

## 3. Postgres schema (DDL — schema.sql)

Database `acme_bank`, schema `public`. Dropped + recreated by the seed, so a
re-run produces an identical database (idempotent by construction).

### Conventions

- Money in **INR**, `NUMERIC(14,2)`. Realistic Indian retail sizes.
- **PAN masked** (`ABCXX1234F`), **Aadhaar last 4 only**, mobile masked
  (`XXXXXX1234`). No unmasked identifier is ever stored — the app must be
  demonstrably safe to show on a projector.
- Cities: Mumbai, Pune, Bengaluru, Hyderabad, Chennai, Ahmedabad, Jaipur,
  Lucknow, Indore, Kochi. State + PIN consistent with the city.
- **DPD buckets**: `0`, `1-30`, `31-60`, `61-90`, `90+`.
- **NPA staging**: `standard`, `sma_0`, `sma_1`, `sma_2`, `sub_standard`.
- Dates anchored to `now()` so the demo always looks current.

```sql
-- ── lending ──────────────────────────────────────────────────────────
CREATE TABLE customers (
  customer_id        VARCHAR(16)  PRIMARY KEY,      -- CUS-0000123
  name_full          VARCHAR(80)  NOT NULL,
  pan_masked         VARCHAR(12)  NOT NULL,         -- ABCXX1234F
  aadhaar_last4      VARCHAR(4),
  mobile_masked      VARCHAR(14),                   -- XXXXXX1234
  email              VARCHAR(80),
  city               VARCHAR(40)  NOT NULL,
  state              VARCHAR(40)  NOT NULL,
  pin                VARCHAR(6)   NOT NULL,
  occupation         VARCHAR(24)  NOT NULL,         -- salaried|self_employed|professional|business
  employer_name      VARCHAR(80),
  monthly_income_declared NUMERIC(14,2) NOT NULL,
  existing_emi       NUMERIC(14,2) NOT NULL DEFAULT 0,
  cibil_score        INTEGER,                       -- 300-900, NULL = no bureau history
  kyc_status         VARCHAR(16)  NOT NULL,         -- verified|pending|re_kyc_due
  customer_segment   VARCHAR(16)  NOT NULL,         -- mass|affluent|hni|nri
  onboarded_on       DATE         NOT NULL
);

CREATE TABLE loan_applications (
  application_id     VARCHAR(20)  PRIMARY KEY,      -- LAN-2026-000123
  customer_id        VARCHAR(16)  NOT NULL REFERENCES customers(customer_id),
  product            VARCHAR(16)  NOT NULL,         -- home|personal|auto|lap|business
  amount_requested   NUMERIC(14,2) NOT NULL,
  tenure_months      INTEGER      NOT NULL,
  roi_offered        NUMERIC(5,2),
  applied_at         TIMESTAMP    NOT NULL,
  branch_code        VARCHAR(12)  NOT NULL,
  sourcing_channel   VARCHAR(16)  NOT NULL,         -- branch|dsa|digital|bancassurance
  income_proof_type  VARCHAR(20),                   -- payslip|itr|bank_statement|none (NULL/none = missing)
  itr_declared_income NUMERIC(14,2),                -- income per the tax filing; NULL = not filed/produced
  ltv_percent        NUMERIC(5,2),
  foir_percent       NUMERIC(5,2),                  -- fixed-obligation-to-income ratio
  status             VARCHAR(16)  NOT NULL,         -- new|under_review|approved|rejected|disbursed
  decision_reason    VARCHAR(200),
  decided_by         VARCHAR(80),
  decided_at         TIMESTAMP
);

CREATE TABLE bureau_pulls (
  pull_id            VARCHAR(20)  PRIMARY KEY,
  application_id     VARCHAR(20)  NOT NULL REFERENCES loan_applications(application_id),
  bureau             VARCHAR(12)  NOT NULL,         -- cibil|experian|crif
  score              INTEGER,
  enquiries_6m       INTEGER      NOT NULL DEFAULT 0,
  active_loans       INTEGER      NOT NULL DEFAULT 0,
  overdue_amount     NUMERIC(14,2) NOT NULL DEFAULT 0,
  writeoff_flag      BOOLEAN      NOT NULL DEFAULT FALSE,
  pulled_at          TIMESTAMP    NOT NULL
);

CREATE TABLE disbursements (
  disbursement_id    VARCHAR(20)  PRIMARY KEY,
  application_id     VARCHAR(20)  NOT NULL REFERENCES loan_applications(application_id),
  amount             NUMERIC(14,2) NOT NULL,
  disbursed_at       DATE         NOT NULL,
  utr                VARCHAR(24),
  account_masked     VARCHAR(20),
  ifsc               VARCHAR(11)
);

-- ── collections ──────────────────────────────────────────────────────
CREATE TABLE loan_accounts (
  loan_account_no    VARCHAR(20)  PRIMARY KEY,      -- LON-2026-000123
  customer_id        VARCHAR(16)  NOT NULL REFERENCES customers(customer_id),
  application_id     VARCHAR(20)  REFERENCES loan_applications(application_id),
  product            VARCHAR(16)  NOT NULL,
  principal          NUMERIC(14,2) NOT NULL,
  roi                NUMERIC(5,2) NOT NULL,
  emi_amount         NUMERIC(14,2) NOT NULL,
  tenure_months      INTEGER      NOT NULL,
  disbursed_on       DATE         NOT NULL,
  outstanding        NUMERIC(14,2) NOT NULL,
  dpd                INTEGER      NOT NULL DEFAULT 0,
  bucket             VARCHAR(8)   NOT NULL,         -- 0|1-30|31-60|61-90|90+
  npa_class          VARCHAR(14)  NOT NULL,         -- standard|sma_0|sma_1|sma_2|sub_standard
  branch_code        VARCHAR(12)  NOT NULL,
  restructured       BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE TABLE repayment_schedule (
  schedule_id        VARCHAR(24)  PRIMARY KEY,
  loan_account_no    VARCHAR(20)  NOT NULL REFERENCES loan_accounts(loan_account_no),
  installment_no     INTEGER      NOT NULL,
  due_date           DATE         NOT NULL,
  emi_due            NUMERIC(14,2) NOT NULL,
  paid_amount        NUMERIC(14,2) NOT NULL DEFAULT 0,
  paid_on            DATE,
  status             VARCHAR(12)  NOT NULL,         -- paid|partial|unpaid|bounced
  bounce_reason      VARCHAR(40)                    -- NACH return narrative, e.g. 'insufficient_funds'
);

CREATE TABLE delinquencies (
  delinquency_id     VARCHAR(24)  PRIMARY KEY,
  loan_account_no    VARCHAR(20)  NOT NULL REFERENCES loan_accounts(loan_account_no),
  as_of              DATE         NOT NULL,
  dpd                INTEGER      NOT NULL,
  bucket             VARCHAR(8)   NOT NULL,
  overdue_amount     NUMERIC(14,2) NOT NULL,
  last_paid_on       DATE,
  risk_flag          VARCHAR(16)                    -- skip_trace|dispute|legal|none
);

CREATE TABLE collection_activities (
  activity_id        VARCHAR(24)  PRIMARY KEY,
  loan_account_no    VARCHAR(20)  NOT NULL REFERENCES loan_accounts(loan_account_no),
  agent_id           VARCHAR(16)  NOT NULL,
  channel            VARCHAR(12)  NOT NULL,         -- call|sms|whatsapp|field|legal
  attempted_at       TIMESTAMP    NOT NULL,
  outcome            VARCHAR(20)  NOT NULL,         -- ptp|no_contact|dispute|refused|paid|wrong_number
  ptp_date           DATE,
  ptp_amount         NUMERIC(14,2),
  ptp_kept           BOOLEAN,
  notes              VARCHAR(240)
);

-- ── claims (GENERAL insurance only: motor, health, property) ──────────
CREATE TABLE policies (
  policy_no          VARCHAR(20)  PRIMARY KEY,      -- POL-MTR-2026-00123
  customer_id        VARCHAR(16)  NOT NULL REFERENCES customers(customer_id),
  line               VARCHAR(10)  NOT NULL,         -- motor|health|property
  product_name       VARCHAR(60)  NOT NULL,
  sum_insured        NUMERIC(14,2) NOT NULL,
  premium_annual     NUMERIC(14,2) NOT NULL,
  start_date         DATE         NOT NULL,
  end_date           DATE         NOT NULL,
  status             VARCHAR(12)  NOT NULL,         -- active|lapsed|cancelled|expired
  nominee_name       VARCHAR(80),
  intimation_window_days INTEGER  NOT NULL DEFAULT 30   -- claim must be intimated within
);

CREATE TABLE claims (
  claim_id           VARCHAR(20)  PRIMARY KEY,      -- CLM-2026-000123
  policy_no          VARCHAR(20)  NOT NULL REFERENCES policies(policy_no),
  customer_id        VARCHAR(16)  NOT NULL REFERENCES customers(customer_id),
  claim_type         VARCHAR(24)  NOT NULL,         -- own_damage|third_party|theft|hospitalisation|fire|burglary
  loss_date          DATE         NOT NULL,
  intimated_at       TIMESTAMP    NOT NULL,
  intimation_delay_days INTEGER   NOT NULL,         -- derived; drives the age_band facet
  claimed_amount     NUMERIC(14,2) NOT NULL,
  approved_amount    NUMERIC(14,2),
  status             VARCHAR(16)  NOT NULL,         -- intimated|under_survey|approved|rejected|settled
  rejection_reason   VARCHAR(200),
  surveyor_id        VARCHAR(16),
  fir_number         VARCHAR(30),                   -- NULL = no FIR (presence facet)
  tat_days           INTEGER,
  decided_by         VARCHAR(80),
  decided_at         TIMESTAMP
);

CREATE TABLE claim_documents (
  document_id        VARCHAR(24)  PRIMARY KEY,
  claim_id           VARCHAR(20)  NOT NULL REFERENCES claims(claim_id),
  doc_type           VARCHAR(24)  NOT NULL,         -- fir|repair_estimate|invoice|discharge_summary|damage_photo|policy_copy
  file_url           VARCHAR(300) NOT NULL,
  uploaded_at        TIMESTAMP    NOT NULL,
  verified           BOOLEAN      NOT NULL DEFAULT FALSE,
  content_sha256     VARCHAR(64)                    -- lets the duplicate-artifact screen fire
);

CREATE TABLE surveyor_reports (
  report_id          VARCHAR(24)  PRIMARY KEY,
  claim_id           VARCHAR(20)  NOT NULL REFERENCES claims(claim_id),
  surveyor_id        VARCHAR(16)  NOT NULL,
  surveyor_name      VARCHAR(80),
  visited_on         DATE,
  assessed_amount    NUMERIC(14,2),
  findings           VARCHAR(400),
  photos_url         VARCHAR(300),
  recommendation     VARCHAR(16)                    -- settle|partial|repudiate|investigate
);

-- ── sales_distribution ───────────────────────────────────────────────
CREATE TABLE branches (
  branch_code        VARCHAR(12)  PRIMARY KEY,
  name               VARCHAR(60)  NOT NULL,
  city               VARCHAR(40)  NOT NULL,
  state              VARCHAR(40)  NOT NULL,
  region             VARCHAR(20)  NOT NULL,         -- west|south|north|east
  ifsc               VARCHAR(11)  NOT NULL,
  cluster_head       VARCHAR(80)
);

CREATE TABLE agents (
  agent_id           VARCHAR(16)  PRIMARY KEY,
  name               VARCHAR(80)  NOT NULL,
  branch_code        VARCHAR(12)  NOT NULL REFERENCES branches(branch_code),
  channel            VARCHAR(16)  NOT NULL,         -- branch|dsa|bancassurance|agency
  licence_no         VARCHAR(24),                   -- IRDAI licence for insurance sellers
  active             BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE leads (
  lead_id            VARCHAR(20)  PRIMARY KEY,
  name_full          VARCHAR(80)  NOT NULL,
  mobile_masked      VARCHAR(14),
  city               VARCHAR(40)  NOT NULL,
  product_interest   VARCHAR(20)  NOT NULL,         -- home_loan|personal_loan|auto_loan|motor_insurance|health_insurance
  source             VARCHAR(16)  NOT NULL,         -- walk_in|digital|referral|campaign|telecalling
  created_at         TIMESTAMP    NOT NULL,
  status             VARCHAR(12)  NOT NULL,         -- new|contacted|qualified|converted|lost
  assigned_agent_id  VARCHAR(16)  REFERENCES agents(agent_id),
  branch_code        VARCHAR(12)  REFERENCES branches(branch_code),
  expected_value     NUMERIC(14,2)
);

CREATE TABLE opportunities (
  opportunity_id     VARCHAR(20)  PRIMARY KEY,
  lead_id            VARCHAR(20)  NOT NULL REFERENCES leads(lead_id),
  product            VARCHAR(20)  NOT NULL,
  expected_value     NUMERIC(14,2) NOT NULL,
  stage              VARCHAR(16)  NOT NULL,         -- prospect|proposal|negotiation|won|lost
  probability        INTEGER      NOT NULL,
  expected_close     DATE,
  closed_on          DATE,
  booked_value       NUMERIC(14,2)                  -- what we actually sold — the dashboard's number
);
```

---

## 4. Seed volumes + needle rows

Deterministic: `random.seed(20260728)`, `Faker(["en_IN"])`, `Faker.seed(20260728)`.
**Names draw from a separate RNG stream**, so the row structure is identical
whether or not Faker is installed — otherwise the fallback path consumes global
randomness the Faker path does not, and the "deterministic" seed produces
different queue sizes per environment.

Figures below are what the seeder ACTUALLY produces (verified 2026-07-28), not
targets.

| Table | Rows | Notes |
|---|---|---|
| customers | 10,001 | 10 cities; ~32% self-employed; ~8% no bureau history |
| loan_applications | 12,001 | ~15% recent (the live queue), rest historical and decided |
| bureau_pulls | 12,001 | one per application |
| disbursements | 7,396 | disbursed applications only |
| loan_accounts | 7,397 | one per disbursement |
| repayment_schedule | 120,001 | capped at 120k; ≤24 installments per account |
| delinquencies | 1,534 | ~21% of accounts — a realistic delinquent share |
| collection_activities | 6,956 | 2-7 per delinquent account |
| policies | 9,000 | motor ~50%, health ~35%, property ~15% |
| claims | 4,002 | |
| claim_documents | 9,365 | 2-3 per claim, shaped by claim type |
| surveyor_reports | 2,500 | claims above ₹50k with a surveyor assigned |
| branches / agents | 60 / 400 | 10 cities, 4 regions |
| leads | 6,001 | |
| opportunities | 3,000 | ~1,100 `won` — feeds "what we sold" |

**Total: 211,615 rows across 16 tables.** Seeds in ~15 seconds.

Queue sizes the apps depend on (printed by the seeder on every run, so a bad
distribution is caught before a demo, not during one):

| Queue | Rows |
|---|---|
| credit queue (`new` + `under_review`) | 1,062 |
| collections, buckets 61-90 and 90+ | 427 |
| claims queue (`intimated` + `under_survey`) | 465 |
| unassigned new leads | 513 |

### Needle rows (deterministic demo paths — non-negotiable)

- **`LAN-NEEDLE-001`** — loan application, product `personal`, ₹12,00,000,
  `monthly_income_declared` ₹1,85,000, `income_proof_type='payslip'`, CIBIL 771,
  **`itr_declared_income` ₹6,20,000** — declared income looks healthy, the tax
  filing does not corroborate it. Status `under_review`. *This is the flagship
  officer-judgement case: no SOP clause enumerates that tell.*
- **`LON-NEEDLE-002`** — loan account, DPD 61, bucket `61-90`, `npa_class`
  `sma_2`, one `bounced` installment (`insufficient_funds`), one broken PTP in
  `collection_activities` (`ptp_kept=false`). The Collections priority case.
- **`CLM-NEEDLE-003`** — motor own-damage claim, ₹2,40,000 claimed, whose
  `repair_estimate` document carries a **`content_sha256` identical to a
  document on an earlier claim** — the duplicate-artifact fraud screen must
  fire on it.
- **`CLM-NEEDLE-004`** — health claim intimated **40 days** after loss date
  against a policy with `intimation_window_days=30`, no FIR — the exclusion
  path.
- **`LED-NEEDLE-005`** — lead, `expected_value` ₹45,00,000, `status='new'`,
  `assigned_agent_id IS NULL`, created 6 days ago — unassigned high-value.
- **`CUS-NEEDLE-001`** — the customer behind LAN-NEEDLE-001, `kyc_status`
  `verified`, segment `affluent`, city Pune.

---

## 5. Write actions (`kind=sql`, parameterised `:name`)

`roles_allowed_write: ["dept_admin","org_admin","super_admin"]`.

- `loan_origination` / `loan_applications` → **`record_credit_decision`**
  `UPDATE loan_applications SET status=:status, decision_reason=:decision_reason, decided_by=:decided_by, decided_at=:decided_at WHERE application_id=:application_id`
- `loan_servicing` / `collection_activities` → **`log_collection_activity`**
  `INSERT INTO collection_activities (activity_id, loan_account_no, agent_id, channel, attempted_at, outcome, ptp_date, ptp_amount, notes) VALUES (:activity_id, :loan_account_no, :agent_id, :channel, :attempted_at, :outcome, :ptp_date, :ptp_amount, :notes)`
- `insurance_claims` / `claims` → **`record_claim_decision`**
  `UPDATE claims SET status=:status, approved_amount=:approved_amount, rejection_reason=:rejection_reason, decided_by=:decided_by, decided_at=:decided_at WHERE claim_id=:claim_id`
- `insurance_claims` / `claims` → **`assign_surveyor`**
  `UPDATE claims SET surveyor_id=:surveyor_id, status='under_survey' WHERE claim_id=:claim_id`
- `sales_crm` / `leads` → **`assign_lead`**
  `UPDATE leads SET assigned_agent_id=:assigned_agent_id, branch_code=:branch_code, status='contacted' WHERE lead_id=:lead_id`

---

## 6. Document corpus (`raw/policy/*.md` → shared dept-library collection)

12 synthetic, India-realistic markdown documents. RBI/IRDAI *shaped* — no
verbatim regulatory text. Ingested with `source_id=acme_bank_policy_library`,
`dept=central_ops`, `org_id=acme-bank`.

1. `retail_credit_policy.md` — eligibility, FOIR/DBR caps, LTV per product, income-proof matrix (salaried vs self-employed)
2. `income_verification_sop.md` — payslip / bank-statement / ITR checks. **Deliberately stops short** of the declared-vs-filed-income tell, so the app can only learn it from officers
3. `kyc_aml_sop.md` — CKYC, PAN/Aadhaar handling, re-KYC triggers, PEP screening
4. `fair_practices_code.md` — disclosure, grievance, recovery-agent conduct
5. `collections_recovery_sop.md` — bucket-wise strategy, call windows, PTP handling, field-visit rules, legal escalation
6. `npa_classification_circular.md` — SMA/NPA staging, provisioning, upgrade rules
7. `motor_claim_settlement_sop.md` — intimation TAT, surveyor thresholds, salvage, cashless vs reimbursement
8. `health_claim_settlement_sop.md` — pre-auth, waiting periods, exclusions, discharge-summary requirements
9. `claims_fraud_indicators_circular.md` — reused/edited photos, mismatched identifiers, late intimation, repeat claimants
10. `grievance_redressal_policy.md` — internal ombudsman, escalation ladder, TATs
11. `sales_conduct_and_suitability.md` — mis-selling prevention, suitability, bancassurance conduct
12. `data_protection_and_customer_consent.md` — DPDP-aligned consent, retention, masking standards

Each chunk tagged `industry=bfsi`, `source_id=acme_bank_policy_library`,
`org_id=acme-bank`, `dept=central_ops`, `doc_path`, `doc_type`, `page`.

**Ingested and verified 2026-07-28** — ~7,800 words, 12 chunks in
`mcp_dept_libraries`, retrieval scoped by `org_id` (an acme-bank-filtered
search returns only acme-bank chunks). Documents of this length produce **one
chunk each** under the platform's 2048-token splitter; acme-power's corpus
behaves identically (13 docs → 13 chunks), so a whole document is the unit of
retrieval here. Citations still carry `doc_path`.

### The deliberate gap — do not "fix" it

`income_verification_sop.md` tells the officer to check that a salaried
applicant's **Form 16 is genuine** (correct year, TRACES watermark, certificate
number, employer matches) and to compute eligibility income from **payslips and
salary credits**. It never asks anyone to reconcile the *filed* income figure
against the *declared* one.

That gap is the whole point of `LAN-NEEDLE-001`. The file satisfies the SOP
completely; only judgement catches that the tax filing corroborates 28% of
declared income. If a future edit adds "compare ITR against declared income" to
any document in this corpus, the app can learn the lesson from the SOP and the
demonstration of learned judgement collapses. A corpus check enforces this.

---

## 7. The apps — 3 Decision Apps + 1 dashboard app

A dashboard is **not** a separate kind: it is `page.kind: "dashboard"` inside a
normal `kind: "app"`.

### 1. `01_loan_triage.json` — slug `acme-bank-loan-triage`
Persona: **Credit Officer**. Decides approve / refer-to-credit / reject.
- sources: `loan_origination.loan_applications`, `.customers`, `.bureau_pulls`, `ds_policy`
- pages: application queue (`status IN ('new','under_review')`), review + decision, agent chat
- write action: `record_credit_decision` behind a review gate
- `case_signature`:
  - facets — `product` (enum: home|personal|auto|lap|business), `amount_band` (band on `amount_requested`: 500000, 2500000, 10000000), `bureau_band` (band on `cibil_score`: 650, 730, 800), `income_proof` (presence on `income_proof_type`), `sourcing_channel` (enum)
  - reason codes — `income_not_corroborated`, `bureau_adverse`, `document_missing`, `policy_exclusion`, `amount_incorrect`, `data_stale_or_wrong`, `other`

### 2. `02_collections_priority.json` — slug `acme-bank-collections-priority`
Persona: **Collections Officer**. Decides who to work today, on which channel.
- sources: `loan_servicing.delinquencies`, `.loan_accounts`, `.collection_activities`, `ds_policy`
- pages: today's worklist (ranked), account detail + activity log, agent chat
- write action: `log_collection_activity`
- `case_signature`:
  - facets — `bucket` (enum), `outstanding_band` (band: 50000, 300000, 1500000), `ptp_history` (presence on last `ptp_date`), `product` (enum), `restructured` (presence)
  - reason codes — `wrong_priority`, `already_paid`, `dispute_raised`, `wrong_contact`, `legal_hold`, `hardship_case`, `other`

### 3. `03_claim_triage.json` — slug `acme-bank-claim-triage`
Persona: **Claims Officer**. Decides settle / survey / investigate. **Multimodal**
— reads `claim_documents` (documents + photos) with per-item review, so the
duplicate-artifact and metadata screens apply.
- sources: `insurance_claims.claims`, `.policies`, `.claim_documents`, `.surveyor_reports`, `ds_policy`
- pages: open-claims queue, claim detail with per-item document review, agent chat
- write actions: `record_claim_decision`, `assign_surveyor`
- `case_signature`:
  - facets — `line` (enum: motor|health|property), `claimed_amount_band` (band: 50000, 250000, 1000000), `fir` (presence on `fir_number`), `intimation_delay_band` (age_band on `intimation_delay_days`: 7, 30), plus signals `exact_duplicate`, `shared_identifier`
  - reason codes — `evidence_insufficient`, `exclusion_applies`, `amount_incorrect`, `fraud_false_positive`, `document_mismatch`, `late_intimation`, `other`

### 4. `04_sales_performance.json` — slug `acme-bank-sales`
Persona: **Branch Sales Manager / leadership**. **Dashboard pages only** — no
officer decision, no review gate, therefore **no `case_signature`** (and the
publish gate correctly will not warn).
- sources: `sales_crm.opportunities`, `.leads`, `.branches`, `.agents`
- pages:
  - `what_we_sold` (`page.kind: "dashboard"`) — booked value by product / branch / channel, month on month
  - `pipeline` (`page.kind: "dashboard"`) — open opportunities by stage, ageing
  - `leadership` (`page.kind: "dashboard"`) — region roll-up, top/bottom branches, unassigned high-value leads

**Apps 1–3 must carry `case_signature` at publish.** Without one, corrections
are stored uncoded and consolidation can only ever use them to reinforce, never
to author — the app records feedback forever and learns nothing.

---

## 8. Personas (`users.json`)

**Three officers minimum in every app-owning department** — a team judgement
needs 3 distinct officers, so with fewer the demo can only ever show "one
officer's judgement" and never the promotion.

| Persona | Dept(s) | Role |
|---|---|---|
| Credit Officer — Pune | `lending` | `user` |
| Credit Officer — Bengaluru | `lending` | `user` |
| Credit Officer — Mumbai | `lending` | `user` |
| Credit Manager — West | `lending` | `dept_admin` |
| Collections Officer — Mumbai | `collections` | `user` |
| Collections Officer — Hyderabad | `collections` | `user` |
| Collections Officer — Jaipur | `collections` | `user` |
| Collections Manager | `collections`, `lending` | `dept_admin` |
| Claims Officer — Motor | `claims` | `user` |
| Claims Officer — Health | `claims` | `user` |
| Claims Officer — Property | `claims` | `user` |
| Claims Manager | `claims` | `dept_admin` |
| Branch Sales Manager | `sales_distribution` | `dept_admin` |
| COO / Central Ops | `central_ops` + all | `org_admin` |

Personas are placeholders without passwords — reached through
**Impersonate User → Demo personas**.

---

## 9. Bring-up order (scripts/)

1. `docker compose up -d citra-ds-acme-bank-postgres` → `python scripts/seed_postgres.py`
2. `python scripts/build_mcp_sources.py` → regenerates `mcp/sources.json`.
   **That file IS the registry** — mounted read-only as the MCP's
   `SOURCES_FILE`, published to discovery on boot. There is no import step.
3. `docker compose up -d --build citra-ds-mcp-demo-acme-bank` → `:18504/health` must list 5 sources
4. `python scripts/ingest_docs.py` (Citra-Service venv; needs an embedding endpoint)
5. `python ../../scripts/seed_tenant.py --tenant acme-bank` (needs Citra-User-Service)
6. **Cut-over**: `ORG_ID=acme-bank` on data-discovery, restart, `POST /crawl/run`
   — this takes acme-power down; see `docs/acme-bank-demo-plan.md` §7.5
7. Author the 4 apps via the builder (1–3 with `case_signature`)
8. `python scripts/seed_memory.py --apply` — demo judgements, formed from evidence
9. `python scripts/acme_bank_e2e.py` — the middleware test, must pass

---

## 10. Demo scope

- **General insurance only** — motor, health, property. No life, so no
  persistency or mortality tables.
- **Sales is reporting, not deciding.** "What we sold" is a dashboard; lead
  assignment exists as a write action but is not itself a Decision App.
- **Every identifier is masked in the data itself**, not just in the UI — the
  demo must be safe on a projector.
- **One org at a time.** acme-bank replaces acme-power; it does not run
  alongside it.
