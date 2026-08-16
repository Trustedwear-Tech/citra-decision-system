-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
-- Author: Rohit Kumar Chandan
-- SPDX-License-Identifier: BUSL-1.1
--
-- Licensed under the Business Source License 1.1. Non-production use is granted;
-- production use requires a commercial licence until the Change Date, after
-- which this file converts to Apache-2.0. See LICENSE at the repository root.

-- =====================================================================
-- Acme Bank & Insurance demo tenant — Postgres DDL
-- Database: acme_bank   Schema: public
--
-- Executed verbatim by seed_postgres.py, which drops and recreates every
-- table, so a re-run produces an identical database.
--
-- Contract: demo-data/tenants/acme-bank/SPEC.md §3. Column names and enum
-- vocabularies here MUST match the facets declared in each app's
-- case_signature (SPEC.md §7) — a facet whose column does not exist emits
-- __unknown for every case, and a judgement scoped to it can never fire.
--
-- PRIVACY: identifiers are masked IN THE TABLE, not at render time.
-- pan_masked / aadhaar_last4 / mobile_masked never hold a full value, so the
-- demo is safe on a projector even if a panel is misconfigured.
-- =====================================================================

DROP TABLE IF EXISTS opportunities        CASCADE;
DROP TABLE IF EXISTS leads                CASCADE;
DROP TABLE IF EXISTS agents               CASCADE;
DROP TABLE IF EXISTS branches             CASCADE;
DROP TABLE IF EXISTS surveyor_reports     CASCADE;
DROP TABLE IF EXISTS claim_documents      CASCADE;
DROP TABLE IF EXISTS claims               CASCADE;
DROP TABLE IF EXISTS policies             CASCADE;
DROP TABLE IF EXISTS collection_activities CASCADE;
DROP TABLE IF EXISTS delinquencies        CASCADE;
DROP TABLE IF EXISTS repayment_schedule   CASCADE;
DROP TABLE IF EXISTS loan_accounts        CASCADE;
DROP TABLE IF EXISTS disbursements        CASCADE;
DROP TABLE IF EXISTS bureau_pulls         CASCADE;
DROP TABLE IF EXISTS loan_applications    CASCADE;
DROP TABLE IF EXISTS customers            CASCADE;

-- ── lending ──────────────────────────────────────────────────────────
CREATE TABLE customers (
  customer_id             VARCHAR(16)   PRIMARY KEY,
  name_full               VARCHAR(80)   NOT NULL,
  pan_masked              VARCHAR(12)   NOT NULL,
  aadhaar_last4           VARCHAR(4),
  mobile_masked           VARCHAR(14),
  email                   VARCHAR(80),
  city                    VARCHAR(40)   NOT NULL,
  state                   VARCHAR(40)   NOT NULL,
  pin                     VARCHAR(6)    NOT NULL,
  occupation              VARCHAR(24)   NOT NULL,   -- salaried|self_employed|professional|business
  employer_name           VARCHAR(80),
  monthly_income_declared NUMERIC(14,2) NOT NULL,
  existing_emi            NUMERIC(14,2) NOT NULL DEFAULT 0,
  cibil_score             INTEGER,                   -- NULL = no bureau history
  kyc_status              VARCHAR(16)   NOT NULL,    -- verified|pending|re_kyc_due
  customer_segment        VARCHAR(16)   NOT NULL,    -- mass|affluent|hni|nri
  onboarded_on            DATE          NOT NULL
);

CREATE TABLE loan_applications (
  application_id          VARCHAR(20)   PRIMARY KEY,
  customer_id             VARCHAR(16)   NOT NULL REFERENCES customers(customer_id),
  product                 VARCHAR(16)   NOT NULL,    -- home|personal|auto|lap|business
  amount_requested        NUMERIC(14,2) NOT NULL,
  tenure_months           INTEGER       NOT NULL,
  roi_offered             NUMERIC(5,2),
  applied_at              TIMESTAMP     NOT NULL,
  branch_code             VARCHAR(12)   NOT NULL,
  sourcing_channel        VARCHAR(16)   NOT NULL,    -- branch|dsa|digital|bancassurance
  income_proof_type       VARCHAR(20),               -- payslip|itr|bank_statement|NULL (missing)
  itr_declared_income     NUMERIC(14,2),             -- income per the tax filing; NULL = not produced
  ltv_percent             NUMERIC(5,2),
  foir_percent            NUMERIC(5,2),
  status                  VARCHAR(16)   NOT NULL,    -- new|under_review|approved|rejected|disbursed
  -- TEXT, not VARCHAR(n): an agent writes a paragraph of grounded
  -- reasoning here, and a 200-char cap failed the COMMIT while the
  -- dry run had passed — the officer approved a plan that could not
  -- be applied.
  decision_reason         TEXT,
  decided_by              VARCHAR(80),
  decided_at              TIMESTAMP
);

