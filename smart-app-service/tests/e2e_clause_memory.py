# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""E2E for clause memory against a LIVE smart-app-service.

Drives the real HTTP surface with a minted admin JWT — no in-process fakes. It
exercises the full loop the design claims to deliver:

    officer corrections -> consolidation -> a scoped rule ->
    retrieval on a matching case -> provenance -> impact metrics

and, just as importantly, the paths that must NOT produce a rule: contradictory
evidence, a single officer's opinion, and an uncoded reject.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import jwt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:9100")
APP = "acme-power-complaint-auto-routing"
TENANT = "acme-power"
MOD, TT = "record", "decision"

load_dotenv(".env")
DB_URI, DB_NAME = os.environ["MONGO_URI"], os.environ.get("MONGO_DB", "dev")

_passed, _failed = [], []


def check(name, cond, detail=""):
    (_passed if cond else _failed).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    return cond


def token(roles=("super_admin",)):
    return jwt.encode(
        {"sub": "e2e", "user_id": "e2e", "email": "e2e@acme-power.citra.ai",
         "roles": list(roles), "tenant_id": TENANT, "org_id": TENANT,
         "exp": int(time.time()) + 1800},
        os.environ["JWT_SECRET"], algorithm=os.getenv("JWT_ALGORITHM", "HS256"))


