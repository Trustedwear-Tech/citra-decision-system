# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Does the learned memory make the RECOMMENDATION sharper?

The retrieval eval (clause_eval.py) answers "was the right rule in front of the
model" — deliberately a weaker claim. This runs the real agent twice on the SAME
case, once with the team judgement active and once with it retired, and diffs
what the model actually recommended. Nothing else changes between the runs.

The case is LAN-NEEDLE-001: a clean file whose declared income the tax filing
does not corroborate. The SOP does NOT cover this — it checks a Form 16 is
authentic and never reconciles filed vs declared — so if the run WITHOUT memory
raises it anyway, memory is not what produced the improvement and the test says
so rather than claiming a win.
"""
import argparse, asyncio, json, os, pathlib, sys, time
import httpx, psycopg2

SVC = pathlib.Path(r"C:/Github/Citra-AI/smart-app-service")
sys.path.insert(0, str(SVC))
sys.path.insert(0, r"C:/Github/Citra-AI/demo-data/tenants/acme-bank/scripts")
import acme_bank_e2e as E

SLUG = "loan-application-triage"
SA = "http://localhost:9100"
PG = dict(host="localhost", port=15444, dbname="acme_bank",
          user="acme_bank", password="acme_bank_demo_pw")


def case_row():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""select application_id, customer_id, product, amount_requested,
                          tenure_months, sourcing_channel, income_proof_type,
                          itr_declared_income, ltv_percent, foir_percent, status
                   from loan_applications where application_id='LAN-NEEDLE-001'""")
    cols = [d[0] for d in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    conn.close()
    return {k: (float(v) if hasattr(v, "quantize") else v) for k, v in row.items()}


async def set_clause_status(status: str) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv(SVC / ".env")
    c = AsyncIOMotorClient(os.environ["MONGO_URI"])
    db = c[os.environ.get("MONGO_DB", "dev")]
    r = await db["smartapp_clauses"].update_many(
        {"app_slug": SLUG, "tenant_id": "acme-bank"}, {"$set": {"status": status}})
    c.close()
    return r.modified_count


def run_once(jwt: str, row: dict, label: str) -> dict:
    t0 = time.time()
    r = httpx.post(f"{SA}/apps/{SLUG}/run",
                   headers={"Authorization": f"Bearer {jwt}"},
                   json={"action": "review_application", "inputs": row,
                         "mode": "queue_action"}, timeout=600.0)
    body = r.json() if r.status_code == 200 else {"error": r.text[:300]}
    refs = body.get("references") or {}
    out = {
        "label": label,
        "http": r.status_code,
        "secs": round(time.time() - t0, 1),
        "status": body.get("status"),
        "injected_clause_ids": refs.get("injected_clause_ids"),
        "case_facets": refs.get("case_facets"),
        "cited_clauses": body.get("cited_clauses"),
        "decision": body.get("decision"),
        "reasoning": (body.get("reasoning") or "")[:1500],
        "text": ((body.get("outputs") or {}).get("text") or "")[:1500],
        "planned_writes": len(body.get("planned_writes") or []),
    }
    return out


async def main(keep: bool) -> int:
    jwt = E._user_jwt(E._secret())
    row = case_row()
    print(f"case: {row['application_id']}  product={row['product']} "
          f"amount={row['amount_requested']} proof={row['income_proof_type']} "
          f"itr_declared={row['itr_declared_income']}\n")

    n = await set_clause_status("active")
    print(f"[A] memory ON  ({n} clause(s) active)")
    with_mem = run_once(jwt, row, "with_memory")

    n = await set_clause_status("retired")
    print(f"[B] memory OFF ({n} clause(s) retired)")
    without = run_once(jwt, row, "without_memory")

    if not keep:
        await set_clause_status("active")
        print("restored clause status -> active")

    pathlib.Path("memory_ab_result.json").write_text(
        json.dumps([with_mem, without], indent=2, default=str), encoding="utf-8")
    for o in (with_mem, without):
        print(f"\n=== {o['label']} ({o['secs']}s, http={o['http']}, "
              f"status={o['status']}) ===")
        print(f"  injected_clause_ids: {o['injected_clause_ids']}")
        print(f"  cited_clauses      : {o['cited_clauses']}")
        print(f"  planned_writes     : {o['planned_writes']}")
        print(f"  decision           : {o['decision']}")
        print(f"  reasoning          : {o['reasoning'][:700]}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-retired", action="store_true")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.keep_retired)))
