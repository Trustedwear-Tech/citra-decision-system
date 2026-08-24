# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Seed the `acme_bank` Postgres database with FK-consistent synthetic data for the
Acme Bank & Insurance demo tenant.

Architecture:
    One Postgres database backs all 4 structured sources declared in SPEC.md.
    Sixteen tables across four departments:

        lending      : customers ← loan_applications ← bureau_pulls
                                                     ← disbursements
        collections  : loan_accounts ← repayment_schedule
                                     ← delinquencies
                                     ← collection_activities
        claims       : policies ← claims ← claim_documents
                                         ← surveyor_reports
        sales        : branches ← agents ← leads ← opportunities

    Foreign-key consistency (parents generated before children):
        loan_applications.customer_id  → customers
        bureau_pulls.application_id    → loan_applications
        disbursements.application_id   → loan_applications  (approved only)
        loan_accounts.application_id   → loan_applications  (disbursed only)
        repayment_schedule / delinquencies / collection_activities
                                       → loan_accounts
        policies.customer_id           → customers
        claims.policy_no               → policies
        claim_documents / surveyor_reports → claims
        agents.branch_code             → branches
        leads.assigned_agent_id        → agents
        opportunities.lead_id          → leads

    DDL lives in the sibling `schema.sql`. This script executes it verbatim —
    it drops and recreates every table, so a re-run produces an identical
    database (idempotent by construction).

Determinism:
    random.seed(20260728) + Faker(["en_IN"]) + Faker.seed(20260728). Dates are
    anchored to datetime.now() so the demo always looks current, while row
    contents are otherwise fully reproducible.

Demo design — the queues are sized deliberately:
        loan_applications  ~1,200 rows status in (new, under_review)  → credit queue
        delinquencies      ~3,000 rows, ~600 in buckets 61-90 / 90+   → collections queue
        claims             ~500  rows status in (intimated, under_survey) → claims queue
        leads              ~900  rows status='new'                    → sales pipeline
    Plus the 6 explicit needle rows (SPEC.md §4) that make the scripted demo
    paths deterministic — above all LAN-NEEDLE-001, where declared income looks
    healthy and the tax filing does not corroborate it.

Privacy:
    Identifiers are masked IN THE TABLE, never at render time — pan_masked
    (ABCXX1234F), aadhaar_last4, mobile_masked (XXXXXX1234). No full PAN,
    Aadhaar or mobile number is ever generated, so the demo is safe on a
    projector even if a panel is misconfigured.

Usage:
    Needs psycopg2 (and optionally faker). Citra-Service's venv has both:
        C:/Github/Citra-AI/Citra-Service/myenv/Scripts/python.exe seed_postgres.py

    python seed_postgres.py [--conn <pg-uri>] [--dry-run]
    Default connection: postgresql://acme_bank:acme_bank_demo_pw@localhost:5444/acme_bank
    Override with env ACME_BANK_PG_CONN or --conn.
    --dry-run generates in memory and writes nothing.

Bring the database up first:
    cd ../mcp && docker compose up -d acme-bank-postgres
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed-acme-bank")

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_SQL = SCRIPT_DIR / "schema.sql"

SEED = 20260728
DEFAULT_CONN = os.environ.get(
    "ACME_BANK_PG_CONN",
    "postgresql://acme_bank:acme_bank_demo_pw@localhost:5444/acme_bank",
)

random.seed(SEED)

#: Names draw from their OWN stream, never the global one. Otherwise the
#: fallback path (faker missing) consumes global randomness that the faker path
#: does not, and the same script produces STRUCTURALLY different data depending
#: on whether an optional dependency happens to be installed — row counts,
#: statuses and queue sizes all shift. Isolating the stream keeps every
#: structural decision identical in both environments; only the name strings
#: differ. Found the hard way: 1082 vs 1062 queue rows across two venvs.
_names = random.Random(SEED ^ 0x9E3779B9)

try:
    from faker import Faker

    fake = Faker(["en_IN"])
    Faker.seed(SEED)          # Faker keeps its own RNG — also off the global stream
except ImportError:  # pragma: no cover — names degrade, structure does not
    fake = None
    log.warning("faker not installed — using the built-in name lists "
                "(row structure is unaffected)")

# ── reference data ───────────────────────────────────────────────────────────
# (city, state, pin-prefix, region)
CITIES = [
    ("Mumbai",    "Maharashtra",   "400", "west"),
    ("Pune",      "Maharashtra",   "411", "west"),
    ("Ahmedabad", "Gujarat",       "380", "west"),
    ("Bengaluru", "Karnataka",     "560", "south"),
    ("Hyderabad", "Telangana",     "500", "south"),
    ("Chennai",   "Tamil Nadu",    "600", "south"),
    ("Kochi",     "Kerala",        "682", "south"),
    ("Jaipur",    "Rajasthan",     "302", "north"),
    ("Lucknow",   "Uttar Pradesh", "226", "north"),
    ("Indore",    "Madhya Pradesh", "452", "west"),
]

FIRST_NAMES = ["Aarav", "Ananya", "Rahul", "Priya", "Vikram", "Meera", "Arjun",
               "Sneha", "Imran", "Devika", "Sameer", "Lakshmi", "Mohit",
               "Farida", "Nikhil", "Kavya", "Rohan", "Ishita", "Karthik", "Neha"]
LAST_NAMES = ["Sharma", "Iyer", "Deshpande", "Reddy", "Patel", "Nair", "Malhotra",
              "Pillai", "Qureshi", "Rao", "Kulkarni", "Agarwal", "Contractor",
              "Bhatia", "Krishnan", "Menon", "Joshi", "Banerjee", "Chauhan", "Sethi"]

