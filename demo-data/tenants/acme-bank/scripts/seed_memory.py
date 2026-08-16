# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Seed the officer corrections that give the `acme-bank` demo tenant a LEARNED
MEMORY, then let the real consolidation pass turn them into judgements.

Adapted from the acme-power script, and it keeps that script's two hard rules
because both were learned the expensive way.

What this does NOT do:
    It never writes a judgement. Clauses are formed by `consolidation` from the
    evidence, exactly as in production. Hand-writing clause text would put words
    in officers' mouths and fabricate the provenance the Memory screen shows
    underneath every judgement. The builder authors the VOCABULARY (the
    case_signature); officers supply the CONTENT; consolidation forms the rule.

Two rules the seed data must obey:

  1. FACETS MUST COME FROM THE APP'S OWN `case_signature`. A correction stamped
     with a family the app does not declare produces a judgement that can never
     apply — it renders as "team · 3 officers" while being structurally
     incapable of firing. Enforced by `_declared_families()` before any write.

  2. VARY THE INCIDENTAL FACETS — BUT NOT ALL OF THEM AT ONCE. Two gates pull in
     opposite directions here, and getting this wrong produces no memory at all:

       * CLUSTERING is pairwise against the cluster's first correction and needs
         an overlap coefficient >= 0.5 — share at least half your facets with it.
       * SCOPE is the three-way INTERSECTION, so a facet common to all three
         ends up in the judgement's scope whether it belongs there or not.

     Varying everything incidental satisfies the second and fails the first: the
     first attempt at these seeds shared only the one meaningful facet (overlap
     1/4 = 0.25), so each officer formed a SINGLETON cluster, every cluster fell
     under the promotion gate, and consolidation authored nothing at all.

     The resolution: each officer shares a DIFFERENT second facet with the first
     one. Every pair clears 0.5, while the intersection across all three is still
     just the lesson. `_check_cluster_shape()` asserts both properties before
     writing, because this is invisible until a whole pass produces zero rules.

Officers are the real personas from `users.json` — three line officers per
department, which is exactly `promotion_min_officers: 3`. Seeding three DISTINCT
officers earns team standing rather than asserting it; a single officer would
stay a candidate, which is the correct outcome for one person's opinion.

Idempotent: re-running skips apps that already carry their seed corrections.
Pass --force to wipe this tenant's memory (corrections AND judgements) and
rebuild it from scratch.

