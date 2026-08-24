# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Render the 5 Acme Bank source documents as the MCP's local SOURCES_FILE
(`mcp/sources.json`). **This script writes nothing to MongoDB.**

The generated file IS the MCP's source registry: `mcp/docker-compose.yml`
mounts it read-only at `/app/sources.json` and points `SOURCES_FILE` at it.
The MCP loads these sources at startup, publishes them to the discovery
registry, and (for structured sources) serves them on its `/query` endpoint.
The central Mongo `dept_sources` registry was retired (2026-07-10) — there is
NO import step; regenerate this file and restart the MCP.

Contract: demo-data/tenants/acme-bank/SPEC.md §2 (sources) and §5 (write
actions). Column names here MUST match schema.sql — the catalogue is built
from this file, and a column that does not exist becomes a dataset the agent
will happily try to query.

Usage:
    python build_mcp_sources.py [--out ../mcp/sources.json] [--stdout]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ORG_ID = "acme-bank"
ENV_PREFIX = "ACME_BANK_SQL"

# Display identity stamped on EVERY source so app headers, browser titles and
# agent prompts carry the company rather than a slug. Presentation only —
# org_id/tenancy is a different field and untouched.
ORGANIZATION = {
    "name": "Acme Bank & Insurance Ltd",
    "short_name": "Acme Bank",
    "brand_color": "#0F766E",
}

COMMON_VISIBILITY = {
    "roles_allowed": ["user", "dept_admin", "org_admin", "super_admin"],
    "cross_org_ids": [],
    "public_within_org": False,
}

WRITE_ROLES = ["dept_admin", "org_admin", "super_admin"]


def _shared_dept_collection() -> str:
    """The single shared dept-library collection — resolved the SAME way as
    Citra-Service's dept_library_store.shared_dept_collection(), so this row's
    rag.milvus_collection points where ingest_docs.py actually writes."""
    prefix = re.sub(r"[^a-zA-Z0-9]", "_",
                    os.getenv("SEMANTIC_COLLECTION_PREFIX", "mcp"))[:40].lower() or "mcp"
    return f"{prefix}_dept_libraries"


def _now() -> Dict[str, str]:
    """Extended-JSON date — the shape the registry consumer parses as a date."""
    return {"$date": datetime.now(timezone.utc).isoformat()}


def _col(name: str, ctype: str, desc: str, pk: bool = False) -> Dict[str, Any]:
    c: Dict[str, Any] = {"name": name, "physical_name": name, "type": ctype,
                         "description": desc}
    if pk:
        c["is_primary_key"] = True
    return c


def _actor(desc: str = "Deciding officer.") -> Dict[str, Any]:
    """A write-action param the SERVER binds from the verified caller identity.

    Declared as an ordinary string, the agent fills it — and it filled it with
    "Credit Officer Assistant", which is the name of no one. ``x-citra-fill:
    actor`` makes the MCP overwrite whatever the payload carries with the
    authenticated caller at the point of write, so the who on a credit file is
    unforgeable rather than suggested."""
    return {"type": "string", "description": desc, "x-citra-fill": "actor"}


def _decided_at(desc: str = "Set by the server at the moment of the write.") -> Dict[str, Any]:
    """The WHEN of a decision, bound to the server clock — never the model's.

    Observed on prod before this existed: a decision applied 2026-08-07
    committed decided_at = 2026-07-17, a plausible timestamp the model invented
    three weeks early. The agent PROPOSES long before an officer approves, so
    any time it could name is wrong even when it is honest.

    Only for the decision's own timestamp. A field like ``attempted_at`` — when
    an officer says they tried to reach a customer — is a real-world event the
    officer REPORTS, not the write time, and must stay theirs to state."""
    return {"type": "string", "description": desc, "x-citra-fill": "now"}