OCCUPATIONS = ["salaried", "self_employed", "professional", "business"]
OCCUPATION_W = [0.55, 0.22, 0.13, 0.10]
SEGMENTS = ["mass", "affluent", "hni", "nri"]
SEGMENT_W = [0.62, 0.26, 0.08, 0.04]
KYC = ["verified", "pending", "re_kyc_due"]
KYC_W = [0.86, 0.08, 0.06]

PRODUCTS = ["home", "personal", "auto", "lap", "business"]
PRODUCT_W = [0.28, 0.34, 0.20, 0.10, 0.08]
CHANNELS = ["branch", "dsa", "digital", "bancassurance"]
CHANNEL_W = [0.42, 0.24, 0.28, 0.06]
PROOFS = ["payslip", "itr", "bank_statement", None]
PROOF_W = [0.52, 0.24, 0.18, 0.06]

BUCKETS = ["0", "1-30", "31-60", "61-90", "90+"]
NPA_BY_BUCKET = {"0": "standard", "1-30": "sma_0", "31-60": "sma_1",
                 "61-90": "sma_2", "90+": "sub_standard"}
BOUNCE_REASONS = ["insufficient_funds", "account_closed", "mandate_cancelled",
                  "signature_mismatch", "technical_decline"]

LINES = ["motor", "health", "property"]
LINE_W = [0.50, 0.35, 0.15]
CLAIM_TYPES = {
    "motor": ["own_damage", "third_party", "theft"],
    "health": ["hospitalisation"],
    "property": ["fire", "burglary"],
}
PRODUCT_NAMES = {
    "motor": ["Acme Drive Secure", "Acme Motor Shield"],
    "health": ["Acme Health Advantage", "Acme Family Care"],
    "property": ["Acme Home Shield", "Acme Business Protect"],
}
LEAD_INTERESTS = ["home_loan", "personal_loan", "auto_loan",
                  "motor_insurance", "health_insurance"]
LEAD_SOURCES = ["walk_in", "digital", "referral", "campaign", "telecalling"]
AGENT_CHANNELS = ["branch", "dsa", "bancassurance", "agency"]


# ── helpers ──────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _days_ago(n: int) -> datetime:
    return _now() - timedelta(days=n)


def _pick(items, weights):
    return random.choices(items, weights=weights, k=1)[0]


def _name() -> str:
    if fake:
        return fake.name()
    return f"{_names.choice(FIRST_NAMES)} {_names.choice(LAST_NAMES)}"


def _company() -> str:
    if fake:
        return fake.company()
    return f"{_names.choice(LAST_NAMES)} {_names.choice(['Industries', 'Enterprises', 'Solutions', 'Technologies'])}"


def _pan_masked() -> str:
    """ABCXX1234F — first three letters kept, the identifying pair masked."""
    letters = "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(3))
    return f"{letters}XX{random.randint(1000, 9999)}{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"


def _mobile_masked() -> str:
    return f"XXXXXX{random.randint(1000, 9999)}"


def _aadhaar_last4() -> str:
    return f"{random.randint(1000, 9999)}"


def _ifsc(idx: int) -> str:
    return f"ACME0{idx:06d}"[:11]


def _round(v: float, step: int = 1000) -> float:
    return float(int(round(v / step)) * step)


# ── generators ───────────────────────────────────────────────────────────────
def gen_branches(n: int = 60) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        city, state, _pin, region = CITIES[i % len(CITIES)]
        rows.append({
            "branch_code": f"BR-{i:04d}",
            "name": f"{city} {'Main' if i <= len(CITIES) else 'Branch ' + str(i)}",
            "city": city, "state": state, "region": region,
            "ifsc": _ifsc(i),
            "cluster_head": _name(),
        })
    return rows


def gen_agents(branches: List[Dict[str, Any]], n: int = 400) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        br = branches[i % len(branches)]
        channel = random.choice(AGENT_CHANNELS)
        rows.append({
            "agent_id": f"AGT-{i:05d}",
            "name": _name(),
            "branch_code": br["branch_code"],
            "channel": channel,
            # Only insurance-selling channels carry an IRDAI licence.
            "licence_no": (f"IRDAI-{random.randint(100000, 999999)}"
                           if channel in ("bancassurance", "agency") else None),
            "active": random.random() > 0.08,
        })
    return rows


def gen_customers(n: int = 10000) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        city, state, pin_prefix, _region = random.choice(CITIES)
        occupation = _pick(OCCUPATIONS, OCCUPATION_W)
        # Income shaped by occupation: salaried tighter, business fatter-tailed.
        if occupation == "salaried":
            income = random.gauss(78000, 34000)
        elif occupation == "professional":
            income = random.gauss(145000, 70000)
        elif occupation == "self_employed":
            income = random.gauss(112000, 82000)
        else:
            income = random.gauss(168000, 120000)
        income = max(18000.0, _round(income, 500))
        # ~8% have no bureau history at all — new-to-credit.
        score = None if random.random() < 0.08 else int(min(900, max(300, random.gauss(722, 74))))
        rows.append({
            "customer_id": f"CUS-{i:07d}",
            "name_full": _name(),
            "pan_masked": _pan_masked(),
            "aadhaar_last4": _aadhaar_last4(),
            "mobile_masked": _mobile_masked(),
            "email": f"customer{i}@example.in",
            "city": city, "state": state,
            "pin": f"{pin_prefix}{random.randint(1, 99):03d}",
            "occupation": occupation,
            "employer_name": (_company() if occupation == "salaried" else "Self"),
            "monthly_income_declared": income,
            "existing_emi": _round(income * random.uniform(0.0, 0.32), 100),
            "cibil_score": score,
            "kyc_status": _pick(KYC, KYC_W),
            "customer_segment": _pick(SEGMENTS, SEGMENT_W),
            "onboarded_on": (_days_ago(random.randint(30, 2200))).date(),
        })
    return rows


