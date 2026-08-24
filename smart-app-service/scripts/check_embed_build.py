# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Inspect what the BUILDER authored for an embed page.

This is the real test of citra-embed-spec: not that a build completed, but that
the LLM produced the composition that actually works. Each check below exists
because getting it wrong fails in a specific, already-observed way.

    MONGO_URI=mongodb://localhost:27077 MONGO_DB=citra_e2e \
      python scripts/check_embed_build.py [slug]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

SLUG = sys.argv[1] if len(sys.argv) > 1 else None
passed = failed = warned = 0


def check(name, ok, why=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"\n          {why}" if why else ""))


def warn(name, why=""):
    global warned
    warned += 1
    print(f"  warn  {name}" + (f"\n          {why}" if why else ""))


async def main() -> int:
    uri = os.getenv("MONGO_URI")
    if not uri:
        print("MONGO_URI required")
        return 1
    db = AsyncIOMotorClient(uri)[os.getenv("MONGO_DB", "citra_e2e")]

    # EXCLUDE the hand-seeded fixture. Without this the checker finds
    # embed-e2e-loan — a spec I wrote by hand to exercise the runtime — and
    # grades THAT, reporting a confident pass that says nothing whatsoever
    # about what the builder authored. A test that can pass without the thing
    # under test having run is worse than no test.
    SEEDED = {"embed-e2e-loan"}
    q = ({"slug": SLUG} if SLUG
         else {"app_spec.pages.kind": "embed", "slug": {"$nin": list(SEEDED)}})
    doc = None
    for col in ("test_smartapp_apps", "smartapp_apps"):
        doc = await db[col].find_one(q, sort=[("deployed_at", -1)])
        if doc:
            print(f"\napp: {doc['slug']}  (store: {col})")
            break
    if not doc:
        print("no published app with an embed page — did the build finish?")
        return 2

    spec = doc.get("app_spec") or {}
    agent = (await db["test_smartapp_agents"].find_one({"agent_id": doc.get("agent_id")})
             or await db["smartapp_agents"].find_one({"agent_id": doc.get("agent_id")})
             or {}).get("agent_spec") or {}

    pages = spec.get("pages") or []
    embed_pages = [p for p in pages if p.get("kind") == "embed"]
    print("\n— the page —")
    check("an embed page was authored", bool(embed_pages),
          f"page kinds: {[p.get('kind') for p in pages]}")
    if not embed_pages:
        return 2
    page = embed_pages[0]
    panels = page.get("panels") or []
    types = [p.get("type") for p in panels]
    print(f"  panels: {types}")

    print("\n— the trigger (only affordance that runs the agent) —")
    with_action = [p for p in panels
                   if any((a or {}).get("agent_action") for a in (p.get("actions") or []))]
    check("a panel carries an agent_action", bool(with_action),
          "without one the card is a viewer: nothing can run the agent, so no "
          "recommendation and no reason capture")
    # The trigger belongs on the DETAIL panel. A queue on an embed page is the
    # old shape: pinned to one id, so its search / view switcher / row counter
    # are dead controls, and it repeats the fields the detail already shows.
    check("the trigger is on the detail panel, not a queue",
          any(p.get("type") == "detail" for p in with_action),
          f"trigger sits on {[p.get('type') for p in with_action]} — move it to "
          "the detail panel and drop the queue (see citra-embed-spec)")
    if any(p.get("type") == "queue" for p in panels):
        warn("the embed page still has a queue",
             "an embed shows exactly one record; the queue duplicates the "
             "detail panel's fields and renders controls that cannot act")

    print("\n— the record binding —")
    details = [p for p in panels if p.get("type") == "detail"]
    check("a detail panel is present", bool(details))
    if details:
        d = details[0]
        check("it binds via data_source, not linked_to",
              bool(d.get("data_source")) and not d.get("linked_to"),
              f"data_source={d.get('data_source')!r} linked_to={d.get('linked_to')!r} — "
              "an embed has no queue row to click; the host passes the id")
        check("it names an id_field", bool(d.get("id_field")))
        sections = [s.get("type") for s in (d.get("sections") or [])]
        print(f"  sections: {sections}")
        check("no dead approval section", "approval" not in sections,
              "detail.approval reads smartapp_pending_runs, which nothing writes")

    print("\n— what makes it LEARN —")
    actions = agent.get("actions") or []
    anchored = [a for a in actions if a.get("anchor_read")]
    check("the action declares anchor_read", bool(anchored),
          "without it facets derive from the run inputs — which for an embed is "
          "just the record id — so every correction lands case_facets:[] and "
          "consolidation can never author a judgement. Fails SILENTLY.")
    cs = spec.get("case_signature") or {}
    check("reason codes are authored", bool(cs.get("reason_codes")),
          "the reject taxonomy is the signal the memory runs on")
    if cs.get("facets"):
        print(f"  facets: {[f.get('family') for f in cs['facets']]}")
    else:
        warn("no facets in case_signature",
             "corrections will code a reason but not a scope")

    print("\n— what cannot render —")
    check("no chart/map on the embed page",
          not ({"chart", "map"} & set(types)),
          "the bundle aliases echarts and leaflet away; publish should have "
          "rejected these")

    print("\n— the key —")
    check("an embed key was minted", bool(doc.get("embed_key")),
          "no key means My Apps cannot hand a developer a snippet")
    if doc.get("embed_key"):
        print(f"  {doc['embed_key']}")

    print(f"\n{passed} passed, {failed} failed, {warned} warning(s)\n")
    if failed:
        print("A failure here is a SKILL problem, not a platform one: the "
              "builder had citra-embed-spec available and did not follow it.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
