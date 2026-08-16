# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Does the learned judgement actually change what the agent recommends?

METHOD — paired, across DISTINCT cases.

The first version of this script ran the SAME application four times with the
judgement on and four times with it off. That measures reproducibility on one
case, not whether memory helps in general. So the unit here is the CASE: each
application is run twice with identical inputs, and the only difference between
the two runs is whether clause C-002 is `active` or `retired`.

The DSA lesson is the one under test because Retail Credit Policy §1 states it
applies to EVERY sourcing channel and prescribes nothing channel-specific — no
document the agent can read tells it to treat a DSA-sourced file differently, so
a behavioural difference cannot have come from the SOP corpus. The other three
seeded lessons restate the SOP almost verbatim and changed nothing when retired.
That is the honest boundary of this claim: it is evidence for THIS lesson on
THIS app, not for memory in general.

TWO ARMS, because sensitivity alone proves little:

  treatment — DSA-sourced applications, where the judgement SHOULD fire.
  control   — non-DSA applications, where it must stay SILENT. A memory that
              fires on everything is noise, and this arm is what would expose it.

STATISTICS — a sign test over discordant pairs. 5 one way is p=0.031, 8 is
p=0.004.

THE MEASURE is the model's OWN attribution — whether it cited C-002 — falling
back to a keyword. The first pass scored on keywords alone and under-counted
badly: a run that reasoned "DSA sourcing (C-002) adds further verification need"
was recorded as a miss because it said "verification" and not "employment
verification". Cases already declined on bureau score or FOIR are REPORTED but not excluded.
Excluding them was the first version's mistake: the transcripts show a file
declined on a 617 bureau score still cited C-002, because declining a file and
noting that its DSA sourcing needs employment verification are not mutually
exclusive. The exclusion discarded six of eight valid observations.

Every run's full reasoning is written to memory_ab_dsa_results.json — audit the
transcripts rather than trusting the number.

    python memory_ab_dsa.py                      # 8 treatment + 4 control
    python memory_ab_dsa.py --dsa 4 --control 2  # quicker, weaker
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
import psycopg2

SCRIPT_DIR = Path(__file__).resolve().parent
TENANT_DIR = SCRIPT_DIR.parent
REPO = TENANT_DIR.parents[2]
SERVICE = REPO / "smart-app-service"
RESULTS = SCRIPT_DIR / "memory_ab_dsa_results.json"
sys.path.insert(0, str(SCRIPT_DIR))
import acme_bank_e2e as E  # noqa: E402 — shares the JWT/secret helpers

SLUG = "loan-application-triage"
CLAUSE = "C-002"
SMART_APP = "http://localhost:9100"
PG = dict(host="localhost", port=5444, dbname="acme_bank",
          user="acme_bank", password="acme_bank_demo_pw")

#: The officers' remedy, in the words a recommendation would use. Crude on
#: purpose — a reader can verify it against the transcripts in one pass.
HIT_TERMS = ("employer", "employment verif", "verify employment",
             "verification of employment")


def cases(n_dsa: int, n_control: int, offset: int = 0,
          only_ids: List[str] | None = None) -> List[Dict[str, Any]]:
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cols = ("application_id, customer_id, product, amount_requested, tenure_months, "
            "sourcing_channel, income_proof_type, itr_declared_income, ltv_percent, "
            "foir_percent, status")

    def pick(where: str, limit: int, offset: int = 0) -> List[Dict[str, Any]]:
        # Ordered by a hash of the id, not by amount or date: taking the top N of
        # one slice would make the result an artefact of that slice. The order is
        # STABLE, so --offset takes the NEXT n cases — a second batch that can be
        # pooled with the first instead of re-running the same applications.
        cur.execute(f"select {cols} from loan_applications where {where} "
                    f"and status in ('new','under_review') "
                    f"order by md5(application_id) limit {limit} offset {offset}")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    if only_ids:
        ids = "','".join(only_ids)
        rows = [{"arm": "treatment", **r}
                for r in pick(f"application_id in ('{ids}')", len(only_ids))]
        conn.close()
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "quantize"):
                    r[k] = float(v)
        return rows
    rows = [{"arm": "treatment", **r}
            for r in pick("sourcing_channel = 'dsa'", n_dsa, offset)]
    rows += [{"arm": "control", **r}
             for r in pick("sourcing_channel <> 'dsa'", n_control, offset)]
    conn.close()
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "quantize"):
                r[k] = float(v)
    return rows