def _dataset(source_id: str, table: str, name: str, desc: str,
             columns: List[Dict[str, Any]],
             write_actions: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    ds: Dict[str, Any] = {
        "id": f"{source_id}.{table}",
        "physical_name": table,
        "name": name,
        "kind": "sql",
        "description": desc,
        "read_via": {"kind": "sql", "target": table},
        "columns": columns,
    }
    if write_actions:
        ds["write_actions"] = write_actions
    return ds


#: The platform's domain ontology is a CLOSED enum (source-mcp-template/
#: registry_models.py) and the MCP refuses to boot on anything outside it —
#: which is how this caught an invented "bfsi" vertical before it ever reached
#: a demo. vertical → allowed sub_verticals:
#:     banking       → loan_origination | loan_recovery
#:     insurance     → claims | underwriting
#:     utility       → power_recovery | metering_inspection
#:     field_service → equipment_inspection
#: Country is ISO-3166 alpha-2 from {IN, US}; it gates the locale pack
#: (ID checksums, date order, currency). These three are also emitted as
#: automatic facets on every case, so they are part of the memory contract.
VERTICALS = {
    "banking": {"loan_origination", "loan_recovery"},
    "insurance": {"claims", "underwriting"},
}


def _domain(vertical: str, sub_vertical: str) -> Dict[str, str]:
    allowed = VERTICALS.get(vertical, set())
    if sub_vertical not in allowed:
        raise ValueError(
            f"domain: sub_vertical {sub_vertical!r} does not belong to vertical "
            f"{vertical!r} (allowed: {sorted(allowed)})")
    return {"vertical": vertical, "sub_vertical": sub_vertical, "country": "IN"}


def _structured(source_id: str, dept_id: str, name: str, desc: str,
                tags: List[str], domain: Dict[str, str],
                datasets: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "source_id": source_id,
        "dept_id": dept_id,
        "org_id": ORG_ID,
        "type": "structured",
        "is_active": True,
        "is_demo": True,
        "name": name,
        "description": desc,
        "tags": tags,
        "organization": ORGANIZATION,
        "domain": domain,
        "visibility": dict(COMMON_VISIBILITY),
        "connection": {"type": "postgres", "env_prefix": ENV_PREFIX},
        "datasets": datasets,
        "query_timeout_seconds": 30,
        "created_at": _now(),
    }


# ── the four structured sources ──────────────────────────────────────────────
def loan_origination() -> Dict[str, Any]:
    sid = "loan_origination"
    customers = _dataset(
        sid, "customers", "Customer master",
        "Retail borrowers across 10 Indian cities. Declared income, existing "
        "EMI obligations, bureau score and KYC state. Identifiers are masked "
        "at rest (pan_masked, aadhaar_last4, mobile_masked) — no full PAN, "
        "Aadhaar or mobile number exists in this table.",
        [
            _col("customer_id", "string", "Customer id — primary key.", pk=True),
            _col("name_full", "string", "Customer name."),
            _col("pan_masked", "string", "PAN with the identifying pair masked (ABCXX1234F)."),
            _col("aadhaar_last4", "string", "Last four digits of Aadhaar only."),
            _col("mobile_masked", "string", "Mobile with only the last four digits (XXXXXX1234)."),
            _col("email", "string", "Contact email."),
            _col("city", "string", "City of residence."),
            _col("state", "string", "State."),
            _col("pin", "string", "PIN code."),
            _col("occupation", "string", "salaried | self_employed | professional | business."),
            _col("employer_name", "string", "Employer, or 'Self' for non-salaried."),
            _col("monthly_income_declared", "number", "Monthly income as DECLARED by the customer, INR."),
            _col("existing_emi", "number", "Existing monthly EMI obligations, INR."),
            _col("cibil_score", "number", "Bureau score 300-900. NULL means new-to-credit."),
            _col("kyc_status", "string", "verified | pending | re_kyc_due."),
            _col("customer_segment", "string", "mass | affluent | hni | nri."),
            _col("onboarded_on", "date", "Date the customer relationship began."),
        ])
    applications = _dataset(
        sid, "loan_applications", "Loan applications",
        "Applications across home, personal, auto, LAP and business loans. "
        "Rows with status new/under_review are the live credit queue. Carries "
        "BOTH monthly_income_declared (on the customer) and itr_declared_income "
        "(income per the tax filing) — the two disagreeing is a real-world "
        "underwriting tell that no checklist enumerates.",
        [
            _col("application_id", "string", "Application id — primary key.", pk=True),
            _col("customer_id", "string", "Applicant → customers.customer_id."),
            _col("product", "string", "home | personal | auto | lap | business."),
            _col("amount_requested", "number", "Requested loan amount, INR."),
            _col("tenure_months", "number", "Requested tenure in months."),
            _col("roi_offered", "number", "Offered rate of interest, % per annum."),
            _col("applied_at", "datetime", "When the application was submitted."),
            _col("branch_code", "string", "Originating branch."),
            _col("sourcing_channel", "string", "branch | dsa | digital | bancassurance."),
            _col("income_proof_type", "string", "payslip | itr | bank_statement. NULL means no proof on file."),
            _col("itr_declared_income", "number",
                 "Annual income as per the TAX FILING, INR. NULL when no filing was produced. "
                 "Compare against monthly_income_declared × 12 — a large shortfall is a "
                 "corroboration failure, not necessarily a rejection."),
            _col("ltv_percent", "number", "Loan-to-value %, secured products only."),
            _col("foir_percent", "number", "Fixed-obligation-to-income ratio %."),
            _col("status", "string", "new | under_review | approved | rejected | disbursed."),
            _col("decision_reason", "string", "Free-text reason recorded with the decision."),
            _col("decided_by", "string", "Officer who decided."),
            _col("decided_at", "datetime", "When the decision was recorded."),
        ],
        write_actions=[{
            "id": "record_credit_decision",
            "verb": "update",
            "description": "Record the credit decision on an application, with the reason and the deciding officer.",
            "sql_template": ("UPDATE loan_applications SET status=:status, "
                             "decision_reason=:decision_reason, decided_by=:decided_by, "
                             "decided_at=:decided_at WHERE application_id=:application_id"),
            "key_fields": ["application_id"],
            "roles_allowed_write": WRITE_ROLES,
            "input_schema": {
                "type": "object",
                "required": ["application_id", "status"],
                "properties": {
                    "application_id": {"type": "string", "description": "Target application."},
                    "status": {"type": "string", "description": "approved | rejected | under_review."},
                    "decision_reason": {"type": "string", "description": "Why — shown to the customer and the auditor."},
                    "decided_by": _actor(),
                    "decided_at": _decided_at(),
                },
            },
        }])
    bureau = _dataset(
        sid, "bureau_pulls", "Credit bureau pulls",
        "One bureau pull per application (CIBIL / Experian / CRIF): score, "
        "recent enquiries, active loans, overdue amount and write-off flag.",
        [
            _col("pull_id", "string", "Pull id — primary key.", pk=True),
            _col("application_id", "string", "→ loan_applications.application_id."),
            _col("bureau", "string", "cibil | experian | crif."),
            _col("score", "number", "Bureau score at pull time."),
            _col("enquiries_6m", "number", "Enquiries in the last 6 months — credit hunger."),
            _col("active_loans", "number", "Live loans across lenders."),
            _col("overdue_amount", "number", "Currently overdue across lenders, INR."),
            _col("writeoff_flag", "boolean", "Any written-off account on record."),
            _col("pulled_at", "datetime", "When the bureau was pulled."),
        ])
    disbursements = _dataset(
        sid, "disbursements", "Disbursements",
        "Money actually released against approved applications, with UTR and "
        "masked destination account.",
        [
            _col("disbursement_id", "string", "Disbursement id — primary key.", pk=True),
            _col("application_id", "string", "→ loan_applications.application_id."),
            _col("amount", "number", "Amount disbursed, INR."),
            _col("disbursed_at", "date", "Disbursement date."),
            _col("utr", "string", "Unique transaction reference."),
            _col("account_masked", "string", "Destination account, last four digits only."),
            _col("ifsc", "string", "Destination IFSC."),
        ])
    return _structured(
        sid, "lending", "Lending — Origination & Underwriting",
        "Loan origination system: customer master, applications, bureau pulls "
        "and disbursements. Drives the Loan Application Triage Decision App.",
        ["lending", "credit", "underwriting", "origination", "bureau"],
        _domain("banking", "loan_origination"),
        [customers, applications, bureau, disbursements])


def loan_servicing() -> Dict[str, Any]:
    sid = "loan_servicing"
    accounts = _dataset(
        sid, "loan_accounts", "Loan accounts",
        "Live loan book with DPD, bucket and NPA staging. Bucket and npa_class "
        "are the fields collections strategy keys on.",
        [
            _col("loan_account_no", "string", "Loan account number — primary key.", pk=True),
            _col("customer_id", "string", "→ customers.customer_id."),
            _col("application_id", "string", "Originating application, when known."),
            _col("product", "string", "home | personal | auto | lap | business."),
            _col("principal", "number", "Sanctioned principal, INR."),
            _col("roi", "number", "Rate of interest, % per annum."),
            _col("emi_amount", "number", "Monthly instalment, INR."),
            _col("tenure_months", "number", "Tenure in months."),
            _col("disbursed_on", "date", "Disbursement date."),
            _col("outstanding", "number", "Principal outstanding, INR."),
            _col("dpd", "number", "Days past due."),
            _col("bucket", "string", "0 | 1-30 | 31-60 | 61-90 | 90+."),
            _col("npa_class", "string", "standard | sma_0 | sma_1 | sma_2 | sub_standard."),
            _col("branch_code", "string", "Servicing branch."),
            _col("restructured", "boolean", "Whether the loan has been restructured."),
        ])
    schedule = _dataset(
        sid, "repayment_schedule", "Repayment schedule",
        "Instalment-level repayment history. status=bounced carries the NACH "
        "return reason, which is what separates 'cannot pay' from 'did not pay'.",
        [
            _col("schedule_id", "string", "Schedule row id — primary key.", pk=True),
            _col("loan_account_no", "string", "→ loan_accounts.loan_account_no."),
            _col("installment_no", "number", "Instalment sequence number."),
            _col("due_date", "date", "Instalment due date."),
            _col("emi_due", "number", "Amount due, INR."),
            _col("paid_amount", "number", "Amount actually received, INR."),
            _col("paid_on", "date", "Receipt date, when paid."),
            _col("status", "string", "paid | partial | unpaid | bounced."),
            _col("bounce_reason", "string", "NACH return reason, e.g. insufficient_funds."),
        ])
    delinquencies = _dataset(
        sid, "delinquencies", "Delinquency snapshot",
        "Current delinquency position per account — the collections worklist. "
        "risk_flag marks accounts under dispute, legal hold or skip-trace.",
        [
            _col("delinquency_id", "string", "Row id — primary key.", pk=True),
            _col("loan_account_no", "string", "→ loan_accounts.loan_account_no."),
            _col("as_of", "date", "Snapshot date."),
            _col("dpd", "number", "Days past due."),
            _col("bucket", "string", "0 | 1-30 | 31-60 | 61-90 | 90+."),
            _col("overdue_amount", "number", "Total overdue, INR."),
            _col("last_paid_on", "date", "Date of the last receipt."),
            _col("risk_flag", "string", "none | skip_trace | dispute | legal."),
        ])
    activities = _dataset(
        sid, "collection_activities", "Collection activities",
        "Every contact attempt and its outcome. A promise-to-pay with "
        "ptp_kept=false is a broken promise — materially different from a "
        "first-time PTP and the strongest signal in this table.",
        [
            _col("activity_id", "string", "Activity id — primary key.", pk=True),
            _col("loan_account_no", "string", "→ loan_accounts.loan_account_no."),
            _col("agent_id", "string", "Collections agent."),
            _col("channel", "string", "call | sms | whatsapp | field | legal."),
            _col("attempted_at", "datetime", "When contact was attempted."),
            _col("outcome", "string", "ptp | no_contact | dispute | refused | paid | wrong_number."),
            _col("ptp_date", "date", "Promised payment date, when a PTP was taken."),
            _col("ptp_amount", "number", "Promised amount, INR."),
            _col("ptp_kept", "boolean", "Whether the promise was honoured. false = broken promise."),
            _col("notes", "string", "Officer note."),
        ],
        write_actions=[{
            "id": "log_collection_activity",
            "verb": "insert",
            "description": "Log a contact attempt and its outcome against a loan account.",
            "sql_template": ("INSERT INTO collection_activities (activity_id, loan_account_no, "
                             "agent_id, channel, attempted_at, outcome, ptp_date, ptp_amount, notes) "
                             "VALUES (:activity_id, :loan_account_no, :agent_id, :channel, "
                             ":attempted_at, :outcome, :ptp_date, :ptp_amount, :notes)"),
            "key_fields": ["activity_id"],
            "roles_allowed_write": WRITE_ROLES,
            "input_schema": {
                "type": "object",
                "required": ["activity_id", "loan_account_no", "agent_id", "channel",
                             "attempted_at", "outcome"],
                "properties": {
                    "activity_id": {"type": "string", "description": "New activity id."},
                    "loan_account_no": {"type": "string", "description": "Target loan account."},
                    "agent_id": {"type": "string", "description": "Collections agent."},
                    "channel": {"type": "string", "description": "call | sms | whatsapp | field | legal."},
                    "attempted_at": {"type": "string", "description": "ISO timestamp of the attempt."},
                    "outcome": {"type": "string", "description": "ptp | no_contact | dispute | refused | paid | wrong_number."},
                    "ptp_date": {"type": "string", "description": "Promised date, when outcome=ptp."},
                    "ptp_amount": {"type": "number", "description": "Promised amount, INR."},
                    "notes": {"type": "string", "description": "Officer note."},
                },
            },
        }])
    return _structured(
        sid, "collections", "Collections & Recovery",
        "Loan servicing and collections: the live book with DPD and NPA "
        "staging, instalment history, the delinquency worklist and every "
        "contact attempt. Drives the Collections Prioritisation Decision App.",
        ["collections", "recovery", "dpd", "npa", "servicing"],
        _domain("banking", "loan_recovery"),
        [accounts, schedule, delinquencies, activities])


def insurance_claims() -> Dict[str, Any]:
    sid = "insurance_claims"
    policies = _dataset(
        sid, "policies", "Policies (general insurance)",
        "Motor, health and property policies. intimation_window_days is the "
        "contractual deadline for reporting a loss — a claim intimated past it "
        "is an exclusion path, not merely a late claim.",
        [
            _col("policy_no", "string", "Policy number — primary key.", pk=True),
            _col("customer_id", "string", "→ customers.customer_id."),
            _col("line", "string", "motor | health | property."),
            _col("product_name", "string", "Marketed product name."),
            _col("sum_insured", "number", "Sum insured, INR."),
            _col("premium_annual", "number", "Annual premium, INR."),
            _col("start_date", "date", "Cover start."),
            _col("end_date", "date", "Cover end."),
            _col("status", "string", "active | lapsed | cancelled | expired."),
            _col("nominee_name", "string", "Nominee."),
            _col("intimation_window_days", "number", "Days within which a loss must be intimated."),
        ])
    claims = _dataset(
        sid, "claims", "Claims",
        "Claims across motor, health and property. Rows with status "
        "intimated/under_survey are the live triage queue. "
        "intimation_delay_days is loss-to-intimation in days — compare it "
        "against the policy's intimation_window_days.",
        [
            _col("claim_id", "string", "Claim id — primary key.", pk=True),
            _col("policy_no", "string", "→ policies.policy_no."),
            _col("customer_id", "string", "→ customers.customer_id."),
            _col("claim_type", "string", "own_damage | third_party | theft | hospitalisation | fire | burglary."),
            _col("loss_date", "date", "Date of loss."),
            _col("intimated_at", "datetime", "When the claim was intimated."),
            _col("intimation_delay_days", "number", "Days between loss and intimation."),
            _col("claimed_amount", "number", "Amount claimed, INR."),
            _col("approved_amount", "number", "Amount approved, INR."),
            _col("status", "string", "intimated | under_survey | approved | rejected | settled."),
            _col("rejection_reason", "string", "Reason recorded on repudiation."),
            _col("surveyor_id", "string", "Assigned surveyor."),
            _col("fir_number", "string", "FIR reference. NULL means no FIR on file."),
            _col("tat_days", "number", "Turnaround time in days, once decided."),
            _col("decided_by", "string", "Officer who decided."),
            _col("decided_at", "datetime", "When the decision was recorded."),
        ],
        write_actions=[
            {
                "id": "record_claim_decision",
                "verb": "update",
                "description": "Record the claim decision with the approved amount or the repudiation reason.",
                "sql_template": ("UPDATE claims SET status=:status, approved_amount=:approved_amount, "
                                 "rejection_reason=:rejection_reason, decided_by=:decided_by, "
                                 "decided_at=:decided_at WHERE claim_id=:claim_id"),
                "key_fields": ["claim_id"],
                "roles_allowed_write": WRITE_ROLES,
                "input_schema": {
                    "type": "object",
                    "required": ["claim_id", "status"],
                    "properties": {
                        "claim_id": {"type": "string", "description": "Target claim."},
                        "status": {"type": "string", "description": "approved | rejected | settled."},
                        "approved_amount": {"type": "number", "description": "Approved amount, INR."},
                        "rejection_reason": {"type": "string", "description": "Reason, when repudiating."},
                        "decided_by": _actor(),
                        "decided_at": _decided_at(),
                    },
                },
            },
            {
                "id": "assign_surveyor",
                "verb": "update",
                "description": "Assign a surveyor and move the claim into survey.",
                "sql_template": ("UPDATE claims SET surveyor_id=:surveyor_id, status='under_survey' "
                                 "WHERE claim_id=:claim_id"),
                "key_fields": ["claim_id"],
                "roles_allowed_write": WRITE_ROLES,
                "input_schema": {
                    "type": "object",
                    "required": ["claim_id", "surveyor_id"],
                    "properties": {
                        "claim_id": {"type": "string", "description": "Target claim."},
                        "surveyor_id": {"type": "string", "description": "Surveyor to assign."},
                    },
                },
            },
        ])
    documents = _dataset(
        sid, "claim_documents", "Claim documents",
        "Documents and photos filed against a claim. content_sha256 is the "
        "content hash — two claims sharing one is the same file submitted "
        "twice, which the platform's duplicate-artifact screen flags.",
        [
            _col("document_id", "string", "Document id — primary key.", pk=True),
            _col("claim_id", "string", "→ claims.claim_id."),
            _col("doc_type", "string", "fir | repair_estimate | invoice | discharge_summary | damage_photo | policy_copy."),
            _col("file_url", "string", "Stored object location."),
            _col("uploaded_at", "datetime", "Upload timestamp."),
            _col("verified", "boolean", "Whether the document has been verified."),
            _col("content_sha256", "string", "Content hash — equal hashes mean identical bytes."),
        ])
    surveys = _dataset(
        sid, "surveyor_reports", "Surveyor reports",
        "Independent surveyor assessment: what they found, what they assessed, "
        "and what they recommend.",
        [
            _col("report_id", "string", "Report id — primary key.", pk=True),
            _col("claim_id", "string", "→ claims.claim_id."),
            _col("surveyor_id", "string", "Surveyor."),
            _col("surveyor_name", "string", "Surveyor name."),
            _col("visited_on", "date", "Site visit date."),
            _col("assessed_amount", "number", "Assessed loss, INR."),
            _col("findings", "string", "Narrative findings."),
            _col("photos_url", "string", "Survey photographs."),
            _col("recommendation", "string", "settle | partial | repudiate | investigate."),
        ])
    return _structured(
        sid, "claims", "General Insurance — Claims",
        "Motor, health and property claims with their policies, filed "
        "documents and surveyor reports. Drives the Insurance Claim Triage "
        "Decision App, including per-document and per-photo review.",
        ["insurance", "claims", "motor", "health", "property", "survey"],
        _domain("insurance", "claims"),
        [policies, claims, documents, surveys])


def sales_crm() -> Dict[str, Any]:
    sid = "sales_crm"
    branches = _dataset(
        sid, "branches", "Branches",
        "Branch network across 10 cities and 4 regions.",
        [
            _col("branch_code", "string", "Branch code — primary key.", pk=True),
            _col("name", "string", "Branch name."),
            _col("city", "string", "City."),
            _col("state", "string", "State."),
            _col("region", "string", "west | south | north | east."),
            _col("ifsc", "string", "Branch IFSC."),
            _col("cluster_head", "string", "Cluster head."),
        ])
    agents = _dataset(
        sid, "agents", "Agents",
        "Sellers across branch, DSA, bancassurance and agency channels. "
        "Insurance-selling channels carry an IRDAI licence number.",
        [
            _col("agent_id", "string", "Agent id — primary key.", pk=True),
            _col("name", "string", "Agent name."),
            _col("branch_code", "string", "→ branches.branch_code."),
            _col("channel", "string", "branch | dsa | bancassurance | agency."),
            _col("licence_no", "string", "IRDAI licence, for insurance sellers."),
            _col("active", "boolean", "Whether the agent is active."),
        ])
    leads = _dataset(
        sid, "leads", "Leads",
        "Inbound and sourced leads. status='new' with no assigned agent is an "
        "unworked lead — the ageing ones are what the sales dashboard surfaces.",
        [
            _col("lead_id", "string", "Lead id — primary key.", pk=True),
            _col("name_full", "string", "Prospect name."),
            _col("mobile_masked", "string", "Mobile, last four digits only."),
            _col("city", "string", "City."),
            _col("product_interest", "string", "home_loan | personal_loan | auto_loan | motor_insurance | health_insurance."),
            _col("source", "string", "walk_in | digital | referral | campaign | telecalling."),
            _col("created_at", "datetime", "When the lead arrived."),
            _col("status", "string", "new | contacted | qualified | converted | lost."),
            _col("assigned_agent_id", "string", "Owning agent. NULL means unassigned."),
            _col("branch_code", "string", "Owning branch."),
            _col("expected_value", "number", "Expected business value, INR."),
        ],
        write_actions=[{
            "id": "assign_lead",
            "verb": "update",
            "description": "Assign a lead to an agent and branch, moving it out of the unworked queue.",
            "sql_template": ("UPDATE leads SET assigned_agent_id=:assigned_agent_id, "
                             "branch_code=:branch_code, status='contacted' WHERE lead_id=:lead_id"),
            "key_fields": ["lead_id"],
            "roles_allowed_write": WRITE_ROLES,
            "input_schema": {
                "type": "object",
                "required": ["lead_id", "assigned_agent_id"],
                "properties": {
                    "lead_id": {"type": "string", "description": "Target lead."},
                    "assigned_agent_id": {"type": "string", "description": "Agent to assign."},
                    "branch_code": {"type": "string", "description": "Owning branch."},
                },
            },
        }])
    opportunities = _dataset(
        sid, "opportunities", "Opportunities",
        "Pipeline and closed business. booked_value on stage='won' is what we "
        "actually sold — the number the sales dashboard reports.",
        [
            _col("opportunity_id", "string", "Opportunity id — primary key.", pk=True),
            _col("lead_id", "string", "→ leads.lead_id."),
            _col("product", "string", "Product sold or proposed."),
            _col("expected_value", "number", "Expected value, INR."),
            _col("stage", "string", "prospect | proposal | negotiation | won | lost."),
            _col("probability", "number", "Win probability %."),
            _col("expected_close", "date", "Expected close date."),
            _col("closed_on", "date", "Actual close date."),
            _col("booked_value", "number", "Value actually booked on a win, INR."),
        ])
    return _structured(
        sid, "sales_distribution", "Sales & Distribution",
        "Branch and agency network, lead pipeline and booked business. Backs "
        "the Sales & Distribution dashboard pages — what we sold, by product, "
        "branch and channel.",
        ["sales", "distribution", "crm", "leads", "pipeline"],
        # Sales is the front of loan origination: leads become applications.
        _domain("banking", "loan_origination"),
        [branches, agents, leads, opportunities])


# ── the semantic source ──────────────────────────────────────────────────────
def policy_library() -> Dict[str, Any]:
    return {
        "source_id": "acme_bank_policy_library",
        "dept_id": "central_ops",
        "org_id": ORG_ID,
        "type": "semantic",
        "is_active": True,
        "is_demo": True,
        "name": "Central Ops — Acme Bank Policy Library",
        "description": (
            "Cross-department RAG corpus of Acme Bank's credit policy, KYC/AML "
            "and fair-practices SOPs, collections and NPA circulars, motor and "
            "health claim settlement SOPs, fraud indicators, grievance "
            "redressal, sales conduct and data-protection standards (12 docs). "
            "Shared by every Decision App as the grounding knowledge source — "
            "every cited answer carries a doc_path + page reference. This is "
            "the RULES layer: the SOP is supreme, and anything the app learns "
            "from officers sits underneath it."
        ),
        "tags": ["central", "policy", "sop", "circular", "compliance", "rag"],
        "organization": ORGANIZATION,
        "visibility": {
            "roles_allowed": ["user", "dept_admin", "org_admin", "super_admin"],
            "cross_org_ids": [],
            # Every app grounds on the policy library.
            "public_within_org": True,
        },
        "rag": {
            "milvus_collection": _shared_dept_collection(),
            "s3_prefix": "bfsi/acme-bank/policy/",
        },
        "taxonomy": {
            "doc_types": [
                {"id": "policy", "label": "Policy"},
                {"id": "sop", "label": "SOP"},
                {"id": "circular", "label": "Circular"},
                {"id": "code", "label": "Code of Conduct"},
                {"id": "guideline", "label": "Guideline"},
            ],
            "classification_levels": ["public", "internal"],
        },
        "query_timeout_seconds": 30,
        "created_at": _now(),
    }


def acme_bank_sources() -> List[Dict[str, Any]]:
    """5 sources — 4 structured (Postgres) + 1 semantic (Milvus RAG)."""
    return [loan_origination(), loan_servicing(), insurance_claims(),
            sales_crm(), policy_library()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent
                                         / "mcp" / "sources.json"))
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = ap.parse_args()

    sources = acme_bank_sources()
    payload = json.dumps(sources, indent=2, ensure_ascii=False)

    if args.stdout:
        print(payload)
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload + "\n", encoding="utf-8")

    for s in sources:
        n_ds = len(s.get("datasets") or [])
        n_wa = sum(len(d.get("write_actions") or []) for d in (s.get("datasets") or []))
        log.info("  %-26s %-10s dept=%-18s datasets=%d write_actions=%d",
                 s["source_id"], s["type"], s["dept_id"], n_ds, n_wa)
    log.info("Wrote %d sources -> %s", len(sources), out)
    log.info("This file IS the MCP's source registry (SOURCES_FILE). No import step — "
             "restart the MCP to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