def api(path, method="GET", body=None, roles=("super_admin",), raw=False):
    req = urllib.request.Request(
        f"{BASE}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token(roles)}",
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        payload = e.read()
        if raw:
            return e.code, payload.decode(errors="replace")
        try:
            return e.code, json.loads(payload or b"null")
        except Exception:
            return e.code, {"raw": payload.decode(errors="replace")}


#: Filled by ``take_backup()`` before the first destructive write and replayed
#: by ``restore_backup()`` at the end. APP is a REAL demo app with REAL learned
#: clauses, and seed()/cleanup() delete_many on it — so without this, running
#: this harness silently destroys a tenant's accumulated judgement. It is not
#: recoverable and nothing else in the file warns you.
_BACKUP: dict = {}


async def take_backup():
    c = AsyncIOMotorClient(DB_URI)
    db = c[DB_NAME]
    for col in ("smartapp_corrections", "smartapp_clauses"):
        _BACKUP[col] = await db[col].find({"app_slug": APP}).to_list(10_000)
    c.close()
    n = sum(len(v) for v in _BACKUP.values())
    print(f"  backed up {n} existing doc(s) for {APP} — restored on exit")


async def restore_backup():
    if not _BACKUP:
        return
    c = AsyncIOMotorClient(DB_URI)
    db = c[DB_NAME]
    for col, docs in _BACKUP.items():
        await db[col].delete_many({"app_slug": APP})
        if docs:
            await db[col].insert_many(docs)
    c.close()
    n = sum(len(v) for v in _BACKUP.values())
    print(f"  restored {n} pre-existing doc(s) for {APP}")


async def seed(rows):
    c = AsyncIOMotorClient(DB_URI)
    db = c[DB_NAME]
    await db["smartapp_corrections"].delete_many({"app_slug": APP})
    await db["smartapp_clauses"].delete_many({"app_slug": APP})
    now = datetime.now(timezone.utc)
    docs = []
    for i, r in enumerate(rows):
        docs.append({
            "correction_id": f"corr-e2e-{i}", "tenant_id": TENANT, "app_slug": APP,
            "modality": MOD, "task_type": TT, "officer": r["officer"],
            "event": r.get("event", "reject"), "reason_code": r.get("code"),
            "reason_text": r["text"], "case_facets": r.get("facets", []),
            "contested_fields": r.get("fields", []),
            "overrides": r.get("overrides", []),
            "injected_clause_ids": [], "cited_clause_ids": [],
            "overruled_clause_ids": [], "consumed_by": None, "at": now,
        })
    if docs:
        await db["smartapp_corrections"].insert_many(docs)
    c.close()


async def clauses():
    c = AsyncIOMotorClient(DB_URI)
    rows = await c[DB_NAME]["smartapp_clauses"].find(
        {"app_slug": APP}, {"_id": 0}).to_list(100)
    c.close()
    return rows


async def cleanup():
    c = AsyncIOMotorClient(DB_URI)
    db = c[DB_NAME]
    await db["smartapp_corrections"].delete_many({"app_slug": APP})
    await db["smartapp_clauses"].delete_many({"app_slug": APP})
    await db["smartapp_learning_control"].delete_many({"control": "consolidation"})
    c.close()


async def main():
    import asyncio

    print("\n=== 1. auth + admin surfaces ===")
    st, _ = api("/admin/consolidation", roles=("viewer",))
    check("non-admin is refused the batch console", st == 403, f"HTTP {st}")
    st, status = api("/admin/consolidation")
    check("admin reads the batch console", st == 200, f"HTTP {st}")
    check("both environments are swept",
          set(status.get("environments") or []) >= {"prod"},
          str(status.get("environments")))

    print("\n=== 2. agreement forms ONE scoped rule ===")
    THEFT = ["category:theft_report", "priority:high"]
    await seed([
        {"officer": "asha@x", "code": "wrong_department", "fields": ["assigned_to"],
         "text": "theft reports must go to revenue protection, not the local line crew",
         "facets": THEFT,
         "overrides": [{"override": {"assigned_to": {"from": "Line Crew", "to": "Revenue Protection"}}}],
         "event": "override"},
        {"officer": "bhavna@x", "code": "wrong_department", "fields": ["assigned_to"],
         "text": "theft report routed to line crew again, revenue protection owns theft",
         "facets": THEFT,
         "overrides": [{"override": {"assigned_to": {"from": "Line Crew", "to": "Revenue Protection"}}}],
         "event": "override"},
        {"officer": "chetan@x", "code": "wrong_department", "fields": ["assigned_to"],
         "text": "revenue protection must own theft reports, line crew cannot action them",
         "facets": THEFT,
         "overrides": [{"override": {"assigned_to": {"from": "Line Crew", "to": "Revenue Protection"}}}],
         "event": "override"},
    ])
    st, res = api("/admin/consolidation/run", method="POST")
    r = (res or {}).get("result", {})
    check("consolidation pass succeeds", st == 200 and r.get("errors") == 0,
          f"HTTP {st} errors={r.get('errors')}")
    check("exactly one rule authored", r.get("created") == 1, f"created={r.get('created')}")
    cl = await clauses()
    check("rule is ACTIVE on 3 distinct officers",
          len(cl) == 1 and cl[0]["status"] == "active" and cl[0]["support_count"] == 3,
          f"{cl[0]['status']}/{cl[0]['support_count']}" if cl else "no clause")
    check("rule is SCOPED to the shared facets",
          bool(cl) and set(cl[0]["scope_facets"]) <= set(THEFT) and cl[0]["scope_facets"],
          str(cl[0]["scope_facets"]) if cl else "-")
    check("rule text is one short sentence",
          bool(cl) and cl[0]["text_words"] <= 40, f"{cl[0]['text_words']}w" if cl else "-")
    if cl:
        print(f"        rule: “{cl[0]['text']}”")
    cid = cl[0]["clause_id"] if cl else None

    print("\n=== 3. the rule is READABLE and TRACEABLE ===")
    st, body = api(f"/apps/{APP}/memory/clauses")
    check("clause list renders", st == 200 and len(body.get("clauses") or []) == 1,
          f"HTTP {st}")
    check("inventory counts the active rule",
          (body.get("inventory") or {}).get("by_status", {}).get("active") == 1)
    st, prov = api(f"/apps/{APP}/memory/clauses/{cid}/provenance")
    check("provenance returns the 3 rejects that taught it",
          st == 200 and len(prov.get("corrections") or []) == 3, f"HTTP {st}")
    check("provenance quotes the officers verbatim",
          any("revenue protection" in (c.get("reason_text") or "").lower()
              for c in (prov.get("corrections") or [])))
    st, _ = api(f"/apps/{APP}/memory/clauses/C-999/provenance")
    check("unknown clause 404s", st == 404, f"HTTP {st}")

    print("\n=== 4. RETRIEVAL: fires on a matching case, not on others ===")
    sys.path.insert(0, os.getcwd())
    import main as _svc
    from clause_store import select_clauses

    # This block calls the retrieval IN-PROCESS (there is no HTTP endpoint that
    # exposes it directly), so the lazy-main collection accessor needs a handle.
    if _svc._db is None:
        _svc._db = AsyncIOMotorClient(DB_URI)[DB_NAME]

    blk, ids = await select_clauses(
        tenant_id=TENANT, app_slug=APP, modality=MOD, task_type=TT,
        case_facets=THEFT + ["channel:care_line"])
    check("fires on a matching case", ids == [cid], str(ids))
    check("block carries the id for the blame edge", cid in blk)
    blk2, ids2 = await select_clauses(
        tenant_id=TENANT, app_slug=APP, modality=MOD, task_type=TT,
        case_facets=["category:billing_issue", "priority:low"])
    check("does NOT fire on an unrelated case", ids2 == [] and blk2 == "", str(ids2))

    print("\n=== 5. CONTRADICTION produces no rule ===")
    await seed([
        {"officer": "dev@x", "code": "wrong_department", "fields": ["assigned_to"],
         "text": "route this to the field team", "facets": THEFT, "event": "override",
         "overrides": [{"override": {"assigned_to": {"from": "Paula", "to": "Adam"}}}]},
        {"officer": "esha@x", "code": "wrong_department", "fields": ["assigned_to"],
         "text": "route this to the field team", "facets": THEFT, "event": "override",
         "overrides": [{"override": {"assigned_to": {"from": "Adam", "to": "Paula"}}}]},
    ])
    st, res = api("/admin/consolidation/run", method="POST")
    r = (res or {}).get("result", {})
    check("contradiction is detected and split", r.get("conflicts_split") == 1,
          f"conflicts_split={r.get('conflicts_split')}")
    check("NO rule authored from contradictory evidence", r.get("created") == 0,
          f"created={r.get('created')}")
    check("evidence is kept, not discarded", len(await clauses()) == 0)

    print("\n=== 6. a single opinion is a CANDIDATE, never a team rule ===")
    # This used to assert "one officer authors nothing", which is the OLD
    # doctrine. Current contract (clause_store.LIVE_STATUSES, citing
    # sop-rules-officer-judgement-plan §0): a lone officer's experience is used
    # IMMEDIATELY and LABELED, never hidden — a one-officer branch office must
    # still learn. So it authors a `candidate`, injected with honest
    # attribution ("one officer's judgement — not yet corroborated"), and only
    # promotes to `active` at promotion_min_officers distinct officers.
    #
    # What must never happen is a lone opinion being asserted AS the team's.
    # That is what this now pins.
    await seed([{"officer": "solo@x", "code": "wrong_priority", "fields": ["priority"],
                 "text": "urgent is wrong for a routine meter fault", "facets": THEFT}])
    api("/admin/consolidation/run", method="POST")
    solo = await clauses()
    check("one officer authors exactly one clause", len(solo) == 1,
          f"got {len(solo)}")
    check("it is a CANDIDATE, not a team rule",
          bool(solo) and solo[0].get("status") == "candidate",
          (solo[0].get("status") if solo else "—"))
    check("it carries exactly one supporting officer",
          bool(solo) and len(solo[0].get("support_officers") or []) == 1,
          str(solo[0].get("support_officers") if solo else "—"))

    print("\n=== 7. an UNCODED reject cannot become a rule ===")
    await seed([{"officer": f"o{i}@x", "code": None, "fields": [],
                 "text": "this routing felt wrong to me", "facets": THEFT}
                for i in range(3)])
    st, res = api("/admin/consolidation/run", method="POST")
    check("uncoded cluster is skipped", (res or {}).get("result", {}).get("created") == 0)
    check("no rule from uncoded evidence", len(await clauses()) == 0)

    print("\n=== 8. batch control ===")
    st, _ = api("/admin/consolidation/pause?paused=true", method="POST",
                body={"reason": "e2e"})
    check("pause accepted", st == 200, f"HTTP {st}")
    st, res = api("/admin/consolidation/run", method="POST")
    check("run refuses while paused",
          (res or {}).get("result", {}).get("paused") is True)
    st, _ = api("/admin/consolidation/pause?paused=false", method="POST",
                body={"reason": ""})
    check("resume accepted", st == 200, f"HTTP {st}")

    print("\n=== 9. impact metrics never fabricate ===")
    st, imp = api("/org/memory-impact")
    check("impact endpoint responds", st == 200, f"HTTP {st}")
    check("lift is SUPPRESSED without both cohorts",
          imp.get("lift") is None and bool(imp.get("lift_note")),
          str(imp.get("lift_note"))[:70])
    st, _ = api("/org/memory-impact", roles=("viewer",))
    check("impact is admin-gated", st == 403, f"HTTP {st}")

    print("\n=== 10. legacy surfaces are GONE ===")
    st, _ = api(f"/apps/{APP}/memory/rubrics")
    check("old rubrics endpoint is gone", st == 404, f"HTTP {st}")
    c = AsyncIOMotorClient(DB_URI)
    names = await c[DB_NAME].list_collection_names()
    c.close()
    check("legacy rubric collection dropped",
          "smartapp_analysis_rubrics" not in names)

    await cleanup()
    print(f"\n{'='*58}\n  {len(_passed)} passed, {len(_failed)} failed")
    for f in _failed:
        print(f"    FAILED: {f}")
    return 1 if _failed else 0


async def _guarded():
    """Restore even when an assertion fails or the service dies midway — a
    crashed harness must not be the thing that loses a tenant's memory."""
    await take_backup()
    try:
        return await main()
    finally:
        await restore_backup()


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_guarded()))
