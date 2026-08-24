# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end check of the acme-bank app layer on PROD."""
import json, sys, time
import httpx, jwt

SECRET = sys.stdin.read().strip()
SA = "http://localhost:9100"
MCP = "http://172.31.39.51:8504"
DEPTS = ["lending", "collections", "claims", "sales_distribution", "central_ops"]
fails = []

def tok(user, org, depts, roles=("org_admin",)):
    now = int(time.time())
    return jwt.encode({"sub": user, "user_id": user, "email": user, "tenant_id": org,
                       "org_id": org, "dept_ids": list(depts), "roles": list(roles),
                       "service_account_admin_of": [], "service_account_member_of": [],
                       "iat": now, "exp": now + 900, "iss": "Citra-AI"},
                      SECRET, algorithm="HS256")

def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        {detail}")
    if not ok:
        fails.append(name)

COO = tok("coo@acme-bank-demo.citra.ai", "acme-bank", DEPTS)

# apps visible to their own officers
for user, dept, want in (("collections-mum@acme-bank-demo.citra.ai", "collections",
                          "acme-bank-collections-priority"),
                         ("claims-motor@acme-bank-demo.citra.ai", "claims",
                          "acme-bank-claim-triage"),
                         ("sales-manager@acme-bank-demo.citra.ai", "sales_distribution",
                          "acme-bank-sales")):
    t = tok(user, "acme-bank", [dept], roles=("user",))
    r = httpx.get(f"{SA}/apps", headers={"Authorization": f"Bearer {t}"},
                  params={"scope": "all", "limit": 50}, timeout=60)
    slugs = [a.get("slug") for a in ((r.json() or {}).get("apps") or [])]
    leaked = [s for s in slugs if "acme-power" in (s or "")]
    check(f"{dept} officer sees {want}", want in slugs and not leaked,
          f"HTTP {r.status_code} sees={slugs} leaked={leaked or 'none'}")

# panel data actually flows from the prod MCP
r = httpx.get(f"{SA}/apps/acme-bank-collections-priority/data/priority_worklist",
              headers={"Authorization": f"Bearer {COO}"}, timeout=180)
b = r.json() if r.status_code == 200 else {}
rows = b.get("rows") or []
accts = [x.get("loan_account_no") for x in rows]
check("collections worklist returns live rows", r.status_code == 200 and len(rows) > 0,
      f"HTTP {r.status_code} rows={len(rows)} needle={'LON-NEEDLE-002' in accts}")

r = httpx.get(f"{SA}/apps/acme-bank-claim-triage/data/open_claims",
              headers={"Authorization": f"Bearer {COO}"}, timeout=180)
b = r.json() if r.status_code == 200 else {}
rows = b.get("rows") or []
ids = [x.get("claim_id") for x in rows]
check("open claims filtered + needles reachable",
      r.status_code == 200 and len(rows) > 0
      and "CLM-NEEDLE-004" in ids and "CLM-NEEDLE-003" in ids,
      f"HTTP {r.status_code} rows={len(rows)} truncated={b.get('truncated')}")

# SOPs retrievable
r = httpx.get(f"{SA}/apps/acme-bank-claim-triage/data/policy_library",
              headers={"Authorization": f"Bearer {COO}"}, timeout=180)
b = r.json() if r.status_code == 200 else {}
check("SOP corpus retrievable", r.status_code == 200 and len(b.get("rows") or []) > 0,
      f"HTTP {r.status_code} rows={len(b.get('rows') or [])} error={b.get('error')}")

# learned memory present and scoped
for slug, scope in (("acme-bank-collections-priority", "risk_flag:dispute"),
                    ("acme-bank-claim-triage", "intimation_delay:gte_30")):
    r = httpx.get(f"{SA}/apps/{slug}/memory/clauses",
                  headers={"Authorization": f"Bearer {COO}"}, timeout=90)
    body = r.json()
    rows = body.get("clauses") if isinstance(body, dict) else body
    cl = (rows or [{}])[0]
    offs = {o for o in (cl.get("support_officers") or [])}
    check(f"{slug} judgement active on 3 officers",
          cl.get("status") == "active" and len(offs) >= 3
          and (cl.get("scope_facets") or []) == [scope],
          f"status={cl.get('status')} officers={len(offs)} scope={cl.get('scope_facets')}")

# documents stream through the MCP
r = httpx.get(f"{SA}/apps/acme-bank-claim-triage/data/claim_documents",
              headers={"Authorization": f"Bearer {COO}"},
              params={"id": "CLM-NEEDLE-003"}, timeout=120)
b = r.json() if r.status_code == 200 else {}
check("claim documents panel resolves", r.status_code == 200,
      f"HTTP {r.status_code} rows={len(b.get('rows') or [])}")

print("\n" + ("ALL PASS - acme-bank app layer live on prod" if not fails
              else f"{len(fails)} FAILURE(S): {fails}"))