def gen_loan_applications(customers: List[Dict[str, Any]],
                          branches: List[Dict[str, Any]],
                          n: int = 12000) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        cust = random.choice(customers)
        product = _pick(PRODUCTS, PRODUCT_W)
        if product == "home":
            amount, tenure, roi = _round(random.uniform(1500000, 12000000)), random.choice([120, 180, 240, 300]), round(random.uniform(8.4, 9.9), 2)
        elif product == "personal":
            amount, tenure, roi = _round(random.uniform(80000, 2000000)), random.choice([12, 24, 36, 48, 60]), round(random.uniform(11.5, 18.0), 2)
        elif product == "auto":
            amount, tenure, roi = _round(random.uniform(300000, 2500000)), random.choice([36, 48, 60, 84]), round(random.uniform(9.2, 12.5), 2)
        elif product == "lap":
            amount, tenure, roi = _round(random.uniform(1000000, 8000000)), random.choice([60, 120, 180]), round(random.uniform(9.5, 13.0), 2)
        else:
            amount, tenure, roi = _round(random.uniform(500000, 5000000)), random.choice([24, 36, 60]), round(random.uniform(12.0, 17.5), 2)

        # Two populations, deliberately. ~15% are RECENT (the live credit
        # queue); the rest are historical and long since decided. A single
        # uniform window cannot serve both: keep it narrow and no loan is old
        # enough to have amortised (the repayment schedule comes out empty);
        # widen it and the queue empties out.
        recent = random.random() < 0.15
        age_days = random.randint(0, 45) if recent else random.randint(46, 1460)
        applied = _days_ago(age_days)
        proof = _pick(PROOFS, PROOF_W)
        declared_annual = float(cust["monthly_income_declared"]) * 12
        # Most filings roughly corroborate declared income; a minority do not.
        if proof is None or random.random() < 0.07:
            itr_income = None
        elif random.random() < 0.12:
            itr_income = _round(declared_annual * random.uniform(0.28, 0.55), 1000)
        else:
            itr_income = _round(declared_annual * random.uniform(0.88, 1.06), 1000)

        # Only recent applications can still be undecided — an application
        # sitting untouched for two years would be a data-quality bug, not a
        # queue item.
        r = random.random()
        if recent and r < 0.62:
            status = "new" if r < 0.34 else "under_review"
        elif r < 0.22:
            status = "rejected"
        elif r < 0.34:
            status = "approved"
        else:
            status = "disbursed"

        emi_est = amount / max(tenure, 1)
        foir = min(95.0, round(((float(cust["existing_emi"]) + emi_est)
                                / max(float(cust["monthly_income_declared"]), 1)) * 100, 2))
        decided = status not in ("new", "under_review")
        rows.append({
            "application_id": f"LAN-2026-{i:06d}",
            "customer_id": cust["customer_id"],
            "product": product,
            "amount_requested": amount,
            "tenure_months": tenure,
            "roi_offered": roi,
            "applied_at": applied,
            "branch_code": random.choice(branches)["branch_code"],
            "sourcing_channel": _pick(CHANNELS, CHANNEL_W),
            "income_proof_type": proof,
            "itr_declared_income": itr_income,
            "ltv_percent": (round(random.uniform(55, 90), 2)
                            if product in ("home", "auto", "lap") else None),
            "foir_percent": foir,
            "status": status,
            "decision_reason": ("FOIR above policy cap" if status == "rejected" and foir > 55
                                else ("Bureau history adverse" if status == "rejected" else None)),
            "decided_by": ("credit-officer@acme-bank-demo.citra.ai" if decided else None),
            "decided_at": (applied + timedelta(days=random.randint(1, 9)) if decided else None),
        })
    return rows


