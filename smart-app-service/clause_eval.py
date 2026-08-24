# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Clause-memory evaluation — the per-app ship gate.
docs/clause-memory-graph-plan.md §13.

Without this we cannot tell whether clause memory HELPED. The whole design
argument (selection beats compression) is a hypothesis until it is measured
against the officers' own past decisions.

Method — leave-one-out over the app's own history:

  * take the last M corrections (the app's disputed cases — the only ones where
    we know the officer disagreed with the AI);
  * for each, build the clause set **excluding that case's own evidence**, so a
    clause never gets credit for a case it was literally derived from. Without
    this exclusion the score is circular and always ~1.0;
  * ask: would the clauses that fire on this case's facets have raised the very
    issue the officer raised (same reason_code / contested field)?

That is a RETRIEVAL question, answerable offline with no LLM and no model call.
It does not prove the model would have decided differently — only that the
relevant learned rule would have been in front of it. A weaker claim than
end-to-end agreement, and it is stated that way deliberately: an offline proxy
oversold as an outcome metric is worse than no metric.

`mean_prompt_words` is the one directly actionable number: how much prompt the
learned layer actually costs per case now that it is selected rather than
compressed.

Usage:
    python clause_eval.py --app acme-power
    python clause_eval.py --app acme-power --holdout 200 --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("clause_eval")

#: A clause set that cannot beat this on the holdout is not worth switching to.
SHIP_GATE_COVERAGE = float(os.getenv("CLAUSE_SHIP_GATE_COVERAGE", "0.5"))
#: Minimum holdout size before the numbers mean anything. Below this the gate
#: reports "insufficient data" rather than a flattering small-sample score —
#: the same suppression discipline the memory-lift cohorts use.
MIN_HOLDOUT = int(os.getenv("CLAUSE_EVAL_MIN_HOLDOUT", "20"))


def _relevant(clause: Dict[str, Any], correction: Dict[str, Any]) -> bool:
    """Would this clause have raised the officer's actual objection?

    Same reason_code is the primary signal (it IS the lesson category). A
    contested-field overlap counts too: a clause about `police_report_no` is
    relevant to a rejection contesting that field even if the codes differ."""
    if clause.get("reason_code") and clause["reason_code"] == correction.get("reason_code"):
        return True
    cf = set(clause.get("contested_fields") or [])
    return bool(cf and cf & set(correction.get("contested_fields") or []))


def _fires(clause: Dict[str, Any], case_facets: Sequence[str]) -> bool:
    """The §6 containment test, in-process (no Mongo round trip per case)."""
    return set(clause.get("scope_facets") or []) <= set(case_facets or [])


async def evaluate_app(
    *,
    tenant_id: str,
    app_slug: str,
    modality: str = "record",
    task_type: str = "decision",
    holdout: int = 100,
) -> Dict[str, Any]:
    """Replay the app's disputed history against its clause set."""
    import clause_store as cs
    import corrections as cx

    col = cx._col()
    cases = await col.find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "modality": modality, "task_type": task_type},
        {"_id": 0},
    ).sort("at", -1).to_list(holdout)

    clauses = await cs._col().find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "modality": modality, "task_type": task_type,
         "status": {"$in": list(cs.LIVE_STATUSES)}},
        {"_id": 0},
    ).to_list(5000)

    result: Dict[str, Any] = {
        "app_slug": app_slug, "modality": modality, "task_type": task_type,
        "holdout": len(cases), "clauses_total": len(clauses),
    }

    if len(cases) < MIN_HOLDOUT:
        result["verdict"] = "insufficient_data"
        result["note"] = (
            f"only {len(cases)} disputed case(s); need {MIN_HOLDOUT} before a "
            "gate number means anything")
        return result

    covered = 0
    fired_counts: List[int] = []
    word_counts: List[int] = []
    facetless = 0

    for c in cases:
        facets = c.get("case_facets") or []
        if not facets:
            facetless += 1
        cid = c.get("correction_id")

        # LEAVE-ONE-OUT: a clause this case's own evidence produced cannot be
        # credited with predicting it. Without this the score is circular.
        eligible = [cl for cl in clauses if cid not in (cl.get("provenance") or [])]

        firing = [cl for cl in eligible if _fires(cl, facets)]
        picked, dissented = cs.rank_and_budget(firing, budget_words=1000)
        fired_counts.append(len(picked))
        word_counts.append(len(cs.render_block(picked, dissented).split()))

        if any(_relevant(cl, c) for cl in picked):
            covered += 1

    n = len(cases)
    coverage = covered / n
    result.update({
        "coverage": round(coverage, 3),
        "covered": covered,
        "mean_clauses_fired": round(sum(fired_counts) / n, 2),
        "mean_prompt_words": round(sum(word_counts) / n, 1),
        "facetless_cases": facetless,
    })

    reasons: List[str] = []
    if coverage < SHIP_GATE_COVERAGE:
        reasons.append(
            f"coverage {coverage:.0%} is below the {SHIP_GATE_COVERAGE:.0%} gate — "
            "the clause set does not yet surface the issues officers actually raise")
    if facetless > n * 0.5:
        reasons.append(
            f"{facetless}/{n} cases have NO facets (backfilled or no case_signature) "
            "— clauses cannot be scoped or routed for them")
    if not clauses:
        reasons.append("no active clauses exist yet")

    result["verdict"] = "pass" if not reasons else "hold"
    result["blockers"] = reasons
    result["measures"] = (
        "coverage = share of disputed cases where a scope-matching clause "
        "addressed the officer's own reason_code or contested field, with that "
        "case's own evidence EXCLUDED. It shows the right rule would have been "
        "in front of the model — NOT that the model would have decided "
        "differently. Do not report it as an accuracy number."
    )
    return result


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app", required=True)
    ap.add_argument("--tenant", help="defaults to the app's own org")
    ap.add_argument("--modality", default="record")
    ap.add_argument("--task-type", default="decision")
    ap.add_argument("--holdout", type=int, default=100)
    ap.add_argument("--env", choices=["prod", "test"], default="prod")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    import main
    from env_context import set_current_env

    set_current_env(args.env)
    if main._db is None:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(os.environ["MONGO_URI"])
        main._db = client[os.environ.get("MONGO_DB", "dev")]

    tenant = args.tenant
    if not tenant:
        from analysis_rubrics import rubric_tenant_for_app

        doc = await main._db["smartapp_apps"].find_one(
            {"slug": args.app}, {"_id": 0, "app_spec": 1, "tenant_id": 1, "org_id": 1})
        tenant = rubric_tenant_for_app(doc)
    if not tenant:
        print(f"could not resolve a tenant for {args.app} — pass --tenant")
        return 2

    res = await evaluate_app(
        tenant_id=tenant, app_slug=args.app, modality=args.modality,
        task_type=args.task_type, holdout=args.holdout)

    if args.json:
        print(json.dumps(res, indent=2))
        return 0

    print(f"\nClause-memory eval — {args.app} ({args.modality}/{args.task_type})")
    print(f"  verdict            {res['verdict'].upper()}")
    for k in ("holdout", "clauses_total", "coverage", "covered",
              "mean_clauses_fired", "mean_prompt_words",
              "facetless_cases"):
        if k in res:
            print(f"  {k:26} {res[k]}")
    for b in res.get("blockers") or []:
        print(f"  BLOCKER: {b}")
    if res.get("note"):
        print(f"  NOTE: {res['note']}")
    print(f"\n  {res.get('measures') or ''}")
    return 0 if res.get("verdict") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
