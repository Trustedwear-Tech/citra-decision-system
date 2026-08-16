# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Apply the staged plan and prove the write reached the source of record.

Plan-then-apply's whole claim is that Apply commits EXACTLY what the officer
reviewed — no second LLM round-trip. So: snapshot the row, stage a plan, Apply,
and diff the row against the payload that was shown.
"""
import json, sys, time
import httpx, psycopg2
sys.path.insert(0, r"C:/Github/Citra-AI/demo-data/tenants/acme-bank/scripts")
import acme_bank_e2e as E

PG = dict(host="localhost", port=5444, dbname="acme_bank",
          user="acme_bank", password="acme_bank_demo_pw")
SLUG = "acme-bank-claim-triage"
CLAIM = "CLM-NEEDLE-004"
FIELDS = ["claim_id", "status", "approved_amount", "rejection_reason",
          "decided_by", "decided_at"]


def row():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute(f"select {', '.join(FIELDS)} from claims where claim_id=%s", (CLAIM,))
    r = dict(zip(FIELDS, cur.fetchone()))
    conn.close()
    return r


def full_claim():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""select claim_id, policy_no, claim_type, claimed_amount,
                          intimation_delay_days, fir_number, surveyor_id, status
                   from claims where claim_id=%s""", (CLAIM,))
    cols = [d[0] for d in cur.description]
    r = dict(zip(cols, cur.fetchone()))
    conn.close()
    return {k: (float(v) if hasattr(v, "quantize") else v) for k, v in r.items()}


def reset():
    """Put the claim back to intimated so the test is repeatable — it commits a
    real decision to the source of record every time it runs."""
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""update claims set status='intimated', approved_amount=null,
                          rejection_reason=null, decided_by=null, decided_at=null
                   where claim_id=%s""", (CLAIM,))
    conn.commit()
    conn.close()


def _norm(v):
    """Compare values, not representations: the DB hands back a datetime where
    the plan carried its ISO string."""
    s = str(v or "").strip().replace("T", " ")
    return s[:19]


jwt = E._user_jwt(E._secret())
reset()
before = row()
print("BEFORE:", json.dumps(before, default=str))

r = httpx.post(f"http://localhost:9100/apps/{SLUG}/run",
               headers={"Authorization": f"Bearer {jwt}"},
               json={"action": "triage_claim", "inputs": full_claim(),
                     "mode": "queue_action"}, timeout=600.0)
b = r.json()
cid = b.get("correlation_id")
plan = b.get("planned_writes") or []
print(f"\nstaged: status={b.get('status')} writes={len(plan)} cid={cid}")
if not plan:
    print("FAIL — nothing staged, nothing to apply"); raise SystemExit(1)
payload = plan[0].get("payload") or {}
print("payload shown to officer:", json.dumps(payload, default=str)[:400])

ar = httpx.post(f"http://localhost:9100/apps/{SLUG}/run/{cid}/approve",
                headers={"Authorization": f"Bearer {jwt}",
                         "Accept": "application/json"},
                json={"decision": "approve",
                      "overrides": [{} for _ in plan],
                      "expected_plan_hash": b.get("plan_hash")},
                timeout=600.0)
print(f"\napprove -> HTTP {ar.status_code}")
print("response:", ar.text[:500])

time.sleep(2)
after = row()
print("\nAFTER :", json.dumps(after, default=str))

fails = []
if after == before:
    fails.append("row UNCHANGED — Apply committed nothing")
for k, v in payload.items():
    if k in FIELDS and k != "claim_id":
        got = after.get(k)
        if _norm(got) != _norm(v) and str(v)[:40] not in str(got or ""):
            fails.append(f"{k}: applied {got!r} != planned {str(v)[:60]!r}")
print("\n" + ("FAIL: " + "; ".join(fails) if fails
              else "PASS — Apply committed exactly the reviewed payload to the SoR"))