CREATE TABLE bureau_pulls (
  pull_id                 VARCHAR(20)   PRIMARY KEY,
  application_id          VARCHAR(20)   NOT NULL REFERENCES loan_applications(application_id),
  bureau                  VARCHAR(12)   NOT NULL,    -- cibil|experian|crif
  score                   INTEGER,
  enquiries_6m            INTEGER       NOT NULL DEFAULT 0,
  active_loans            INTEGER       NOT NULL DEFAULT 0,
  overdue_amount          NUMERIC(14,2) NOT NULL DEFAULT 0,
  writeoff_flag           BOOLEAN       NOT NULL DEFAULT FALSE,
  pulled_at               TIMESTAMP     NOT NULL
);

CREATE TABLE disbursements (
  disbursement_id         VARCHAR(20)   PRIMARY KEY,
  application_id          VARCHAR(20)   NOT NULL REFERENCES loan_applications(application_id),
  amount                  NUMERIC(14,2) NOT NULL,
  disbursed_at            DATE          NOT NULL,
  utr                     VARCHAR(24),
  account_masked          VARCHAR(20),
  ifsc                    VARCHAR(11)
);

-- ── collections ──────────────────────────────────────────────────────
CREATE TABLE loan_accounts (
  loan_account_no         VARCHAR(20)   PRIMARY KEY,
  customer_id             VARCHAR(16)   NOT NULL REFERENCES customers(customer_id),
  application_id          VARCHAR(20)   REFERENCES loan_applications(application_id),
  product                 VARCHAR(16)   NOT NULL,
  principal               NUMERIC(14,2) NOT NULL,
  roi                     NUMERIC(5,2)  NOT NULL,
  emi_amount              NUMERIC(14,2) NOT NULL,
  tenure_months           INTEGER       NOT NULL,
  disbursed_on            DATE          NOT NULL,
  outstanding             NUMERIC(14,2) NOT NULL,
  dpd                     INTEGER       NOT NULL DEFAULT 0,
  bucket                  VARCHAR(8)    NOT NULL,    -- 0|1-30|31-60|61-90|90+
  npa_class               VARCHAR(14)   NOT NULL,    -- standard|sma_0|sma_1|sma_2|sub_standard
  branch_code             VARCHAR(12)   NOT NULL,
  restructured            BOOLEAN       NOT NULL DEFAULT FALSE
);

CREATE TABLE repayment_schedule (
  schedule_id             VARCHAR(24)   PRIMARY KEY,
  loan_account_no         VARCHAR(20)   NOT NULL REFERENCES loan_accounts(loan_account_no),
  installment_no          INTEGER       NOT NULL,
  due_date                DATE          NOT NULL,
  emi_due                 NUMERIC(14,2) NOT NULL,
  paid_amount             NUMERIC(14,2) NOT NULL DEFAULT 0,
  paid_on                 DATE,
  status                  VARCHAR(12)   NOT NULL,    -- paid|partial|unpaid|bounced
  bounce_reason           VARCHAR(40)
);

CREATE TABLE delinquencies (
  delinquency_id          VARCHAR(24)   PRIMARY KEY,
  loan_account_no         VARCHAR(20)   NOT NULL REFERENCES loan_accounts(loan_account_no),
  as_of                   DATE          NOT NULL,
  dpd                     INTEGER       NOT NULL,
  bucket                  VARCHAR(8)    NOT NULL,
  overdue_amount          NUMERIC(14,2) NOT NULL,
  last_paid_on            DATE,
  risk_flag               VARCHAR(16)                -- skip_trace|dispute|legal|none
);