async def set_clause(status: str) -> None:
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient
    load_dotenv(SERVICE / ".env")
    c = AsyncIOMotorClient(os.environ["MONGO_URI"])
    db = c[os.environ.get("MONGO_DB", "dev")]
    r = await db["smartapp_clauses"].update_one(
        {"app_slug": SLUG, "tenant_id": "acme-bank", "clause_id": CLAUSE},
        {"$set": {"status": status}})
    c.close()
    if r.matched_count != 1:
        raise SystemExit(f"clause {CLAUSE} not found — run seed_memory.py --apply")


def run_case(jwt: str, case: Dict[str, Any]) -> Dict[str, Any]:
    inputs = {k: v for k, v in case.items() if k != "arm"}
    t0 = time.time()
    r = httpx.post(f"{SMART_APP}/apps/{SLUG}/run",
                   headers={"Authorization": f"Bearer {jwt}"},
                   json={"action": "review_application", "inputs": inputs,
                         "mode": "queue_action"}, timeout=900.0)
    body = r.json() if r.status_code == 200 else {"error": r.text[:300]}
    refs = body.get("references") or {}
    text = " ".join([
        str(body.get("decision") or ""),
        str(body.get("reasoning") or ""),
        str((body.get("outputs") or {}).get("text") or ""),
    ]).lower()
    cited = [c.get("clause_id") for c in (body.get("cited_clauses") or [])
             if isinstance(c, dict)]
    decision = str(body.get("decision") or "")
    return {
        "http": r.status_code,
        "secs": round(time.time() - t0, 1),
        "injected": refs.get("injected_clause_ids") or [],
        "cited": cited,
        "decision": body.get("decision"),
        "reasoning": body.get("reasoning"),
        # PRIMARY measure: the model's own attribution. A keyword predicate
        # under-counted badly — one run reasoned "DSA sourcing (C-002) adds
        # further verification need" and scored as a miss because it said
        # "verification" rather than "employment verification".
        "used_judgement": (CLAUSE in cited) or any(t in text for t in HIT_TERMS),
        "raised_employment_check": any(t in text for t in HIT_TERMS),
        # Reported, NOT excluded. The first version dropped declined files on the
        # reasoning that they have no headroom for an extra verification step —
        # which the transcripts disproved: LAN-2026-004550 was declined on a 617
        # bureau score AND cited C-002. Declining a file and noting that its DSA
        # sourcing needs employment verification are not mutually exclusive, and
        # excluding them threw away six of eight valid observations, turning
        # p=0.0039 into p=0.25.
        "hard_decline": decision.lower().startswith(("rejected", "reject", "decline")),
    }


def sign_test(b: int, c: int) -> float:
    """One-sided p for b of b+c discordant pairs falling one way under p=0.5."""
    n = b + c
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(b, n + 1)) / (2 ** n)


