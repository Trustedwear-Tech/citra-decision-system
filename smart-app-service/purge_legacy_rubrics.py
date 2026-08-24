# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Drop the legacy ``smartapp_analysis_rubrics`` collection.

The single-summary rubric is gone from the code (see analysis_rubrics.py).
This removes its data. Nothing reads the collection any more, so leaving it
would just be a decommissioned store that looks live to the next person who
greps for it.

**Run the backfill FIRST.** ``corrections[]`` inside those documents is the
only surviving copy of some officer reasons — the rubric ``summary`` is the
damaged derivative, not the source. This script refuses to drop a bucket whose
corrections have not been promoted into ``smartapp_corrections``, so the order
cannot be got wrong by accident:

    python backfill_clause_memory.py --all
    python purge_legacy_rubrics.py --all --confirm

The ``summary`` text itself is deliberately NOT preserved. It is a lossy
re-encode of the corrections underneath it (plan §1) — keeping it would invite
someone to "restore" from the worse copy.

Both environments are swept, since the collection is env-routed and a test-env
copy would otherwise survive the purge invisibly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any, Dict, List

log = logging.getLogger("purge_legacy_rubrics")

_LEGACY = "smartapp_analysis_rubrics"


async def audit(db) -> List[Dict[str, Any]]:
    """Per-bucket: how many corrections exist, and how many were promoted."""
    out: List[Dict[str, Any]] = []
    async for d in db[_LEGACY].find({}, {"_id": 0}):
        entries = d.get("corrections") or []
        promoted = await db["smartapp_corrections"].count_documents({
            "tenant_id": d.get("tenant_id"), "app_slug": d.get("app_slug"),
            "modality": d.get("modality"), "task_type": d.get("task_type"),
        })
        out.append({
            "app_slug": d.get("app_slug"), "modality": d.get("modality"),
            "task_type": d.get("task_type"),
            "corrections": len(entries), "promoted": promoted,
            "safe": promoted >= len(entries),
        })
    return out


async def purge(db, *, confirm: bool) -> Dict[str, Any]:
    rows = await audit(db)
    unsafe = [r for r in rows if not r["safe"]]
    result = {"buckets": len(rows), "unsafe": len(unsafe), "dropped": False,
              "rows": rows}
    if unsafe:
        result["error"] = (
            "refusing to drop — these buckets hold corrections that were never "
            "promoted; run backfill_clause_memory.py first")
        return result
    if not confirm:
        result["note"] = "dry run — pass --confirm to drop"
        return result
    await db[_LEGACY].drop()
    result["dropped"] = True
    log.warning("[PURGE] dropped %s (%d bucket(s))", _LEGACY, len(rows))
    return result


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", required=True,
                    help="explicit opt-in; the drop is fleet-wide by nature")
    ap.add_argument("--confirm", action="store_true",
                    help="actually drop (default is a dry run)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import main
    from env_context import set_current_env

    if main._db is None:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(os.environ["MONGO_URI"])
        main._db = client[os.environ.get("MONGO_DB", "dev")]

    rc = 0
    for env in ("prod", "test"):
        set_current_env(env)
        name = _LEGACY if env == "prod" else main._test_collection_name(_LEGACY)
        if name not in await main._db.list_collection_names():
            print(f"\n[{env}] {name}: absent — nothing to purge")
            continue

        class _Shim:
            """Routes EVERY collection for the environment being swept.

            Routing only the legacy collection would check promotion against the
            PROD corrections ledger while reading TEST rubrics — the guard would
            then either block forever or, worse, wave through a drop because a
            same-named prod bucket happened to be migrated."""

            def __getitem__(self, key):
                if key == _LEGACY:
                    return main._db[name]
                routed = (main._test_collection_name(key)
                          if env == "test" else key)
                return main._db[routed]

        res = await purge(_Shim(), confirm=args.confirm)
        print(f"\n[{env}] {name}")
        for r in res["rows"]:
            flag = "ok " if r["safe"] else "!! "
            print(f"  {flag}{r['app_slug']:32} {r['modality']:9} "
                  f"{r['task_type']:24} corrections={r['corrections']:3} "
                  f"promoted={r['promoted']:3}")
        if res.get("error"):
            print(f"  ERROR: {res['error']}")
            rc = 1
        elif res["dropped"]:
            print("  DROPPED")
        else:
            print(f"  {res.get('note')}")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