CREATE TABLE collection_activities (
  activity_id             VARCHAR(24)   PRIMARY KEY,
  loan_account_no         VARCHAR(20)   NOT NULL REFERENCES loan_accounts(loan_account_no),
  agent_id                VARCHAR(16)   NOT NULL,
  channel                 VARCHAR(12)   NOT NULL,    -- call|sms|whatsapp|field|legal
  attempted_at            TIMESTAMP     NOT NULL,
  outcome                 VARCHAR(20)   NOT NULL,    -- ptp|no_contact|dispute|refused|paid|wrong_number
  ptp_date                DATE,
  ptp_amount              NUMERIC(14,2),
  ptp_kept                BOOLEAN,
  -- TEXT, not VARCHAR(n): an agent writes a paragraph of grounded
  -- reasoning here, and a 200-char cap failed the COMMIT while the
  -- dry run had passed — the officer approved a plan that could not
  -- be applied.
  notes                   TEXT
);

-- ── claims (GENERAL insurance only: motor, health, property) ──────────
CREATE TABLE policies (
  policy_no               VARCHAR(20)   PRIMARY KEY,
  customer_id             VARCHAR(16)   NOT NULL REFERENCES customers(customer_id),
  line                    VARCHAR(10)   NOT NULL,    -- motor|health|property
  product_name            VARCHAR(60)   NOT NULL,
  sum_insured             NUMERIC(14,2) NOT NULL,
  premium_annual          NUMERIC(14,2) NOT NULL,
  start_date              DATE          NOT NULL,
  end_date                DATE          NOT NULL,
  status                  VARCHAR(12)   NOT NULL,    -- active|lapsed|cancelled|expired
  nominee_name            VARCHAR(80),
  intimation_window_days  INTEGER       NOT NULL DEFAULT 30
);

CREATE TABLE claims (
  claim_id                VARCHAR(20)   PRIMARY KEY,
  policy_no               VARCHAR(20)   NOT NULL REFERENCES policies(policy_no),
  customer_id             VARCHAR(16)   NOT NULL REFERENCES customers(customer_id),
  claim_type              VARCHAR(24)   NOT NULL,    -- own_damage|third_party|theft|hospitalisation|fire|burglary
  loss_date               DATE          NOT NULL,
  intimated_at            TIMESTAMP     NOT NULL,
  intimation_delay_days   INTEGER       NOT NULL,    -- drives the age_band facet
  claimed_amount          NUMERIC(14,2) NOT NULL,
  approved_amount         NUMERIC(14,2),
  status                  VARCHAR(16)   NOT NULL,    -- intimated|under_survey|approved|rejected|settled
  -- TEXT, not VARCHAR(n): an agent writes a paragraph of grounded
  -- reasoning here, and a 200-char cap failed the COMMIT while the
  -- dry run had passed — the officer approved a plan that could not
  -- be applied.
  rejection_reason        TEXT,
  surveyor_id             VARCHAR(16),
  fir_number              VARCHAR(30),               -- NULL = no FIR (presence facet)
  tat_days                INTEGER,
  decided_by              VARCHAR(80),
  decided_at              TIMESTAMP
);

CREATE TABLE claim_documents (
  document_id             VARCHAR(24)   PRIMARY KEY,
  claim_id                VARCHAR(20)   NOT NULL REFERENCES claims(claim_id),
  doc_type                VARCHAR(24)   NOT NULL,    -- fir|repair_estimate|invoice|discharge_summary|damage_photo|policy_copy
  -- Nullable on purpose: a document can be RECORDED without the bytes being
  -- held. Only documents on open claims are actually filed in object storage
  -- (upload_claim_documents.py); the rest keep their metadata and carry no
  -- link, because a link that looks openable and is not is worse than none.
  file_url                VARCHAR(300),
  uploaded_at             TIMESTAMP     NOT NULL,
  verified                BOOLEAN       NOT NULL DEFAULT FALSE,
  content_sha256          VARCHAR(64)                -- lets the duplicate-artifact screen fire
);

