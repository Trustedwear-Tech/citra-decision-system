# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Export loan-application-triage from the dev database into apps/.

It is the only one of the four built by the BUILDER rather than hand-authored,
and its spec has lived only in Mongo — so it could not be rebuilt from the repo,
could not be published to prod, and its history was one dropped collection away
from being unrecoverable. This makes it reproducible like the other three.

The slug stays `loan-application-triage` rather than moving to the tenant's
`acme-bank-*` convention: corrections, clauses and decision records are keyed by
app_slug, so renaming would orphan the seeded judgements and the 19-case A/B
evidence that stands behind them. Consistency is not worth losing the provenance.
"""
import asyncio, json, os, pathlib, sys

SVC = pathlib.Path(r"C:/Github/Citra-AI/smart-app-service")
sys.path.insert(0, str(SVC)); os.chdir(SVC)
from dotenv import load_dotenv
load_dotenv(SVC / ".env")
from motor.motor_asyncio import AsyncIOMotorClient

OUT = pathlib.Path(r"C:/Github/Citra-AI/demo-data/tenants/acme-bank/apps/01_loan_triage.json")
SLUG = "loan-application-triage"

# Runtime/bookkeeping fields the service owns — exporting them would ship a
# stale version number or a foreign app_id into a fresh publish.
# agent_id is NOT bookkeeping: it BINDS the app to its agent and the publish
# gate requires it. The other three apps carry it in app_spec too.
STRIP_APP = {"version", "app_id", "status", "deployed_at", "promoted_at",
             "promoted_to_slug", "author_at", "author_user_id", "author_email",
             "owner_changed_at", "owner_changed_by", "previous_owners",
             "requirements_unmet", "preview_mode", "preview_until", "spec_version"}
STRIP_AGENT = {"version", "spec_version", "learning_log"}


async def main() -> int:
    c = AsyncIOMotorClient(os.environ["MONGO_URI"])
    db = c[os.environ.get("MONGO_DB", "dev")]
    doc = await db["smartapp_apps"].find_one({"slug": SLUG}, {"_id": 0})
    if not doc:
        print("not found in dev"); return 1
    spec = dict(doc.get("app_spec") or {})
    agent_id = spec.get("agent_id")
    ag = await db["smartapp_agents"].find_one({"agent_id": agent_id}, {"_id": 0})
    agent = dict((ag or {}).get("agent_spec") or {})
    print(f"dev version: {doc.get('version')}  agent: {agent_id}")
    print(f"tools_v2: {[t.get('name') for t in (agent.get('tools_v2') or [])]}")
    print(f"case_signature facets: "
          f"{[f['family'] for f in (spec.get('case_signature') or {}).get('facets', [])]}")

    for k in STRIP_APP:
        spec.pop(k, None)
    for k in STRIP_AGENT:
        agent.pop(k, None)
    spec["slug"] = SLUG

    out = {
        "_doc": ("Acme Bank - Loan Application Triage. Exported from the dev "
                 "database (the BUILDER produced this one; the other three are "
                 "hand-authored). Slug kept as loan-application-triage because "
                 "corrections and clauses are keyed by app_slug - renaming would "
                 "orphan the seeded judgements and the A/B evidence behind them."),
        "session_id": "demo_seed_acme_bank_loan_triage_v1",
        "skills": [],
        "app_spec": spec,
        "agent_spec": agent,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {OUT.name}  ({OUT.stat().st_size:,} bytes)")
    print("pages:", [(p['id'], [q['id'] for q in p.get('panels', [])])
                     for p in spec.get('pages', [])])
    c.close()
    return 0

raise SystemExit(asyncio.run(main()))