def gen_bureau_pulls(applications: List[Dict[str, Any]],
                     customers_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for i, app in enumerate(applications, start=1):
        cust = customers_by_id[app["customer_id"]]
        score = cust["cibil_score"]
        overdue = 0.0 if (score or 800) > 700 else _round(random.uniform(0, 180000), 100)
        rows.append({
            "pull_id": f"BPL-{i:07d}",
            "application_id": app["application_id"],
            "bureau": random.choice(["cibil", "experian", "crif"]),
            "score": score,
            "enquiries_6m": random.randint(0, 9),
            "active_loans": random.randint(0, 6),
            "overdue_amount": overdue,
            "writeoff_flag": bool(score and score < 620 and random.random() < 0.25),
            "pulled_at": app["applied_at"] + timedelta(hours=random.randint(1, 48)),
        })
    return rows


def gen_disbursements(applications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    disbursed = [a for a in applications if a["status"] == "disbursed"]
    for i, app in enumerate(disbursed, start=1):
        rows.append({
            "disbursement_id": f"DIS-{i:07d}",
            "application_id": app["application_id"],
            "amount": app["amount_requested"],
            "disbursed_at": (app["applied_at"] + timedelta(days=random.randint(3, 21))).date(),
            "utr": f"UTR{random.randint(10**11, 10**12 - 1)}",
            "account_masked": f"XXXXXX{random.randint(1000, 9999)}",
            "ifsc": _ifsc(random.randint(1, 60)),
        })
    return rows


def gen_loan_accounts(applications: List[Dict[str, Any]],
                      disbursements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_app = {a["application_id"]: a for a in applications}
    rows = []
    for i, dis in enumerate(disbursements, start=1):
        app = by_app[dis["application_id"]]
        principal = float(dis["amount"])
        tenure = app["tenure_months"]
        roi = float(app["roi_offered"] or 12.0)
        r = roi / 1200.0
        emi = principal * r * (1 + r) ** tenure / max((1 + r) ** tenure - 1, 1e-9)
        months_elapsed = max(0, (date.today() - dis["disbursed_at"]).days // 30)
        paid_ratio = min(0.95, months_elapsed / max(tenure, 1))
        # DPD distribution: most current, a long tail into the hard buckets.
        rr = random.random()
        if rr < 0.78:
            dpd = 0
        elif rr < 0.88:
            dpd = random.randint(1, 30)
        elif rr < 0.94:
            dpd = random.randint(31, 60)
        elif rr < 0.975:
            dpd = random.randint(61, 90)
        else:
            dpd = random.randint(91, 240)
        bucket = ("0" if dpd == 0 else "1-30" if dpd <= 30 else "31-60" if dpd <= 60
                  else "61-90" if dpd <= 90 else "90+")
        rows.append({
            "loan_account_no": f"LON-2026-{i:06d}",
            "customer_id": app["customer_id"],
            "application_id": app["application_id"],
            "product": app["product"],
            "principal": principal,
            "roi": roi,
            "emi_amount": _round(emi, 1),
            "tenure_months": tenure,
            "disbursed_on": dis["disbursed_at"],
            "outstanding": _round(principal * (1 - paid_ratio), 1),
            "dpd": dpd,
            "bucket": bucket,
            "npa_class": NPA_BY_BUCKET[bucket],
            "branch_code": app["branch_code"],
            "restructured": random.random() < 0.03,
        })
    return rows


def gen_repayment_schedule(accounts: List[Dict[str, Any]],
                           cap: int = 120000) -> List[Dict[str, Any]]:
    rows = []
    seq = 0
    for acc in accounts:
        months_elapsed = max(1, (date.today() - acc["disbursed_on"]).days // 30)
        n_inst = min(months_elapsed, acc["tenure_months"], 24)
        for k in range(1, n_inst + 1):
            if len(rows) >= cap:
                return rows
            seq += 1
            due = acc["disbursed_on"] + timedelta(days=30 * k)
            emi = float(acc["emi_amount"])
            # The last installments carry the account's delinquency.
            late = acc["dpd"] > 0 and k > n_inst - max(1, acc["dpd"] // 30)
            if late:
                status = random.choice(["unpaid", "bounced", "partial"])
            else:
                status = "paid"
            paid_amount = (emi if status == "paid"
                           else round(emi * random.uniform(0.2, 0.6), 2) if status == "partial"
                           else 0.0)
            rows.append({
                "schedule_id": f"SCH-{seq:08d}",
                "loan_account_no": acc["loan_account_no"],
                "installment_no": k,
                "due_date": due,
                "emi_due": emi,
                "paid_amount": paid_amount,
                "paid_on": (due + timedelta(days=random.randint(-3, 4))
                            if status in ("paid", "partial") else None),
                "status": status,
                "bounce_reason": (random.choice(BOUNCE_REASONS) if status == "bounced" else None),
            })
    return rows


def gen_delinquencies(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    delinquent = [a for a in accounts if a["dpd"] > 0]
    for i, acc in enumerate(delinquent, start=1):
        rows.append({
            "delinquency_id": f"DLQ-{i:07d}",
            "loan_account_no": acc["loan_account_no"],
            "as_of": date.today(),
            "dpd": acc["dpd"],
            "bucket": acc["bucket"],
            "overdue_amount": _round(float(acc["emi_amount"]) * max(1, acc["dpd"] // 30), 1),
            "last_paid_on": (date.today() - timedelta(days=acc["dpd"] + random.randint(0, 10))),
            "risk_flag": _pick(["none", "skip_trace", "dispute", "legal"],
                               [0.72, 0.14, 0.09, 0.05]),
        })
    return rows


def gen_collection_activities(delinquencies: List[Dict[str, Any]],
                              agents: List[Dict[str, Any]],
                              cap: int = 15000) -> List[Dict[str, Any]]:
    rows = []
    seq = 0
    for dl in delinquencies:
        for _ in range(random.randint(2, 7)):
            if len(rows) >= cap:
                return rows
            seq += 1
            outcome = _pick(["ptp", "no_contact", "dispute", "refused", "paid", "wrong_number"],
                            [0.30, 0.34, 0.08, 0.10, 0.10, 0.08])
            attempted = _days_ago(random.randint(0, min(60, max(1, dl["dpd"]))))
            ptp_date = (attempted + timedelta(days=random.randint(2, 12))).date() if outcome == "ptp" else None
            rows.append({
                "activity_id": f"ACT-{seq:08d}",
                "loan_account_no": dl["loan_account_no"],
                "agent_id": random.choice(agents)["agent_id"],
                "channel": _pick(["call", "sms", "whatsapp", "field", "legal"],
                                 [0.52, 0.18, 0.14, 0.13, 0.03]),
                "attempted_at": attempted,
                "outcome": outcome,
                "ptp_date": ptp_date,
                "ptp_amount": (_round(float(dl["overdue_amount"]) * random.uniform(0.3, 1.0), 100)
                               if outcome == "ptp" else None),
                "ptp_kept": (random.random() < 0.55 if outcome == "ptp" else None),
                "notes": None,
            })
    return rows


def gen_policies(customers: List[Dict[str, Any]], n: int = 9000) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        cust = random.choice(customers)
        line = _pick(LINES, LINE_W)
        if line == "motor":
            sum_insured = _round(random.uniform(300000, 2500000))
            premium = _round(sum_insured * random.uniform(0.025, 0.045), 100)
        elif line == "health":
            sum_insured = _round(random.choice([300000, 500000, 1000000, 2500000]))
            premium = _round(sum_insured * random.uniform(0.02, 0.05), 100)
        else:
            sum_insured = _round(random.uniform(1000000, 20000000))
            premium = _round(sum_insured * random.uniform(0.004, 0.012), 100)
        start = _days_ago(random.randint(30, 700)).date()
        rows.append({
            "policy_no": f"POL-{line[:3].upper()}-2026-{i:06d}",
            "customer_id": cust["customer_id"],
            "line": line,
            "product_name": random.choice(PRODUCT_NAMES[line]),
            "sum_insured": sum_insured,
            "premium_annual": premium,
            "start_date": start,
            "end_date": start + timedelta(days=365),
            "status": _pick(["active", "lapsed", "expired", "cancelled"],
                            [0.82, 0.08, 0.08, 0.02]),
            "nominee_name": _name(),
            "intimation_window_days": random.choice([15, 30, 30, 30, 45]),
        })
    return rows


def gen_claims(policies: List[Dict[str, Any]], n: int = 4000) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        pol = random.choice(policies)
        loss = _days_ago(random.randint(1, 330)).date()
        delay = _pick([random.randint(0, 3), random.randint(4, 14), random.randint(15, 45)],
                      [0.66, 0.24, 0.10])
        intimated = datetime.combine(loss, datetime.min.time()) + timedelta(days=delay, hours=random.randint(1, 20))
        claimed = _round(float(pol["sum_insured"]) * random.uniform(0.05, 0.65), 100)
        r = random.random()
        if r < 0.08:
            status = "intimated"
        elif r < 0.125:
            status = "under_survey"
        elif r < 0.22:
            status = "rejected"
        elif r < 0.36:
            status = "approved"
        else:
            status = "settled"
        decided = status in ("approved", "rejected", "settled")
        approved_amt = (_round(claimed * random.uniform(0.55, 1.0), 100)
                        if status in ("approved", "settled") else None)
        claim_type = random.choice(CLAIM_TYPES[pol["line"]])
        rows.append({
            "claim_id": f"CLM-2026-{i:06d}",
            "policy_no": pol["policy_no"],
            "customer_id": pol["customer_id"],
            "claim_type": claim_type,
            "loss_date": loss,
            "intimated_at": intimated,
            "intimation_delay_days": delay,
            "claimed_amount": claimed,
            "approved_amount": approved_amt,
            "status": status,
            "rejection_reason": ("Intimation beyond policy window" if status == "rejected" and delay > 30
                                 else ("Excluded peril" if status == "rejected" else None)),
            "surveyor_id": (f"SUR-{random.randint(1, 120):04d}"
                            if status != "intimated" else None),
            # Theft/burglary claims normally carry an FIR; others rarely do.
            "fir_number": (f"FIR/{random.randint(100, 999)}/2026"
                           if claim_type in ("theft", "burglary") and random.random() < 0.85
                           else None),
            "tat_days": (random.randint(3, 45) if decided else None),
            "decided_by": ("claims-officer@acme-bank-demo.citra.ai" if decided else None),
            "decided_at": (intimated + timedelta(days=random.randint(2, 40)) if decided else None),
        })
    return rows


def gen_claim_documents(claims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    seq = 0
    for cl in claims:
        types = ["policy_copy"]
        if cl["claim_type"] in ("own_damage", "third_party"):
            types += ["repair_estimate", "damage_photo"]
        elif cl["claim_type"] in ("theft", "burglary"):
            types += ["fir"]
        elif cl["claim_type"] == "hospitalisation":
            types += ["discharge_summary", "invoice"]
        else:
            types += ["invoice"]
        for t in types[: random.randint(2, len(types))]:
            seq += 1
            rows.append({
                "document_id": f"DOC-{seq:08d}",
                "claim_id": cl["claim_id"],
                "doc_type": t,
                "file_url": f"s3://acme-bank-demo/claims/{cl['claim_id']}/{t}.pdf",
                "uploaded_at": cl["intimated_at"] + timedelta(hours=random.randint(1, 72)),
                "verified": random.random() < 0.7,
                "content_sha256": uuid.uuid5(
                    uuid.NAMESPACE_URL, f"{cl['claim_id']}/{t}").hex + uuid.uuid4().hex[:0],
            })
    return rows


def gen_surveyor_reports(claims: List[Dict[str, Any]], cap: int = 2500) -> List[Dict[str, Any]]:
    rows = []
    eligible = [c for c in claims if c["surveyor_id"] and float(c["claimed_amount"]) > 50000]
    for i, cl in enumerate(eligible[:cap], start=1):
        rows.append({
            "report_id": f"SRP-{i:07d}",
            "claim_id": cl["claim_id"],
            "surveyor_id": cl["surveyor_id"],
            "surveyor_name": _name(),
            "visited_on": (cl["intimated_at"] + timedelta(days=random.randint(1, 10))).date(),
            "assessed_amount": _round(float(cl["claimed_amount"]) * random.uniform(0.5, 1.0), 100),
            "findings": "Damage consistent with the reported cause; parts assessed at market rate.",
            "photos_url": f"s3://acme-bank-demo/surveys/{cl['claim_id']}/photos/",
            "recommendation": _pick(["settle", "partial", "repudiate", "investigate"],
                                    [0.58, 0.26, 0.09, 0.07]),
        })
    return rows


def gen_leads(branches: List[Dict[str, Any]], agents: List[Dict[str, Any]],
              n: int = 6000) -> List[Dict[str, Any]]:
    rows = []
    for i in range(1, n + 1):
        city, _state, _pin, _region = random.choice(CITIES)
        status = _pick(["new", "contacted", "qualified", "converted", "lost"],
                       [0.15, 0.24, 0.20, 0.18, 0.23])
        assigned = None if status == "new" and random.random() < 0.6 else random.choice(agents)
        rows.append({
            "lead_id": f"LED-2026-{i:06d}",
            "name_full": _name(),
            "mobile_masked": _mobile_masked(),
            "city": city,
            "product_interest": random.choice(LEAD_INTERESTS),
            "source": random.choice(LEAD_SOURCES),
            "created_at": _days_ago(random.randint(0, 120)),
            "status": status,
            "assigned_agent_id": (assigned["agent_id"] if assigned else None),
            "branch_code": (assigned["branch_code"] if assigned
                            else random.choice(branches)["branch_code"]),
            "expected_value": _round(random.uniform(80000, 6000000)),
        })
    return rows


def gen_opportunities(leads: List[Dict[str, Any]], n: int = 3000) -> List[Dict[str, Any]]:
    rows = []
    pool = [l for l in leads if l["status"] in ("qualified", "converted", "lost")]
    for i, lead in enumerate(random.sample(pool, min(n, len(pool))), start=1):
        if lead["status"] == "converted":
            stage, prob = "won", 100
        elif lead["status"] == "lost":
            stage, prob = "lost", 0
        else:
            stage = _pick(["prospect", "proposal", "negotiation"], [0.4, 0.35, 0.25])
            prob = {"prospect": 20, "proposal": 50, "negotiation": 75}[stage]
        value = float(lead["expected_value"])
        rows.append({
            "opportunity_id": f"OPP-2026-{i:06d}",
            "lead_id": lead["lead_id"],
            "product": lead["product_interest"],
            "expected_value": value,
            "stage": stage,
            "probability": prob,
            "expected_close": (lead["created_at"] + timedelta(days=random.randint(10, 90))).date(),
            "closed_on": ((lead["created_at"] + timedelta(days=random.randint(10, 80))).date()
                          if stage in ("won", "lost") else None),
            "booked_value": (_round(value * random.uniform(0.8, 1.05), 100)
                             if stage == "won" else None),
        })
    return rows


# ── needle rows (SPEC.md §4) ─────────────────────────────────────────────────
def add_needle_rows(customers, applications, bureau_pulls, disbursements, accounts,
                    schedule, delinquencies, activities, policies, claims,
                    claim_docs, leads, agents, branches) -> None:
    """The deterministic demo paths. Every scripted moment depends on these."""
    br = branches[0]["branch_code"]

    # CUS-NEEDLE-001 — the customer behind the flagship judgement case.
    cust = {
        "customer_id": "CUS-NEEDLE-001",
        "name_full": "Rohit Deshmukh",
        "pan_masked": "AAPXX4417K", "aadhaar_last4": "8842",
        "mobile_masked": "XXXXXX7781", "email": "needle.customer@example.in",
        "city": "Pune", "state": "Maharashtra", "pin": "411014",
        # SALARIED on purpose. The Income Verification SOP asks a salaried
        # applicant for payslips + Form 16 and checks the Form 16 is GENUINE —
        # it never reconciles the filed figure against declared income. So this
        # file satisfies the SOP completely while the tax filing tells a
        # different story. Make the applicant self-employed and the SOP would
        # demand an ITR, turning this into a document-deficiency case instead
        # of the judgement case it is meant to be.
        "occupation": "salaried", "employer_name": "Sundara Technologies Pvt Ltd",
        "monthly_income_declared": 185000.00, "existing_emi": 24000.00,
        "cibil_score": 771, "kyc_status": "verified",
        "customer_segment": "affluent",
        "onboarded_on": _days_ago(900).date(),
    }
    customers.append(cust)

    # LAN-NEEDLE-001 — declared income healthy, tax filing does NOT corroborate.
    # Every box the SOP asks about is ticked; only an experienced eye catches it.
    app = {
        "application_id": "LAN-NEEDLE-001",
        "customer_id": "CUS-NEEDLE-001",
        "product": "personal", "amount_requested": 1200000.00,
        "tenure_months": 48, "roi_offered": 13.25,
        # Pending 47 days. A credit queue is worked OLDEST FIRST (TAT), so a
        # file applied 3 days ago sat ~1000th of 1,062 actionable rows and no
        # officer would ever reach it. Ageing it is also the better story: the
        # uncorroborated income has been sitting unspotted for six weeks.
        "applied_at": _days_ago(47), "branch_code": br,
        "sourcing_channel": "branch",
        "income_proof_type": "payslip",
        "itr_declared_income": 620000.00,      # vs 22.2 lakh declared annually
        "ltv_percent": None, "foir_percent": 26.4,
        "status": "under_review",
        "decision_reason": None, "decided_by": None, "decided_at": None,
    }
    applications.append(app)
    bureau_pulls.append({
        "pull_id": "BPL-NEEDLE-001", "application_id": "LAN-NEEDLE-001",
        "bureau": "cibil", "score": 771, "enquiries_6m": 2, "active_loans": 1,
        "overdue_amount": 0.0, "writeoff_flag": False,
        "pulled_at": _days_ago(3) + timedelta(hours=4),
    })

    # LON-NEEDLE-002 — DPD 61, one bounce, one broken PTP. Collections case.
    acc = {
        "loan_account_no": "LON-NEEDLE-002",
        "customer_id": customers[0]["customer_id"],
        "application_id": None,
        "product": "personal", "principal": 900000.00, "roi": 14.5,
        "emi_amount": 24500.00, "tenure_months": 48,
        "disbursed_on": _days_ago(420).date(), "outstanding": 612000.00,
        "dpd": 61, "bucket": "61-90", "npa_class": "sma_2",
        "branch_code": br, "restructured": False,
    }
    accounts.append(acc)
    schedule.append({
        "schedule_id": "SCH-NEEDLE-01", "loan_account_no": "LON-NEEDLE-002",
        "installment_no": 13, "due_date": _days_ago(61).date(), "emi_due": 24500.00,
        "paid_amount": 0.0, "paid_on": None, "status": "bounced",
        "bounce_reason": "insufficient_funds",
    })
    delinquencies.append({
        # Id 0 deliberately: a queue panel's window is the first N rows BY
        # PRIMARY KEY, so "DLQ-NEEDLE-001" sorted after every "DLQ-00xxxxx"
        # and the flagship collections case was unreachable in the UI. The
        # account number keeps the NEEDLE marker for searching.
        "delinquency_id": "DLQ-0000000", "loan_account_no": "LON-NEEDLE-002",
        "as_of": date.today(), "dpd": 61, "bucket": "61-90",
        "overdue_amount": 49000.00,
        "last_paid_on": _days_ago(71).date(), "risk_flag": "none",
    })
    activities.append({
        "activity_id": "ACT-NEEDLE-001", "loan_account_no": "LON-NEEDLE-002",
        "agent_id": agents[0]["agent_id"], "channel": "call",
        "attempted_at": _days_ago(20), "outcome": "ptp",
        "ptp_date": _days_ago(12).date(), "ptp_amount": 49000.00,
        "ptp_kept": False,                      # the broken promise
        "notes": "Promised to clear both installments; nothing received.",
    })

    def _cover_for(line: str, loss_date, window: int = 30) -> Dict[str, Any]:
        """A policy of the right LINE that is genuinely IN FORCE on the loss date.

        Both claim needles used to take policies[0] / policies[1] whichever line
        those happened to be, and whatever period. CLM-NEEDLE-004 landed as a
        hospitalisation claim against a MOTOR policy that had expired seven
        months before the loss — so the agent (correctly) repudiated on cover
        and policy-not-in-force and never reached the 40-day intimation delay
        the needle exists to demonstrate. A needle has to fail for exactly ONE
        reason, or it tests something other than what it claims to.
        """
        idx = next((i for i, p in enumerate(policies) if p["line"] == line), 0)
        pol = dict(policies[idx])
        pol["start_date"] = loss_date - timedelta(days=200)
        pol["end_date"] = loss_date + timedelta(days=165)
        pol["status"] = "active"
        pol["intimation_window_days"] = window
        policies[idx] = pol
        return pol

    # CLM-NEEDLE-003 — repair estimate byte-identical to an earlier claim's.
    _n3_loss = _days_ago(9).date()
    pol = _cover_for("motor", _n3_loss)
    dup_sha = uuid.uuid5(uuid.NAMESPACE_URL, "acme-bank/reused-estimate").hex
    claims.append({
        "claim_id": "CLM-NEEDLE-003", "policy_no": pol["policy_no"],
        "customer_id": pol["customer_id"], "claim_type": "own_damage",
        "loss_date": _n3_loss, "intimated_at": _days_ago(8),
        "intimation_delay_days": 1, "claimed_amount": 240000.00,
        "approved_amount": None, "status": "under_survey",
        "rejection_reason": None, "surveyor_id": "SUR-0007",
        "fir_number": None, "tat_days": None,
        "decided_by": None, "decided_at": None,
    })
    claim_docs.append({
        "document_id": "DOC-NEEDLE-001", "claim_id": "CLM-NEEDLE-003",
        "doc_type": "repair_estimate",
        "file_url": "s3://acme-bank-demo/claims/CLM-NEEDLE-003/repair_estimate.pdf",
        "uploaded_at": _days_ago(8) + timedelta(hours=2),
        "verified": False, "content_sha256": dup_sha,
    })
    # ...the SAME bytes already on an older claim — this is what makes the
    # duplicate-artifact screen fire with a real record to point at.
    older = claims[0]
    claim_docs.append({
        "document_id": "DOC-NEEDLE-002", "claim_id": older["claim_id"],
        "doc_type": "repair_estimate",
        "file_url": f"s3://acme-bank-demo/claims/{older['claim_id']}/repair_estimate.pdf",
        "uploaded_at": older["intimated_at"] + timedelta(hours=3),
        "verified": True, "content_sha256": dup_sha,
    })

    # CLM-NEEDLE-004 — intimated 40 days after loss, window is 30. Exclusion
    # path. Health cover, in force, so late intimation is the ONLY thing wrong.
    _n4_loss = _days_ago(45).date()
    pol2 = _cover_for("health", _n4_loss, window=30)
    claims.append({
        "claim_id": "CLM-NEEDLE-004", "policy_no": pol2["policy_no"],
        "customer_id": pol2["customer_id"], "claim_type": "hospitalisation",
        "loss_date": _n4_loss, "intimated_at": _days_ago(5),
        "intimation_delay_days": 40, "claimed_amount": 185000.00,
        "approved_amount": None, "status": "intimated",
        "rejection_reason": None, "surveyor_id": None,
        "fir_number": None, "tat_days": None,
        "decided_by": None, "decided_at": None,
    })

    # LED-NEEDLE-005 — high-value lead, unassigned, ageing.
    leads.append({
        "lead_id": "LED-NEEDLE-005", "name_full": "Kavita Menon",
        "mobile_masked": "XXXXXX2290", "city": "Bengaluru",
        "product_interest": "home_loan", "source": "referral",
        "created_at": _days_ago(6), "status": "new",
        "assigned_agent_id": None, "branch_code": br,
        "expected_value": 4500000.00,
    })


# ── db plumbing ──────────────────────────────────────────────────────────────
def _ddl() -> str:
    return SCHEMA_SQL.read_text(encoding="utf-8")


def _insert_many(cur, table: str, rows: List[Dict[str, Any]]):
    if not rows:
        log.info("  %s: 0 rows (skipped)", table)
        return
    import psycopg2.extras

    cols = list(rows[0].keys())
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
           f"VALUES ({', '.join('%(' + c + ')s' for c in cols)})")
    psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
    log.info("  %s: %d rows", table, len(rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conn", default=DEFAULT_CONN)
    ap.add_argument("--dry-run", action="store_true",
                    help="generate in memory only, write nothing")
    args = ap.parse_args()

    log.info("Generating Acme Bank demo data (deterministic seed=%d)...", SEED)
    branches = gen_branches()
    log.info("  branches: %d", len(branches))
    agents = gen_agents(branches)
    log.info("  agents: %d", len(agents))
    customers = gen_customers()
    log.info("  customers: %d", len(customers))
    by_id = {c["customer_id"]: c for c in customers}
    applications = gen_loan_applications(customers, branches)
    log.info("  loan_applications: %d", len(applications))
    bureau_pulls = gen_bureau_pulls(applications, by_id)
    log.info("  bureau_pulls: %d", len(bureau_pulls))
    disbursements = gen_disbursements(applications)
    log.info("  disbursements: %d", len(disbursements))
    accounts = gen_loan_accounts(applications, disbursements)
    log.info("  loan_accounts: %d", len(accounts))
    schedule = gen_repayment_schedule(accounts)
    log.info("  repayment_schedule: %d", len(schedule))
    delinquencies = gen_delinquencies(accounts)
    log.info("  delinquencies: %d", len(delinquencies))
    activities = gen_collection_activities(delinquencies, agents)
    log.info("  collection_activities: %d", len(activities))
    policies = gen_policies(customers)
    log.info("  policies: %d", len(policies))
    claims = gen_claims(policies)
    log.info("  claims: %d", len(claims))
    claim_docs = gen_claim_documents(claims)
    log.info("  claim_documents: %d", len(claim_docs))
    surveyor_reports = gen_surveyor_reports(claims)
    log.info("  surveyor_reports: %d", len(surveyor_reports))
    leads = gen_leads(branches, agents)
    log.info("  leads: %d", len(leads))
    opportunities = gen_opportunities(leads)
    log.info("  opportunities: %d", len(opportunities))

    log.info("Adding needle rows...")
    add_needle_rows(customers, applications, bureau_pulls, disbursements, accounts,
                    schedule, delinquencies, activities, policies, claims,
                    claim_docs, leads, agents, branches)

    tables = [
        ("branches", branches), ("agents", agents), ("customers", customers),
        ("loan_applications", applications), ("bureau_pulls", bureau_pulls),
        ("disbursements", disbursements), ("loan_accounts", accounts),
        ("repayment_schedule", schedule), ("delinquencies", delinquencies),
        ("collection_activities", activities), ("policies", policies),
        ("claims", claims), ("claim_documents", claim_docs),
        ("surveyor_reports", surveyor_reports), ("leads", leads),
        ("opportunities", opportunities),
    ]

    # NEEDLES FIRST. A queue panel fetches a capped window (the runtime shows
    # "First 500 only") and then sorts/filters/searches CLIENT-side within it,
    # so a needle outside the window is unreachable however the officer sorts:
    # the demo's own flagship case cannot be opened. Insertion order is only
    # half the story — the window is ordered by PRIMARY KEY, so needle ids must
    # ALSO sort early (see delinquency_id above). Both found by driving the
    # actual UI, not by reading the spec.
    def _needles_first(rows):
        needles = [r for r in rows if any("NEEDLE" in str(v) for v in r.values())]
        if not needles:
            return rows
        rest = [r for r in rows if r not in needles]
        return needles + rest

    tables = [(name, _needles_first(rows)) for name, rows in tables]
    total = sum(len(r) for _t, r in tables)
    log.info("TOTAL: %d rows across %d tables", total, len(tables))

    # Queue sizes the demo depends on — surfaced so a bad distribution is
    # caught here, not in front of a customer.
    log.info("  credit queue      : %d",
             sum(1 for a in applications if a["status"] in ("new", "under_review")))
    log.info("  collections 61-90+: %d",
             sum(1 for d in delinquencies if d["bucket"] in ("61-90", "90+")))
    log.info("  claims queue      : %d",
             sum(1 for c in claims if c["status"] in ("intimated", "under_survey")))
    log.info("  new leads         : %d",
             sum(1 for l in leads if l["status"] == "new"))

    if args.dry_run:
        log.info("--dry-run set; nothing written.")
        return 0

    import psycopg2

    log.info("Connecting to %s", args.conn.rsplit("@", 1)[-1])
    conn = psycopg2.connect(args.conn)
    try:
        with conn.cursor() as cur:
            log.info("Applying schema.sql...")
            cur.execute(_ddl())
            log.info("Inserting rows (parents first)...")
            for table, rows in tables:
                _insert_many(cur, table, rows)
        conn.commit()
        log.info("Done — %d rows committed.", total)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