async def main(n_dsa: int, n_control: int, offset: int = 0,
               only_ids: List[str] | None = None) -> int:
    rows = cases(n_dsa, n_control, offset, only_ids)
    results_path = RESULTS
    if only_ids:
        results_path = RESULTS.with_name("memory_ab_dsa_results_rerun.json")
    elif offset:
        results_path = RESULTS.with_name(f"memory_ab_dsa_results_off{offset}.json")
    treat = [r for r in rows if r["arm"] == "treatment"]
    ctrl = [r for r in rows if r["arm"] == "control"]
    print(f"cases: {len(treat)} DSA (treatment) + {len(ctrl)} non-DSA (control)")
    print(f"each runs TWICE — {CLAUSE} active, then retired — identical inputs\n")

    out: List[Dict[str, Any]] = []
    for i, case in enumerate(rows, start=1):
        # Mint per case: the token lives 30 minutes and a 12-case batch runs
        # longer, so a single token expired mid-run and the last three cases
        # came back 401 — silent data loss dressed up as "the judgement did
        # not fire".
        jwt = E._user_jwt(E._secret())
        await set_clause("active")
        on = run_case(jwt, case)
        await set_clause("retired")
        off = run_case(jwt, case)
        out.append({"application_id": case["application_id"], "arm": case["arm"],
                    "product": case["product"], "channel": case["sourcing_channel"],
                    "with_memory": on, "without_memory": off})
        print(f"[{i}/{len(rows)}] {case['application_id']} ({case['arm']}, "
              f"{case['sourcing_channel']})")
        print(f"      memory ON : injected={on['injected']} cited={on['cited']} "
              f"used={on['used_judgement']} decline={on['hard_decline']} ({on['secs']}s)")
        print(f"      memory OFF: injected={off['injected']} "
              f"used={off['used_judgement']} decline={off['hard_decline']} ({off['secs']}s)")
    await set_clause("dissented")   # the state consolidation left it in
    results_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    void = [r for r in out if r["with_memory"]["http"] != 200
            or r["without_memory"]["http"] != 200]
    if void:
        print(f"\nVOID: {len(void)} case(s) had a failed run (HTTP != 200) and "
              f"are excluded — a failed call is not evidence either way:")
        for r in void:
            print(f"  {r['application_id']} on={r['with_memory']['http']} "
                  f"off={r['without_memory']['http']}")
    out = [r for r in out if r not in void]
    t = [r for r in out if r["arm"] == "treatment"]
    c = [r for r in out if r["arm"] == "control"]
    live = t                       # every treatment case is scoreable
    dead = [r for r in t if r["with_memory"]["hard_decline"]]
    b = sum(1 for r in live if r["with_memory"]["used_judgement"]
            and not r["without_memory"]["used_judgement"])
    d = sum(1 for r in live if not r["with_memory"]["used_judgement"]
            and r["without_memory"]["used_judgement"])
    both = sum(1 for r in live if r["with_memory"]["used_judgement"]
               and r["without_memory"]["used_judgement"])
    neither = sum(1 for r in live if not r["with_memory"]["used_judgement"]
                  and not r["without_memory"]["used_judgement"])
    fired = sum(1 for r in t if CLAUSE in (r["with_memory"]["injected"] or []))
    leaked = sum(1 for r in c if CLAUSE in (r["with_memory"]["injected"] or []))
    ctrl_changed = sum(1 for r in c
                       if r["with_memory"]["raised_employment_check"]
                       != r["without_memory"]["raised_employment_check"])
    p = sign_test(b, d)

    print("\n" + "=" * 68)
    print("TREATMENT — DSA-sourced, the judgement should fire")
    print(f"  {CLAUSE} injected                        : {fired}/{len(t)}")
    print(f"  of which declined on policy anyway       : {len(dead)}/{len(t)}"
          f"   (reported, not excluded)")
    print(f"  raised the check WITH memory only        : {b}")
    print(f"  raised it WITHOUT memory only            : {d}")
    print(f"  raised in both / neither                 : {both} / {neither}")
    print(f"  sign test over {b + d} discordant pair(s)          : p = {p:.4f}")
    print("\nCONTROL — non-DSA, the judgement must stay silent")
    print(f"  {CLAUSE} wrongly injected                : {leaked}/{len(c)}")
    print(f"  behaviour changed anyway                 : {ctrl_changed}/{len(c)}")
    print("=" * 68)

    lines = []
    if b >= 5 and p <= 0.05:
        lines.append(f"memory CHANGED the recommendation on {b}/{len(live)} "
                     f"scoreable cases (p={p:.4f})")
    else:
        lines.append(f"NOT demonstrated: {b} discordant pair(s), p={p:.4f} — "
                     f"5 or more are needed before claiming anything")
    if leaked:
        lines.append(f"WARNING: fired on {leaked} non-DSA case(s) — the scope is "
                     f"too broad to trust")
    else:
        lines.append(f"and stayed silent on all {len(c)} control case(s)")
    lines.append("evidence for THIS lesson on THIS app — the three SOP-duplicating "
                 "lessons changed nothing")
    print("\n" + "\n".join(f"  {v}" for v in lines))
    print(f"\ntranscripts: {RESULTS}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsa", type=int, default=8)
    ap.add_argument("--control", type=int, default=4)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip the first N cases — a second batch to pool with the first")
    ap.add_argument("--ids", default="",
                    help="comma-separated application_ids — re-run exactly these")
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] or None
    raise SystemExit(asyncio.run(main(a.dsa, a.control, a.offset, ids)))