Usage (from the repo root, with smart-app-service's venv):

    python demo-data/tenants/acme-bank/scripts/seed_memory.py          # dry run
    python demo-data/tenants/acme-bank/scripts/seed_memory.py --apply
    python demo-data/tenants/acme-bank/scripts/seed_memory.py --apply --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


def _service_dir() -> Path:
    """Where smart-app-service actually lives.

    Runs from the repo (local dev) OR from inside the service container (prod,
    shipped to /tmp and executed there), where the repo layout does not exist
    and secrets arrive from Vault rather than a .env. Probe for the code rather
    than assuming a checkout.
    """
    here = Path(__file__).resolve()
    candidates = []
    if len(here.parents) > 4:
        candidates.append(here.parents[4] / "smart-app-service")
    candidates.append(Path("/app/smart-app-service"))
    for p in candidates:
        if (p / "corrections.py").exists():
            return p
    return candidates[0]


SERVICE = _service_dir()
sys.path.insert(0, str(SERVICE))

TENANT = "acme-bank"

# --------------------------------------------------------------------------
# The evidence. Three DISTINCT officers per lesson, each from users.json, each
# writing it their own way — officers do not produce identical sentences and the
# clustering has to survive that.
#
# A lesson is only worth learning if the SOP does NOT already settle it. A rule
# already written down belongs in the corpus, which is live-fetched and supreme;
# the learned layer exists for the judgement calls it leaves open.
#
# The first three lessons below FAIL that test, and they are kept as the honest
# illustration of why it matters. "Stop collection on a dispute" is Collections
# SOP §5 almost verbatim; escalating a broken PTP is §4.5; the income lesson is
# most of Income Verification SOP §7. A/B runs (memory_ab.py,
# memory_ab_collections.py) confirmed the consequence: the agent reaches the SAME
# decision with the judgement retired, because it can read the rule. Memory
# demonstrably fires and is cited — it just has nothing to add.
#
# The DSA lesson is the control. Retail Credit Policy §1 states it applies to
# EVERY sourcing channel and prescribes nothing channel-specific, so treating
# DSA-sourced files with extra scrutiny is precisely the kind of call officers
# make and no document contains — and it is the only one of the four that can
# honestly be used to claim the memory earns its keep.
#
# It does. memory_ab_dsa.py, 4 pairs on the same DSA-sourced application:
# employer verification was raised 4/4 WITH the judgement and 0/4 without. The
# credit decision itself was Rejected either way (FOIR 95% breaches the cap on
# its own) — memory did not overturn policy, it added the check the team applies
# on top of it. That is the shape of the benefit: not a different verdict, an
# extra step nobody wrote down.
# --------------------------------------------------------------------------
SEEDS: List[Dict[str, Any]] = [
    {
        "app_slug": "acme-bank-collections-priority",
        "modality": "record",
        "task_type": "decision",
        "event": "reject",
        "reason_code": "dispute_raised",
        "contested_fields": ["next_action"],
        "overrides": [],
        "lesson": "a disputed account is not a collections case — stop and route "
                  "to servicing",
        "corrections": [
            {
                "officer": "collections-mum@acme-bank-demo.citra.ai",
                "reason_text": "account is under dispute - stop collection activity and route the case to servicing",
                # The cluster representative. The other two each share dispute
                # plus ONE of this officer's other facets — a different one each
                # time, so every pair clears the overlap gate while the
                # three-way intersection stays {risk_flag:dispute}.
                "case_facets": ["risk_flag:dispute", "bucket:61-90",
                                "overdue_band:50000_300000", "last_payment:present"],
            },
            {
                "officer": "collections-hyd@acme-bank-demo.citra.ai",
                "reason_text": "customer has raised a dispute - stop collection and route this case to servicing",
                # shares bucket:61-90 with the representative
                "case_facets": ["risk_flag:dispute", "bucket:61-90",
                                "overdue_band:lt_50000", "last_payment:absent"],
            },
            {
                "officer": "collections-jai@acme-bank-demo.citra.ai",
                "reason_text": "dispute on the account - collection activity must stop and the case goes to servicing",
                # shares overdue_band:50000_300000 with the representative
                "case_facets": ["risk_flag:dispute", "bucket:90+",
                                "overdue_band:50000_300000", "last_payment:absent"],
            },
        ],
    },
    {
        "app_slug": "acme-bank-claim-triage",
        "modality": "record",
        "task_type": "decision",
        "event": "override",
        "reason_code": "late_intimation",
        "contested_fields": ["decision"],
        "overrides": [{"override": {"decision": {"from": "settle",
                                                 "to": "exclusion_review"}}}],
        "lesson": "intimation past the 30-day window goes to exclusion review, not "
                  "straight to settlement",
        "corrections": [
            {
                "officer": "claims-motor@acme-bank-demo.citra.ai",
                "reason_text": "intimated past the 30 day window - needs exclusion review before any settlement",
                # Representative. 5 facets, so each pair needs 3 shared.
                "case_facets": ["intimation_delay:gte_30", "claim_type:own_damage",
                                "claimed_band:50000_250000", "fir:present",
                                "surveyor:present"],
            },
            {
                "officer": "claims-health@acme-bank-demo.citra.ai",
                "reason_text": "late intimation past the 30 day window - exclusion review first, not settlement",
                # shares claim_type + claimed_band with the representative
                "case_facets": ["intimation_delay:gte_30", "claim_type:own_damage",
                                "claimed_band:50000_250000", "fir:absent",
                                "surveyor:absent"],
            },
            {
                "officer": "claims-property@acme-bank-demo.citra.ai",
                "reason_text": "intimation past the 30 day window is an exclusion review question, not a settlement",
                # shares fir + surveyor with the representative instead
                "case_facets": ["intimation_delay:gte_30", "claim_type:theft",
                                "claimed_band:gte_1000000", "fir:present",
                                "surveyor:present"],
            },
        ],
    },
    {
        "app_slug": "loan-application-triage",
        "modality": "record",
        "task_type": "decision",
        "event": "override",
        "reason_code": "income_not_corroborated",
        "contested_fields": ["decision"],
        "overrides": [{"override": {"decision": {"from": "approve",
                                                 "to": "refer_income_proof"}}}],
        # The SOP checks that a Form 16 is AUTHENTIC and never reconciles filed
        # income against declared income — a deliberate gap in the corpus. This
        # is the lesson the app can only get from officers.
        "lesson": "a genuine income document is not corroboration — reconcile it "
                  "against the filed return",
        "corrections": [
            {
                "officer": "credit-pune@acme-bank-demo.citra.ai",
                "reason_text": "form 16 is genuine but the filed return does not support the declared income - refer for income verification",
                # Representative. income_proof:present is the lesson: HAVING the
                # document is not the same as the document corroborating.
                "case_facets": ["income_proof:present", "product:home",
                                "amount_band:gte_2500000", "foir_band:30_50"],
            },
            {
                "officer": "credit-blr@acme-bank-demo.citra.ai",
                "reason_text": "income proof does not match the filed return - refer for income verification, the document alone is not enough",
                # shares product:home with the representative
                "case_facets": ["income_proof:present", "product:home",
                                "amount_band:lt_500000", "foir_band:50_70"],
            },
            {
                "officer": "credit-mum@acme-bank-demo.citra.ai",
                "reason_text": "declared income is not corroborated by the filed return - refer this for income verification",
                # shares amount_band:gte_2500000 with the representative
                "case_facets": ["income_proof:present", "product:business",
                                "amount_band:gte_2500000", "foir_band:lt_30"],
            },
        ],
    },
    {
        "app_slug": "loan-application-triage",
        "modality": "record",
        "task_type": "decision",
        "event": "override",
        "reason_code": "data_stale_or_wrong",
        "contested_fields": ["decision"],
        "overrides": [{"override": {"decision": {"from": "approve",
                                                 "to": "verify_employment"}}}],
        # THE CONTROL. Retail Credit Policy §1 applies to every sourcing channel
        # and says nothing channel-specific, so no document tells the agent to
        # treat a DSA file differently. If memory changes behaviour anywhere, it
        # has to be here.
        "lesson": "DSA-sourced files get employment verified with the employer "
                  "directly — the submitted document set is not enough",
        "corrections": [
            {
                "officer": "credit-mum@acme-bank-demo.citra.ai",
                "reason_text": "dsa sourced file - verify employment directly with the employer, do not rely on the submitted documents alone",
                # Representative: 5 facets, each pair needs 3 shared.
                "case_facets": ["sourcing_channel:dsa", "product:personal",
                                "amount_band:lt_500000", "foir_band:50_70",
                                "income_proof:present"],
            },
            {
                "officer": "credit-pune@acme-bank-demo.citra.ai",
                "reason_text": "dsa sourced application - verify employment with the employer directly, submitted documents alone are not enough",
                # shares product + amount_band with the representative
                "case_facets": ["sourcing_channel:dsa", "product:personal",
                                "amount_band:lt_500000", "foir_band:30_50",
                                "income_proof:absent"],
            },
            {
                "officer": "credit-blr@acme-bank-demo.citra.ai",
                "reason_text": "for dsa sourced files verify employment independently with the employer, do not rely on submitted documents",
                # shares foir_band + income_proof with the representative
                "case_facets": ["sourcing_channel:dsa", "product:auto",
                                "amount_band:500000_1000000", "foir_band:50_70",
                                "income_proof:present"],
            },
        ],
    },
]


def _check_cluster_shape(seed: Dict[str, Any]) -> tuple:
    """Will these corrections actually become ONE judgement with a sane scope?

    Runs the SERVICE's own gate functions rather than a copy of the arithmetic,
    so this cannot drift from the thresholds consolidation really applies. Two
    independent properties, and seeds have failed each of them:

      * every correction must cluster with the representative (text similarity
        AND facet overlap) — otherwise each is a singleton, no cluster reaches
        the promotion gate, and the pass silently authors nothing;
      * the three-way intersection must be exactly the lesson — a stray shared
        facet would narrow the judgement to cases nobody meant.
    """
    from consolidation import (CLUSTER_FACET_OVERLAP, CLUSTER_SIMILARITY,
                               facet_compatible, text_similarity)

    rows = seed["corrections"]
    rep = rows[0]
    lines, ok = [], True
    for c in rows[1:]:
        sim = text_similarity(c["reason_text"], rep["reason_text"])
        shared = set(c["case_facets"]) & set(rep["case_facets"])
        overlap = len(shared) / min(len(c["case_facets"]), len(rep["case_facets"]))
        good = sim >= CLUSTER_SIMILARITY and facet_compatible(
            c["case_facets"], rep["case_facets"],
            min_overlap=CLUSTER_FACET_OVERLAP)
        ok = ok and good
        lines.append(
            f"  {'ok  ' if good else 'FAIL'} {c['officer'].split('@')[0]:<16} "
            f"vs representative: text={sim:.2f} (>={CLUSTER_SIMILARITY}) "
            f"facets={overlap:.2f} (>={CLUSTER_FACET_OVERLAP}) shared={sorted(shared)}"
        )
    common = set.intersection(*(set(c["case_facets"]) for c in rows))
    lines.append(f"  scope (3-way intersection): {sorted(common) or 'EMPTY'}")
    if not common:
        lines.append("  ABORT — nothing common, so a judgement has no scope.")
        ok = False
    elif len(common) > 1:
        lines.append(f"  ABORT — {len(common)} shared facets would ALL enter the "
                     f"scope; vary the incidental ones so only the lesson remains.")
        ok = False
    if not ok:
        lines.append("  (a failing pair clusters alone → under the promotion "
                     "gate → consolidation authors NOTHING)")
    return ok, "\n".join(lines)


async def _wire_db():
    """Give the memory modules the handles the FastAPI lifespan normally sets.

    Same client, same db, same env routing — so this runs the SAME code as the
    service rather than a second implementation that could drift from it.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    env_file = SERVICE / ".env"
    if env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(env_file)
    os.chdir(SERVICE)

    import main as m
    from config import get_settings

    s = get_settings()
    client = AsyncIOMotorClient(s.mongo_uri)
    db = client[s.mongo_db]
    m._mongo_client = client
    m._db = db
    m._apps_col = db[s.apps_collection]
    m._agents_col = db[s.agents_collection]
    m._decision_records_col = db[s.decision_records_collection]
    return client, db, s.mongo_db


async def _declared_families(db, slug: str) -> Set[str]:
    """The facet families this app can actually emit. Anything else on a
    correction is a defect, not a nuance — see rule 1 in the module docstring."""
    doc = await db["smartapp_apps"].find_one({"slug": slug},
                                             {"_id": 0, "app_spec": 1})
    if not doc:
        return set()
    cs = (doc.get("app_spec") or {}).get("case_signature") or {}
    fams = {f.get("family") for f in (cs.get("facets") or [])}
    # Emitted by the platform from the dataset ontology, never authored.
    return fams | {"vertical", "sub_vertical", "country"}


async def _declared_reason_codes(db, slug: str) -> Set[str]:
    """The reason codes this app offers its officers. A code outside the app's
    own taxonomy cannot be picked in the UI, so seeding one would fake evidence
    that could never actually arise."""
    doc = await db["smartapp_apps"].find_one({"slug": slug},
                                             {"_id": 0, "app_spec": 1})
    cs = ((doc or {}).get("app_spec") or {}).get("case_signature") or {}
    return {r.get("code") for r in (cs.get("reason_codes") or [])}


async def main(apply: bool, force: bool) -> int:
    client, db, dbname = await _wire_db()
    print(f"database: {dbname}   tenant: {TENANT}")

    import consolidation as cons
    import corrections as cx

    planned = 0
    for seed in SEEDS:
        slug = seed["app_slug"]
        declared = await _declared_families(db, slug)
        if not declared:
            print(f"\n[{slug}] SKIP — app not published in this database")
            continue

        # Rule 1, enforced before a single write.
        used = {t.split(":", 1)[0]
                for c in seed["corrections"] for t in c["case_facets"]}
        undeclared = sorted(used - declared)
        if undeclared:
            print(f"\n[{slug}] ABORT — seed uses undeclared facet families: "
                  f"{undeclared}\n  declared: {sorted(declared)}")
            print("  A judgement scoped to these could NEVER fire. Fix the seed "
                  "or the app's case_signature.")
            client.close()
            return 2

        codes = await _declared_reason_codes(db, slug)
        if codes and seed["reason_code"] not in codes:
            print(f"\n[{slug}] ABORT — reason_code {seed['reason_code']!r} is not "
                  f"in this app's taxonomy: {sorted(codes)}")
            client.close()
            return 2

        print(f"\n[{slug}] {seed['lesson']}")
        print(f"  bucket: {seed['modality']}/{seed['task_type']}   "
              f"reason_code: {seed['reason_code']}")
        ok, detail = _check_cluster_shape(seed)
        print(detail)
        if not ok:
            client.close()
            return 2

        # Keyed on reason_code as well as the bucket: one app can carry several
        # lessons in the same modality/task_type, and a bucket-wide delete would
        # take a sibling lesson's evidence with it.
        key = {"app_slug": slug, "task_type": seed["task_type"],
               "reason_code": seed["reason_code"]}
        existing = await db["smartapp_corrections"].count_documents(key)
        print(f"  existing corrections for this lesson: {existing}")

        if existing and not force:
            print("  SKIP — already seeded (use --force to rebuild)")
            continue
        planned += len(seed["corrections"])
        if not apply:
            for c in seed["corrections"]:
                print(f"    + {c['officer'].split('@')[0]}: "
                      f"\"{c['reason_text'][:58]}…\"")
            continue

        if force and existing:
            d1 = await db["smartapp_corrections"].delete_many(key)
            d2 = await db["smartapp_clauses"].delete_many(key)
            print(f"  --force: removed {d1.deleted_count} correction(s), "
                  f"{d2.deleted_count} judgement(s)")

        for c in seed["corrections"]:
            cid = await cx.record_correction(
                tenant_id=TENANT, app_slug=slug,
                modality=seed["modality"], task_type=seed["task_type"],
                event=seed["event"], officer=c["officer"],
                officer_role="officer",
                case_facets=c["case_facets"],
                reason_code=seed["reason_code"],
                reason_text=c["reason_text"],
                contested_fields=seed["contested_fields"],
                overrides=seed["overrides"],
            )
            print(f"    + {c['officer'].split('@')[0]} -> {cid}")

    if not apply:
        print(f"\nDRY RUN — {planned} correction(s) would be written. "
              f"Re-run with --apply")
        client.close()
        return 0

    if planned:
        # The real batch, thresholds bypassed — one LLM call per new lesson.
        print("\nfolding evidence into judgements (real consolidation pass)…")
        totals = await cons.run_consolidation_pass(force=True)
        print("  " + json.dumps({k: totals.get(k) for k in
                                 ("buckets", "created", "reinforced", "skipped",
                                  "errors")}))

    print("\n=== memory now ===")
    found = False
    async for c in db["smartapp_clauses"].find(
            {"tenant_id": TENANT}, {"_id": 0}):
        found = True
        print(f"  [{c.get('status')}] {c.get('clause_id')} {c.get('app_slug')} "
              f"{c.get('modality')}/{c.get('task_type')}")
        print(f"      \"{c.get('text')}\"")
        print(f"      scope={c.get('scope_facets')} "
              f"officers={c.get('support_count')}")
    if not found:
        print("  (none — consolidation authored nothing)")
    client.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write (default is a dry run)")
    ap.add_argument("--force", action="store_true",
                    help="wipe this tenant's seeded memory and rebuild it")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.apply, a.force)))
