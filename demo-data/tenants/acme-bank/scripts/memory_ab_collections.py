# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""The strong memory test, unlocked by the approval-gate fix.

A disputed account. The SOP says stop collection on a dispute; the officers'
judgement C-001 says the same in their words. Run the real agent twice — once
with the judgement active, once retired — and see whether the RECOMMENDED ACTION
changes. Unlike the loan case, the two answers here are behaviourally opposite:
route to servicing vs work the account.
"""
import argparse, asyncio, json, os, pathlib, sys, time
import httpx, psycopg2

SVC = pathlib.Path(r"C:/Github/Citra-AI/smart-app-service")
sys.path.insert(0, r"C:/Github/Citra-AI/demo-data/tenants/acme-bank/scripts")
import acme_bank_e2e as E

SLUG = "acme-bank-collections-priority"
PG = dict(host="localhost", port=5444, dbname="acme_bank",
          user="acme_bank", password="acme_bank_demo_pw")


def case():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""select loan_account_no, bucket, dpd, overdue_amount,
                          last_paid_on, risk_flag from delinquencies
                   where risk_flag='dispute' order by dpd desc limit 1""")
    cols = [d[0] for d in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    conn.close()
    return {k: (float(v) if hasattr(v, "quantize")
                else str(v) if hasattr(v, "isoformat") else v)
            for k, v in row.items()}


async def set_status(status: str) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv(SVC / ".env")
    c = AsyncIOMotorClient(os.environ["MONGO_URI"])
    db = c[os.environ.get("MONGO_DB", "dev")]
    r = await db["smartapp_clauses"].update_many(
        {"app_slug": SLUG, "tenant_id": "acme-bank"}, {"$set": {"status": status}})
    c.close()
    return r.modified_count


def run(jwt, row, label):
    t0 = time.time()
    r = httpx.post(f"http://localhost:9100/apps/{SLUG}/run",
                   headers={"Authorization": f"Bearer {jwt}"},
                   json={"action": "log_collection_activity", "inputs": row,
                         "mode": "queue_action"}, timeout=600.0)
    b = r.json()
    refs = b.get("references") or {}
    return {"label": label, "secs": round(time.time() - t0, 1),
            "injected": refs.get("injected_clause_ids"),
            "cited": b.get("cited_clauses"),
            "decision": b.get("decision"),
            "reasoning": (b.get("reasoning") or "")[:700]}


async def main(pairs: int):
    jwt = E._user_jwt(E._secret())
    row = case()
    print(f"case: {row['loan_account_no']}  risk_flag={row['risk_flag']} "
          f"bucket={row['bucket']} dpd={row['dpd']}\n")
    results = []
    for i in range(1, pairs + 1):
        await set_status("active")
        a = run(jwt, row, f"pair{i}_with_memory")
        await set_status("retired")
        b = run(jwt, row, f"pair{i}_without_memory")
        results += [a, b]
        for o in (a, b):
            print(f"[{o['label']}] ({o['secs']}s) injected={o['injected']}")
            print(f"    decision: {o['decision']}")
        print()
    await set_status("active")
    print("restored -> active")
    pathlib.Path("collections_ab.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=2)
    raise SystemExit(asyncio.run(main(ap.parse_args().pairs)))
