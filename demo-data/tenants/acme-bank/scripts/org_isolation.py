# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Does every surface honour the org boundary?

Dev holds BOTH tenants right now — acme-power (6 apps, 2 clauses, 6 corrections)
and acme-bank — which makes it the right place to prove isolation before a prod
cut-over puts them side by side there too.
"""
import sys, time, json
import httpx, jwt
sys.path.insert(0, r"C:/Github/Citra-AI/demo-data/tenants/acme-bank/scripts")
import acme_bank_e2e as E

SECRET = E._secret()
SA, DISC, DD, MCP = ("http://localhost:9100", "http://localhost:9010",
                     "http://localhost:8095", "http://localhost:18504")


def tok(user, org, depts, roles=("org_admin",)):
    now = int(time.time())
    return jwt.encode({"sub": user, "user_id": user, "email": user,
                       "tenant_id": org, "org_id": org, "dept_ids": list(depts),
                       "roles": list(roles), "service_account_admin_of": [],
                       "service_account_member_of": [], "iat": now,
                       "exp": now + 900, "iss": "Citra-AI"}, SECRET, algorithm="HS256")


BANK = tok("coo@acme-bank-demo.citra.ai", "acme-bank",
           ["lending", "collections", "claims", "sales_distribution", "central_ops"])
POWER = tok("asha@acme-power", "acme-power", ["operations", "field", "central_ops"])
fails = []

def check(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        {detail}")
    if not ok:
        fails.append(name)

# A/B — app listing must not cross tenants
for label, t, want, forbid in (("acme-bank user sees only acme-bank apps", BANK,
                                "acme-bank", "acme-power"),
                               ("acme-power user sees only acme-power apps", POWER,
                                "acme-power", "acme-bank")):
    r = httpx.get(f"{SA}/apps", headers={"Authorization": f"Bearer {t}"},
                  params={"scope": "all", "limit": 100}, timeout=60)
    slugs = [a.get("slug") for a in ((r.json() or {}).get("apps") or [])]
    leaked = [s for s in slugs if forbid in (s or "")]
    check(label, r.status_code == 200 and not leaked,
          f"HTTP {r.status_code}, {len(slugs)} app(s), leaked={leaked or 'none'}")

# C — the acme-bank dept-MCP must refuse an acme-power user
r = httpx.post(f"{MCP}/run_query",
               headers={"Authorization": f"Bearer {E._mcp_key()}", "X-User-JWT": POWER},
               json={"source_id": "loan_servicing", "kind": "sql",
                     "query": "select loan_account_no from delinquencies limit 1"},
               timeout=60)
body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
denied = r.status_code in (401, 403) or bool(body.get("error"))
check("acme-bank MCP refuses an acme-power user", denied,
      f"HTTP {r.status_code} {str(body.get('error') or r.text)[:110]}")

# D — data-discovery catalogue must be tenant-scoped
for label, t, forbid in (("catalogue for acme-bank excludes acme-power datasets",
                          BANK, "acme-power"),):
    r = httpx.get(f"{DD}/catalogue", headers={"Authorization": f"Bearer {t}"},
                  params={"limit": 500}, timeout=90)
    txt = r.text.lower()
    body = r.json() if r.status_code == 200 else {}
    # The key is `entries`. Looking for `datasets`/`items` returned 0 and made
    # this assertion pass on an empty list — a test that proves nothing because
    # it found nothing.
    items = body if isinstance(body, list) else (
        body.get("entries") or body.get("datasets") or body.get("items") or [])
    tenants = {str(x.get("tenant_id") or x.get("org_id")) for x in items
               if isinstance(x, dict)}
    # Must be NON-EMPTY and clean: an empty catalogue would pass the
    # "no acme-power" test while proving nothing at all.
    check(label, r.status_code == 200 and len(items) >= 16 and forbid not in txt,
          f"HTTP {r.status_code}, {len(items)} dataset(s) (expect >=16), "
          f"tenants={sorted(tenants) or 'n/a'}, contains '{forbid}': {forbid in txt}")

# D2 — a keyword search must not surface the other tenant either
r = httpx.get(f"{DD}/catalogue/search", headers={"Authorization": f"Bearer {BANK}"},
              params={"q": "customer", "top_k": 50}, timeout=90)
check("catalogue SEARCH for acme-bank excludes acme-power",
      r.status_code == 200 and "acme-power" not in r.text.lower(),
      f"HTTP {r.status_code}, {len(r.text)} bytes")

# E — learned memory must not cross tenants
r = httpx.get(f"{SA}/apps/acme-bank-collections-priority/memory/clauses",
              headers={"Authorization": f"Bearer {BANK}"}, timeout=60)
rows = (r.json() or {}).get("clauses") if isinstance(r.json(), dict) else r.json()
bad = [c for c in (rows or []) if c.get("tenant_id") not in (None, "acme-bank")]
check("clause store returns only acme-bank judgements", not bad,
      f"{len(rows or [])} clause(s), foreign={len(bad)}")

# F — an acme-power user must not read an acme-bank app's spec
r = httpx.get(f"{SA}/apps/acme-bank-collections-priority",
              headers={"Authorization": f"Bearer {POWER}"}, timeout=60)
check("acme-power user cannot read an acme-bank app spec",
      r.status_code in (401, 403, 404),
      f"HTTP {r.status_code} {r.text[:90]}")

print("\n" + ("ALL PASS — org boundary holds" if not fails
              else f"{len(fails)} FAILURE(S): {fails}"))