CREATE TABLE surveyor_reports (
  report_id               VARCHAR(24)   PRIMARY KEY,
  claim_id                VARCHAR(20)   NOT NULL REFERENCES claims(claim_id),
  surveyor_id             VARCHAR(16)   NOT NULL,
  surveyor_name           VARCHAR(80),
  visited_on              DATE,
  assessed_amount         NUMERIC(14,2),
  findings                VARCHAR(400),
  photos_url              VARCHAR(300),
  recommendation          VARCHAR(16)                -- settle|partial|repudiate|investigate
);

-- ── sales_distribution ───────────────────────────────────────────────
CREATE TABLE branches (
  branch_code             VARCHAR(12)   PRIMARY KEY,
  name                    VARCHAR(60)   NOT NULL,
  city                    VARCHAR(40)   NOT NULL,
  state                   VARCHAR(40)   NOT NULL,
  region                  VARCHAR(20)   NOT NULL,    -- west|south|north|east
  ifsc                    VARCHAR(11)   NOT NULL,
  cluster_head            VARCHAR(80)
);

CREATE TABLE agents (
  agent_id                VARCHAR(16)   PRIMARY KEY,
  name                    VARCHAR(80)   NOT NULL,
  branch_code             VARCHAR(12)   NOT NULL REFERENCES branches(branch_code),
  channel                 VARCHAR(16)   NOT NULL,    -- branch|dsa|bancassurance|agency
  licence_no              VARCHAR(24),
  active                  BOOLEAN       NOT NULL DEFAULT TRUE
);

CREATE TABLE leads (
  lead_id                 VARCHAR(20)   PRIMARY KEY,
  name_full               VARCHAR(80)   NOT NULL,
  mobile_masked           VARCHAR(14),
  city                    VARCHAR(40)   NOT NULL,
  product_interest        VARCHAR(20)   NOT NULL,    -- home_loan|personal_loan|auto_loan|motor_insurance|health_insurance
  source                  VARCHAR(16)   NOT NULL,    -- walk_in|digital|referral|campaign|telecalling
  created_at              TIMESTAMP     NOT NULL,
  status                  VARCHAR(12)   NOT NULL,    -- new|contacted|qualified|converted|lost
  assigned_agent_id       VARCHAR(16)   REFERENCES agents(agent_id),
  branch_code             VARCHAR(12)   REFERENCES branches(branch_code),
  expected_value          NUMERIC(14,2)
);

CREATE TABLE opportunities (
  opportunity_id          VARCHAR(20)   PRIMARY KEY,
  lead_id                 VARCHAR(20)   NOT NULL REFERENCES leads(lead_id),
  product                 VARCHAR(20)   NOT NULL,
  expected_value          NUMERIC(14,2) NOT NULL,
  stage                   VARCHAR(16)   NOT NULL,    -- prospect|proposal|negotiation|won|lost
  probability             INTEGER       NOT NULL,
  expected_close          DATE,
  closed_on               DATE,
  booked_value            NUMERIC(14,2)              -- what we actually sold
);

-- ── indexes the demo queues actually filter on ───────────────────────
CREATE INDEX idx_apps_status        ON loan_applications (status);
CREATE INDEX idx_apps_customer      ON loan_applications (customer_id);
CREATE INDEX idx_bureau_app         ON bureau_pulls (application_id);
CREATE INDEX idx_accounts_bucket    ON loan_accounts (bucket);
CREATE INDEX idx_accounts_customer  ON loan_accounts (customer_id);
CREATE INDEX idx_sched_account      ON repayment_schedule (loan_account_no);
CREATE INDEX idx_sched_status       ON repayment_schedule (status);
CREATE INDEX idx_delinq_bucket      ON delinquencies (bucket);
CREATE INDEX idx_delinq_account     ON delinquencies (loan_account_no);
CREATE INDEX idx_activity_account   ON collection_activities (loan_account_no);
CREATE INDEX idx_claims_status      ON claims (status);
CREATE INDEX idx_claims_policy      ON claims (policy_no);
CREATE INDEX idx_claimdocs_claim    ON claim_documents (claim_id);
CREATE INDEX idx_claimdocs_sha      ON claim_documents (content_sha256);
CREATE INDEX idx_policies_customer  ON policies (customer_id);
CREATE INDEX idx_leads_status       ON leads (status);
CREATE INDEX idx_opps_stage         ON opportunities (stage);
