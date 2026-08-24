# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

r"""Middleware / data-plane E2E for the Acme Bank demo tenant.

Mirrors acme_power_e2e.py and extends it: as well as the MCP data plane, it
checks the pieces this tenant exists to demonstrate — the catalogue the builder
searches, the SOP corpus every app grounds on, and the case_signature that
decides whether an app can learn at all.

Only needs httpx + pyjwt. Every step prints PASS/FAIL, is collected, summarised
and written to a markdown report next to this script.

Run it with the stack up (MCP :8504, discovery :9000, data-discovery :8095,
smart-app-service :9100):

    cd c:/Github/Citra-AI/demo-data/tenants/acme-bank
    <Citra-Service venv>/python.exe scripts/acme_bank_e2e.py

Exit code 0 when every CORE step passes. Non-core steps are recorded only.

Contracts mirrored from source-mcp-template:
  RunQueryRequest       {source_id, dataset_id?, kind, query, row_limit}
  RunQueryResponse      {rows[], total, truncated, error?}
  ExecuteActionRequest  {source_id, dataset_id, action_id, payload, dry_run}

AUTH, easy to get backwards: `Authorization: Bearer <MCP_API_KEY>` is the
SERVICE key; the end user travels separately in `X-User-JWT`, and that is what
the visibility PDP evaluates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — console reconfiguration is best-effort
    pass

import httpx
import jwt

SCRIPT_DIR = Path(__file__).resolve().parent
TENANT_DIR = SCRIPT_DIR.parent
REPO = TENANT_DIR.parents[2]
REPORT_PATH = SCRIPT_DIR / "acme-bank-e2e-report.md"

TENANT = "acme-bank"
DEPTS = ["lending", "collections", "claims", "sales_distribution", "central_ops"]

EXPECTED_DATASETS = [
    "loan_origination.customers", "loan_origination.loan_applications",
    "loan_origination.bureau_pulls", "loan_origination.disbursements",
    "loan_servicing.loan_accounts", "loan_servicing.repayment_schedule",
    "loan_servicing.delinquencies", "loan_servicing.collection_activities",
    "insurance_claims.policies", "insurance_claims.claims",
    "insurance_claims.claim_documents", "insurance_claims.surveyor_reports",
    "sales_crm.branches", "sales_crm.agents", "sales_crm.leads",
    "sales_crm.opportunities",
]

DECISION_APPS = ["acme-bank-collections-priority", "acme-bank-claim-triage"]
DASHBOARD_APP = "acme-bank-sales"


@dataclass
class StepResult:
    num: int
    name: str
    core: bool
    passed: bool = False
    detail: str = ""


@dataclass
class Harness:
    results: list = field(default_factory=list)

    def record(self, num, name, core, passed, detail) -> None:
        self.results.append(StepResult(num, name, core, passed, detail))
        status = "PASS" if passed else ("FAIL" if core else "SKIP/INFO")
        print(f"[{status}] Step {num}: {name}")
        for line in (detail or "").splitlines():
            print(f"         {line}")

    def core_passed(self) -> bool:
        return all(r.passed for r in self.results if r.core)


def run_step(h: Harness, num: int, name: str, core: bool, fn) -> None:
    try:
        passed, detail = fn()
    except Exception as exc:  # noqa: BLE001 — a step must never abort the run
        passed, detail = False, f"{type(exc).__name__}: {exc}"
    h.record(num, name, core, passed, detail)


def _secret() -> str:
    # The root .env is the single source for the dev stack (see its header), so
    # this no longer reaches into another tenant's folder for a shared secret.
    env = REPO / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("JWT_SECRET="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("JWT_SECRET not found")


def _mcp_key() -> str:
    env = TENANT_DIR / "mcp" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("MCP_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("MCP_API_KEY not found — copy mcp/.env.example to mcp/.env")


def _user_jwt(secret: str, roles=("org_admin",)) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "coo@acme-bank-demo.citra.ai", "user_id": "coo@acme-bank-demo.citra.ai",
         "email": "coo@acme-bank-demo.citra.ai",
         "tenant_id": TENANT, "org_id": TENANT, "dept_ids": DEPTS,
         "roles": list(roles), "service_account_admin_of": [],
         "service_account_member_of": [], "iat": now, "exp": now + 1800,
         "iss": "Citra-AI"},
        secret, algorithm="HS256")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mcp-url", default="http://localhost:8504")
    ap.add_argument("--discovery-url", default="http://localhost:9000")
    ap.add_argument("--data-discovery-url", default="http://localhost:8095")
    ap.add_argument("--smart-app-url", default="http://localhost:9100")
    args = ap.parse_args()

    secret = _secret()
    user_jwt = _user_jwt(secret)
    mcp_headers = {"Authorization": f"Bearer {_mcp_key()}", "X-User-JWT": user_jwt}
    svc_headers = {"Authorization": f"Bearer {user_jwt}"}

    print("=" * 76)
    print("acme-bank middleware / data-plane E2E")
    print(f"  MCP            : {args.mcp_url}")
    print(f"  discovery      : {args.discovery_url}")
    print(f"  data-discovery : {args.data_discovery_url}")
    print(f"  smart-app      : {args.smart_app_url}")
    print("=" * 76)

    h = Harness()
    mcp = httpx.Client(base_url=args.mcp_url, headers=mcp_headers, timeout=60.0)
    dd = httpx.Client(base_url=args.data_discovery_url, headers=svc_headers, timeout=90.0)
    sa = httpx.Client(base_url=args.smart_app_url, headers=svc_headers, timeout=90.0)

    # ── 1. MCP health ───────────────────────────────────────────────────────
    def step1():
        r = httpx.get(f"{args.mcp_url}/health", timeout=20.0)
        if r.status_code != 200:
            return False, f"expected 200, got {r.status_code}"
        b = r.json()
        srcs = set(b.get("sources") or [])
        want = {"loan_origination", "loan_servicing", "insurance_claims", "sales_crm"}
        if not want.issubset(srcs):
            return False, f"missing structured sources: {sorted(want - srcs)}"
        if b.get("org_id") != TENANT:
            return False, f"org_id={b.get('org_id')!r}, expected {TENANT!r}"
        if not b.get("discovery_registered"):
            return False, "MCP is not registered with discovery"
        return True, f"sources={sorted(srcs)} org={b.get('org_id')} registered=True"

    run_step(h, 1, "MCP /health — 4 structured sources, registered, right org", True, step1)

    # ── 2. datasets ─────────────────────────────────────────────────────────
    def step2():
        r = mcp.get("/datasets")
        if r.status_code != 200:
            return False, f"expected 200, got {r.status_code}: {r.text[:200]}"
        body = r.json()
        items = body if isinstance(body, list) else (
            body.get("datasets") or body.get("items") or body.get("entries") or [])
        ids = {d.get("id") or d.get("dataset_id") for d in items}
        missing = [d for d in EXPECTED_DATASETS if d not in ids]
        if missing:
            return False, f"missing datasets: {missing}"
        return True, f"{len(ids)} datasets exposed; all 16 expected present"

    run_step(h, 2, "MCP /datasets — all 16 SQL datasets exposed", True, step2)

    # ── 3. the flagship judgement case ──────────────────────────────────────
    def step3():
        sql = ("SELECT a.application_id, a.status, a.income_proof_type, "
               "a.itr_declared_income, a.foir_percent, "
               "c.monthly_income_declared, c.cibil_score, c.occupation "
               "FROM loan_applications a JOIN customers c USING (customer_id) "
               "WHERE a.application_id = 'LAN-NEEDLE-001'")
        r = mcp.post("/run_query", json={
            "source_id": "loan_origination",
            "dataset_id": "loan_origination.loan_applications",
            "kind": "sql", "query": sql, "row_limit": 5})
        if r.status_code != 200:
            return False, f"expected 200, got {r.status_code}: {r.text[:200]}"
        b = r.json()
        if b.get("error"):
            return False, f"query error: {b['error']}"
        rows = b.get("rows") or []
        if not rows:
            return False, "LAN-NEEDLE-001 not found"
        row = rows[0]
        declared = float(row["monthly_income_declared"]) * 12
        filed = float(row["itr_declared_income"])
        checks = [
            (row["status"] == "under_review", "still in the credit queue"),
            (row["income_proof_type"] == "payslip", "income proof on file"),
            (row["occupation"] == "salaried", "salaried (so the SOP asks for payslips, not an ITR)"),
            (int(row["cibil_score"]) >= 750, "bureau healthy"),
            (float(row["foir_percent"]) < 50, "FOIR within policy"),
            (filed < declared * 0.5, "tax filing does NOT corroborate declared income"),
        ]
        bad = [why for ok, why in checks if not ok]
        if bad:
            return False, f"needle no longer presents as a clean file: {bad}"
        return True, (f"clean on every SOP check, and filed {filed:,.0f} vs declared "
                      f"{declared:,.0f} = {filed / declared:.0%} corroborated")

    run_step(h, 3, "LAN-NEEDLE-001 — clean file, uncorroborated income", True, step3)

    # ── 4. collections needle ───────────────────────────────────────────────
    def step4():
        r = mcp.post("/run_query", json={
            "source_id": "loan_servicing",
            "dataset_id": "loan_servicing.collection_activities",
            "kind": "sql",
            "query": ("SELECT a.activity_id, a.outcome, a.ptp_kept, l.dpd, l.bucket, "
                      "l.npa_class FROM collection_activities a "
                      "JOIN loan_accounts l USING (loan_account_no) "
                      "WHERE a.loan_account_no = 'LON-NEEDLE-002'"),
            "row_limit": 10})
        if r.status_code != 200:
            return False, f"expected 200, got {r.status_code}: {r.text[:200]}"
        rows = (r.json() or {}).get("rows") or []
        if not rows:
            return False, "LON-NEEDLE-002 has no activity"
        broken = [x for x in rows if x.get("outcome") == "ptp" and x.get("ptp_kept") is False]
        if not broken:
            return False, "no BROKEN promise-to-pay on the needle account"
        row = rows[0]
        if int(row["dpd"]) != 61 or row["bucket"] != "61-90":
            return False, f"expected dpd=61 bucket=61-90, got {row['dpd']}/{row['bucket']}"
        return True, (f"dpd={row['dpd']} bucket={row['bucket']} npa={row['npa_class']}, "
                      f"{len(broken)} broken PTP")

    run_step(h, 4, "LON-NEEDLE-002 — DPD 61 with a broken promise-to-pay", True, step4)

    # ── 5. duplicate-artifact needle ────────────────────────────────────────
    def step5():
        r = mcp.post("/run_query", json={
            "source_id": "insurance_claims",
            "dataset_id": "insurance_claims.claim_documents",
            "kind": "sql",
            "query": ("SELECT claim_id, document_id, doc_type FROM claim_documents "
                      "WHERE content_sha256 = (SELECT content_sha256 FROM claim_documents "
                      "WHERE document_id = 'DOC-NEEDLE-001') ORDER BY claim_id"),
            "row_limit": 10})
        if r.status_code != 200:
            return False, f"expected 200, got {r.status_code}: {r.text[:200]}"
        rows = (r.json() or {}).get("rows") or []
        claims = {x["claim_id"] for x in rows}
        if len(rows) != 2 or len(claims) != 2:
            return False, f"expected the same bytes on exactly 2 claims, got {rows}"
        return True, f"identical estimate on {sorted(claims)} — duplicate screen has a real target"

    run_step(h, 5, "CLM-NEEDLE-003 — estimate byte-identical to one other claim", True, step5)

    # ── 6. write action, dry run only ───────────────────────────────────────
    def step6():
        base = {
            "source_id": "loan_origination",
            "dataset_id": "loan_origination.loan_applications",
            "action_id": "record_credit_decision",
            "payload": {
                "application_id": "LAN-NEEDLE-001",
                "status": "under_review",
                "decision_reason": "E2E dry run — no change intended",
                "decided_by": "e2e@acme-bank-demo.citra.ai",
                "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        }
        r = mcp.post("/execute_action", json={**base, "dry_run": True})
        if r.status_code != 200:
            return False, f"expected 200, got {r.status_code}: {r.text[:200]}"
        b = r.json()
        if not b.get("ok"):
            return False, f"dry_run not ok: {b.get('error')}"
        # The needle must be UNCHANGED — a dry run that writes is worse than one
        # that fails, because nothing surfaces it.
        chk = mcp.post("/run_query", json={
            "source_id": "loan_origination",
            "dataset_id": "loan_origination.loan_applications",
            "kind": "sql",
            "query": ("SELECT status, decision_reason FROM loan_applications "
                      "WHERE application_id = 'LAN-NEEDLE-001'"),
            "row_limit": 1})
        row = ((chk.json() or {}).get("rows") or [{}])[0]
        if row.get("decision_reason"):
            return False, f"DRY RUN MUTATED THE ROW: decision_reason={row['decision_reason']!r}"
        return True, "dry_run ok and the row is untouched"

    run_step(h, 6, "record_credit_decision — dry run does not mutate", True, step6)

    # ── 7. catalogue (what the builder searches) ────────────────────────────
    def step7():
        r = dd.get("/catalogue", params={"limit": 100})
        if r.status_code != 200:
            return False, f"/catalogue {r.status_code}: {r.text[:200]}"
        items = (r.json() or {}).get("entries") or (r.json() or {}).get("items") or []
        ids = {i.get("dataset_id") for i in items}
        missing = [d for d in EXPECTED_DATASETS if d not in ids]
        if missing:
            return False, f"catalogue missing {len(missing)}: {missing[:4]}"
        foreign = [i["dataset_id"] for i in items if i.get("tenant_id") != TENANT]
        if foreign:
            return False, f"cross-tenant rows in the catalogue: {foreign[:3]}"
        s = dd.get("/catalogue/search",
                   params={"q": "loan applications awaiting a credit decision", "top_k": 3})
        hits = (s.json() or {}).get("entries") or []
        top = hits[0].get("dataset_id") if hits else None
        if top != "loan_origination.loan_applications":
            return False, f"search top hit was {top!r}"
        return True, f"{len(ids)} datasets, no cross-tenant rows, search top hit correct"

    run_step(h, 7, "catalogue — 16 datasets, tenant-clean, searchable", True, step7)

    # ── 8. SOP corpus ───────────────────────────────────────────────────────
    def step8():
        sys.path.insert(0, str(REPO / "Citra-Service"))
        import os
        cwd = os.getcwd()
        os.chdir(REPO / "Citra-Service")
        try:
            from dotenv import load_dotenv
            load_dotenv(REPO / "Citra-Service" / ".env")
            from dept_library_store import shared_dept_collection
            from config.milvus_config import get_milvus_client
            coll = shared_dept_collection()
            client = get_milvus_client()
            rows = client.query(collection_name=coll,
                                filter=f'org_id == "{TENANT}"',
                                output_fields=["doc_path"], limit=100)
            docs = {r.get("doc_path") for r in rows}
            if len(docs) < 12:
                return False, f"expected 12 SOP docs, found {len(docs)}"
            gap = [d for d in docs if "income_verification" in (d or "")]
            return True, (f"{len(rows)} chunks / {len(docs)} docs in {coll}"
                          + (f"; income SOP present ({gap[0]})" if gap else ""))
        finally:
            os.chdir(cwd)

    run_step(h, 8, "SOP corpus — 12 documents ingested and org-isolated", True, step8)

    # ── 9. apps + case_signature ────────────────────────────────────────────
    def step9():
        notes = []
        for slug in DECISION_APPS:
            r = sa.get(f"/apps/{slug}")
            if r.status_code != 200:
                return False, f"GET /apps/{slug} -> {r.status_code}: {r.text[:160]}"
            spec = (r.json() or {}).get("app_spec") or {}
            cs = spec.get("case_signature") or {}
            if not cs:
                return False, (f"{slug} has NO case_signature — its corrections would be "
                               "stored uncoded and could never author a judgement")
            notes.append(f"{slug}: {len(cs.get('facets') or [])} facets / "
                         f"{len(cs.get('reason_codes') or [])} codes")
        r = sa.get(f"/apps/{DASHBOARD_APP}")
        if r.status_code != 200:
            return False, f"GET /apps/{DASHBOARD_APP} -> {r.status_code}"
        if ((r.json() or {}).get("app_spec") or {}).get("case_signature"):
            return False, f"{DASHBOARD_APP} should NOT carry a case_signature (no decision)"
        notes.append(f"{DASHBOARD_APP}: no signature (correct — dashboard only)")
        return True, "\n".join(notes)

    run_step(h, 9, "apps — decision apps carry case_signature, dashboard does not", True, step9)

    # ── 9b. can the OFFICERS actually see their app? ────────────────────────
    def step9b():
        """A test publish forces audience='owner' on purpose (an unpromoted app
        must not leak), and audience is set at PROMOTE. Get that wrong and every
        app is published, healthy, and invisible to the people who use it — which
        only shows up in front of a customer. So assert it here."""
        expect = {
            ("collections-mum@acme-bank-demo.citra.ai", "collections"): "acme-bank-collections-priority",
            ("claims-motor@acme-bank-demo.citra.ai", "claims"): "acme-bank-claim-triage",
            ("credit-pune@acme-bank-demo.citra.ai", "lending"): "loan-application-triage",
            ("sales-manager@acme-bank-demo.citra.ai", "sales_distribution"): DASHBOARD_APP,
        }
        bad, seen = [], []
        for (user, dept), slug in expect.items():
            now = int(time.time())
            t = jwt.encode(
                {"sub": user, "user_id": user, "email": user,
                 "tenant_id": TENANT, "org_id": TENANT, "dept_ids": [dept],
                 "roles": ["user"], "service_account_admin_of": [],
                 "service_account_member_of": [], "iat": now, "exp": now + 300,
                 "iss": "Citra-AI"}, secret, algorithm="HS256")
            rr = httpx.get(f"{args.smart_app_url}/apps",
                           headers={"Authorization": f"Bearer {t}"},
                           params={"scope": "all", "limit": 50}, timeout=30.0)
            slugs = [a.get("slug") for a in ((rr.json() or {}).get("apps") or [])]
            seen.append(f"{dept}: {slugs}")
            if slug not in slugs:
                bad.append(f"{dept} officer cannot see {slug}")
        if bad:
            return False, "\n".join(bad + seen)
        return True, "\n".join(seen)

    run_step(h, 9.5, "audience — every officer sees their own app", True, step9b)

    def step9f():
        """Documents: real bytes, one deliberate duplicate, no broken links.

        The fraud stack fingerprints REAL BYTES, so every filed document is a
        potential "reused document" signal. Exactly ONE byte-identical pair is
        intended — the repair estimate filed on two claims. A second collision
        would mean the generator started producing look-alikes and the claims
        app would cry fraud on unrelated cases; zero would mean the demo's
        flagship duplicate is gone. Both are failures, so assert the number.

        Also asserts no row advertises a file it cannot serve: an unfiled
        document must carry NO file_url, because a dead link in a demo is worse
        than an honest absence.
        """
        def q(sql):
            r = mcp.post("/run_query", json={"source_id": "insurance_claims",
                                             "kind": "sql", "query": sql},
                         timeout=90.0)
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {r.text[:160]}"
            body = r.json() or {}
            if body.get("error"):
                return None, str(body["error"])[:200]
            return body.get("rows") or [], None

        rows, err = q("select count(*) n from claim_documents where file_url is not null")
        if err:
            return False, f"filed count: {err}"
        filed = int(rows[0]["n"])

        rows, err = q("""select content_sha256, count(*) n,
                                string_agg(document_id, ',' order by document_id) ids
                         from claim_documents where file_url is not null
                         group by content_sha256 having count(*) > 1""")
        if err:
            return False, f"duplicate scan: {err}"
        groups = rows or []

        bad, seen = [], [f"filed documents: {filed}",
                         f"byte-identical groups: {len(groups)}"]
        if filed < 100:
            bad.append(f"only {filed} document(s) carry a file — run "
                       f"generate_claim_documents.py --upload")
        if len(groups) != 1:
            bad.append(f"expected exactly 1 byte-identical group, found {len(groups)}"
                       + (" — a look-alike would read as a reused document on an "
                          "unrelated claim" if len(groups) > 1 else
                          " — the flagship duplicate is missing"))
        else:
            ids = sorted(str(groups[0]["ids"]).split(","))
            seen.append(f"the intended pair: {ids}")
            if ids != ["DOC-00000002", "DOC-NEEDLE-001"]:
                bad.append(f"the duplicate is {ids}, not the intended estimate pair")

        # The bytes must actually stream, and be the bytes the column claims.
        rows, err = q("select content_sha256 from claim_documents "
                      "where document_id = 'DOC-NEEDLE-001'")
        if err or not rows:
            bad.append(f"needle document row unreadable: {err}")
        else:
            want = rows[0]["content_sha256"]
            m = httpx.post(f"{args.mcp_url}/media", headers=mcp_headers, timeout=90.0,
                           json={"source_id": "insurance_claims",
                                 "dataset_id": "insurance_claims.claim_documents",
                                 "key_field": "document_id",
                                 "key_value": "DOC-NEEDLE-001", "column": "file_url"})
            if m.status_code != 200:
                bad.append(f"/media returned {m.status_code} — the document does "
                           f"not stream")
            else:
                got = hashlib.sha256(m.content).hexdigest()
                seen.append(f"/media streamed {len(m.content):,} bytes "
                            f"({m.headers.get('content-type')})")
                if m.content[:4] != b"%PDF":
                    bad.append("streamed bytes are not a PDF")
                if got != want:
                    bad.append("streamed bytes do not match content_sha256 — the "
                               "column and the object disagree")
        if bad:
            return False, "\n".join(bad + seen)
        return True, "\n".join(seen)

    run_step(h, 9.6, "documents — real files, exactly one deliberate duplicate",
             False, step9f)

    def step9c():
        """Every needle must be REACHABLE in the panel an officer opens.

        A queue is capped at 500 rows and sorts/searches in the BROWSER, so a
        needle outside the server-side window cannot be reached however the
        officer sorts — the case is in the database, returns from the MCP, and
        is still un-openable. Three separate causes were found this way: the
        window was cut on the wrong column, "open claims" never filtered out
        the 2,585 settled ones, and needle ids sorted last. Each looked
        identical from the outside: a healthy app with the case missing.
        """
        cases = [
            ("acme-bank-collections-priority", "priority_worklist",
             "loan_account_no", "LON-NEEDLE-002"),
            ("acme-bank-claim-triage", "open_claims", "claim_id", "CLM-NEEDLE-003"),
            ("acme-bank-claim-triage", "open_claims", "claim_id", "CLM-NEEDLE-004"),
            (DASHBOARD_APP, "unworked_leads", "lead_id", "LED-NEEDLE-005"),
        ]
        bad, seen = [], []
        for slug, panel, key, needle in cases:
            rr = httpx.get(f"{args.smart_app_url}/apps/{slug}/data/{panel}",
                           headers=svc_headers, timeout=120.0)
            if rr.status_code != 200:
                bad.append(f"{slug}/{panel}: HTTP {rr.status_code}")
                continue
            body = rr.json() or {}
            rows = body.get("rows") or []
            seen.append(f"{needle}: {len(rows)} rows, truncated={body.get('truncated')}")
            if needle not in [x.get(key) for x in rows]:
                bad.append(
                    f"{needle} NOT in {slug}/{panel} window "
                    f"({len(rows)} rows) — unreachable in the UI"
                )
        if bad:
            return False, "\n".join(bad + seen)
        return True, "\n".join(seen)

    run_step(h, 9.7, "needles — every demo case is reachable in its panel", True, step9c)

    def step9d():
        """Reject a recommendation and assert the app actually LEARNED from it.

        The runtime demands a reason and a reason code, and tells the officer
        "the AI learns from it" — so this step is the only thing standing
        between that promise and a lie. Two separate defects lived here
        undetected because nothing asserted the ledger afterwards:

          * every reject raised TypeError inside a broad except and wrote
            NOTHING (a stray kwarg on fold_decision_feedback);
          * the approval gate returns before facets are frozen, so what did get
            written was UNCODED — and consolidation can reinforce a judgement
            from uncoded evidence but never author one, which is the whole
            point of collecting it.

        So assert all three: the row exists, it carries the reason_code the
        officer picked, and case_facets is non-empty. Facets must also be free
        of ``__unknown`` families — a family the app can never populate matches
        only other unpopulated cases, so a judgement scoped on it can never fire.
        """
        # Uses loan triage, because a reject needs something TO reject: the run
        # must stage planned_writes. review_application proposes a credit
        # decision, so it stages one. The collections action does not — its agent
        # cannot know a call's outcome before the call is made, so it answers
        # conditionally and writes nothing (which is correct, just not testable
        # here). Same call the queue's row action makes (PanelRenderer → /run
        # with mode=queue_action), so this is the officer's real path.
        slug = "loan-application-triage"
        row = {"application_id": "LAN-NEEDLE-001", "customer_id": "CUS-NEEDLE-001",
               "product": "personal", "amount_requested": 1200000.0,
               "tenure_months": 48, "sourcing_channel": "branch",
               "income_proof_type": "payslip", "itr_declared_income": 620000.0,
               "foir_percent": 26.4, "status": "under_review"}
        rr = sa.post(f"/apps/{slug}/run",
                     json={"action": "review_application", "inputs": row,
                           "mode": "queue_action"},
                     timeout=600.0)
        if rr.status_code != 200:
            return False, f"run HTTP {rr.status_code}: {rr.text[:200]}"
        body = rr.json() or {}
        cid = body.get("correlation_id")
        status_ = body.get("status")
        if status_ != "pending_approval" or not cid:
            return False, f"expected pending_approval + correlation_id, got {status_!r} / {cid!r}"

        code = "income_not_corroborated"
        ar = sa.post(
            f"/apps/{slug}/run/{cid}/approve",
            json={"decision": "reject", "reason_code": code,
                  "decision_reason": ("E2E: filed return does not corroborate the "
                                      "declared income; refer for verification."),
                  "note": "e2e reject"},
            timeout=180.0)
        if ar.status_code != 200:
            return False, f"reject HTTP {ar.status_code}: {ar.text[:200]}"

        # The fold is deliberately non-fatal, so a failure is only visible here.
        run_id = cid.split(":")[0]
        found, deadline = None, time.time() + 20
        while time.time() < deadline and not found:
            cr = sa.get(f"/apps/{slug}/memory/export", timeout=90.0)
            if cr.status_code == 200:
                rows = ((cr.json() or {}).get("collections")
                        or {}).get("smartapp_corrections") or []
                for c in rows:
                    if str(c.get("correlation_id") or "").startswith(run_id):
                        found = c
                        break
            if not found:
                time.sleep(2)
        if not found:
            return False, (f"rejected {cid} with reason_code={code} and NO correction "
                           f"was recorded — the reject-learning loop is dead")
        problems = []
        if found.get("reason_code") != code:
            problems.append(f"reason_code {found.get('reason_code')!r} != {code!r}")
        facets = found.get("case_facets") or []
        if not facets:
            problems.append("case_facets EMPTY — correction is uncoded, so "
                            "consolidation can never author a judgement from it")
        dead = [f for f in facets if str(f).endswith(":__unknown")]
        if dead:
            problems.append(f"unpopulated facet famil(ies): {dead} — a judgement "
                            f"scoped on these can never fire")
        detail = (f"correction={found.get('correction_id')} "
                  f"reason_code={found.get('reason_code')} facets={facets}")
        if problems:
            return False, "\n".join(problems + [detail])
        return True, detail

    run_step(h, 9.8, "reject — the correction lands, coded, in the ledger", True, step9d)

    def step9e():
        """Consolidation turned the seeded evidence into TEAM judgements.

        Non-core: it asserts seed_memory.py has been run, which a bare data-plane
        bring-up has not necessarily done.

        The scope is what makes a judgement real. A clause scoped to the whole
        world fires on every case and is noise; one scoped to a family the app
        cannot populate fires on nothing while still rendering as "team · 3
        officers". So assert the EXACT scope each lesson was designed for, that
        three distinct officers stand behind it, and that the evidence is still
        linked — the Memory screen shows that provenance under every judgement.
        """
        expect = {
            DECISION_APPS[0]: ("risk_flag:dispute", "dispute_raised"),
            DECISION_APPS[1]: ("intimation_delay:gte_30", "late_intimation"),
            "loan-application-triage": ("income_proof:present",
                                        "income_not_corroborated"),
        }
        bad, seen = [], []
        for slug, (scope, code) in expect.items():
            rr = sa.get(f"/apps/{slug}/memory/clauses", timeout=90.0)
            if rr.status_code != 200:
                bad.append(f"{slug}: HTTP {rr.status_code}")
                continue
            body = rr.json()
            rows = body.get("clauses") if isinstance(body, dict) else body
            rows = [c for c in (rows or []) if c.get("reason_code") == code]
            if not rows:
                bad.append(f"{slug}: no judgement for reason_code={code} — "
                           f"run seed_memory.py --apply")
                continue
            c = rows[0]
            officers = {o for o in (c.get("support_officers") or [])}
            got_scope = sorted(c.get("scope_facets") or [])
            seen.append(f"{slug}: [{c.get('status')}] scope={got_scope} "
                        f"officers={len(officers)} \"{(c.get('text') or '')[:60]}…\"")
            if got_scope != [scope]:
                bad.append(f"{slug}: scope {got_scope} != ['{scope}'] — the "
                           f"judgement does not apply to the cases it was taught on")
            if any(str(f).endswith(":__unknown") for f in got_scope):
                bad.append(f"{slug}: scope contains an unpopulated family — "
                           f"this judgement can never fire")
            # `dissented` is a LIVE status, not a failure: consolidation flags a
            # pair that could co-fire under different reason codes so a human
            # adjudicates, and the clause keeps being injected meanwhile. What
            # would be wrong is a judgement that is retired/superseded, or one
            # that never earned team standing.
            if c.get("status") not in ("active", "dissented") or len(officers) < 3:
                bad.append(f"{slug}: status={c.get('status')} officers="
                           f"{len(officers)} — expected a live judgement with 3 "
                           f"distinct officers (promotion_min_officers)")
            if len(c.get("provenance") or []) < 3:
                bad.append(f"{slug}: provenance {c.get('provenance')} — the "
                           f"evidence behind the judgement is not linked")
        if bad:
            return False, "\n".join(bad + seen)
        return True, "\n".join(seen)

    run_step(h, 9.9, "memory — seeded evidence consolidated into team judgements",
             False, step9e)

    def step9g():
        """How much evidence does it take to form a team judgement?

        Deterministic, not statistical — the thresholds are configuration, so
        assert them rather than measure them. `promotion_min_officers` is the
        promise the Memory screen makes when it labels a judgement "team": that
        at least that many DIFFERENT officers said it. One officer's view is a
        candidate — used and labelled, never silently promoted — and the count
        must be of DISTINCT officers, or the same person disposing several cases
        would manufacture a consensus on their own.

        Non-core: it reads the seeded memory, which a bare data-plane bring-up
        does not have.
        """
        bad, seen = [], []
        for slug in (DECISION_APPS[0], "loan-application-triage"):
            ar = sa.get(f"/apps/{slug}", timeout=60.0)
            if ar.status_code != 200:
                bad.append(f"{slug}: spec HTTP {ar.status_code}")
                continue
            spec = (ar.json() or {}).get("app_spec") or {}
            want = int(((spec.get("case_signature") or {}).get("learning") or {})
                       .get("promotion_min_officers") or 0)
            if want < 2:
                bad.append(f"{slug}: promotion_min_officers={want} — a judgement "
                           f"would carry team standing on one person's say-so")
            cr = sa.get(f"/apps/{slug}/memory/clauses", timeout=90.0)
            if cr.status_code != 200:
                bad.append(f"{slug}: clauses HTTP {cr.status_code}")
                continue
            body = cr.json()
            rows = body.get("clauses") if isinstance(body, dict) else body
            for cl in (rows or []):
                officers = {o for o in (cl.get("support_officers") or [])}
                cid, st = cl.get("clause_id"), cl.get("status")
                seen.append(f"{slug}/{cid}: status={st} distinct_officers="
                            f"{len(officers)} (threshold {want})")
                if st in ("active", "dissented") and len(officers) < want:
                    bad.append(f"{slug}/{cid}: {st} on {len(officers)} officer(s), "
                               f"below the threshold of {want} — team standing "
                               f"that was never earned")
                if st == "candidate" and len(officers) >= want:
                    bad.append(f"{slug}/{cid}: still candidate with {len(officers)} "
                               f"officers — promotion did not happen")
        if bad:
            return False, "\n".join(bad + seen)
        return True, "\n".join(seen)

    run_step(h, 9.95, "memory — team standing requires 3 DISTINCT officers",
             False, step9g)

    # ── 10. discovery registration (non-core) ───────────────────────────────
    def step10():
        r = httpx.get(f"{args.discovery_url}/health", timeout=15.0)
        if r.status_code != 200:
            return False, f"discovery /health {r.status_code}"
        return True, f"discovery up: {json.dumps(r.json())[:160]}"

    run_step(h, 10, "discovery-service reachable", False, step10)

    # ── 11. memory state (non-core — informational before Phase G) ──────────
    def step11():
        sys.path.insert(0, str(REPO / "smart-app-service"))
        import os
        cwd = os.getcwd()
        os.chdir(REPO / "smart-app-service")
        try:
            from dotenv import load_dotenv
            load_dotenv(REPO / "smart-app-service" / ".env")
            from pymongo import MongoClient
            cli = MongoClient(os.environ["MONGO_URI"])
            db = cli[os.environ.get("MONGO_DB", "dev")]
            corr = db["smartapp_corrections"].count_documents({"app_slug": {"$regex": "^acme-bank"}})
            clauses = db["smartapp_clauses"].count_documents({"app_slug": {"$regex": "^acme-bank"}})
            cli.close()
            return True, (f"corrections={corr} judgements={clauses} "
                          f"({'seeded' if clauses else 'empty — run seed_memory.py'})")
        finally:
            os.chdir(cwd)

    run_step(h, 11, "memory — corrections and judgements for this tenant", False, step11)

    for c in (mcp, dd, sa):
        c.close()

    print("=" * 76)
    print("SUMMARY")
    print(f"{'Step':<6}{'Core':<7}{'Result':<12}Name")
    print("-" * 76)
    for r in h.results:
        print(f"{r.num:<6}{'yes' if r.core else 'no':<7}"
              f"{'PASS' if r.passed else ('FAIL' if r.core else 'INFO'):<12}{r.name}")
    print("-" * 76)
    ok = h.core_passed()
    print(f"Core steps: {'ALL PASS' if ok else 'FAILURES PRESENT'}")
    print("=" * 76)

    lines = ["# Acme Bank — middleware / data-plane E2E report", "",
             f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
             "| Step | Core | Result | Name | Detail |", "|---|---|---|---|---|"]
    for r in h.results:
        detail = (r.detail or "").replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| {r.num} | {'yes' if r.core else 'no'} | "
                     f"{'PASS' if r.passed else ('FAIL' if r.core else 'INFO')} | "
                     f"{r.name} | {detail} |")
    lines += ["", f"**Core steps: {'ALL PASS' if ok else 'FAILURES PRESENT'}**", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
