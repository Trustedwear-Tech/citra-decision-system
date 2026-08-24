#!/usr/bin/env python
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""One-off migration: retire AppSpec.kind == 'dashboard'.

The 'dashboard' artefact kind is gone. A dashboard is now a kind='app'
whose primary page has page.kind='dashboard'. This script rewrites every
persisted dashboard in the smartapp_apps collection to the new shape:

  app_spec.kind            : 'dashboard' -> 'app'
  app_spec.pages[0].kind   : set to 'dashboard' (first/landing page)
  panel-only dashboards    : panels[] wrapped into one dashboard page
  version                  : bumped (append-only history)

It is idempotent: docs already kind='app' (or with a dashboard page) are
skipped. Read-only by default — pass --apply to write.

Usage:
    python scripts/migrate_dashboard_kind.py            # dry run (report only)
    python scripts/migrate_dashboard_kind.py --apply    # perform the writes

Reads MONGO_URI / MONGO_DB / APPS_COLLECTION from the environment (same as
the service). Run against `dev` first, eyeball the rendered apps, then run
against `prod`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:  # pragma: no cover
    sys.stderr.write("motor is required (pip install motor)\n")
    raise


def _coerce_app_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a NEW app_spec dict migrated to the page-kind model.

    Mirrors AppSpec._coerce_legacy_dashboard_kind so the migrated docs match
    exactly what the model would have produced on read.
    """
    spec = dict(spec)
    spec["kind"] = "app"
    if spec.get("pages"):
        pages = [dict(p) for p in spec["pages"]]
        pages[0].setdefault("kind", "dashboard")
        spec["pages"] = pages
    elif spec.get("panels"):
        spec["pages"] = [
            {
                "id": "overview",
                "path": "/",
                "title": spec.get("title") or "Overview",
                "kind": "dashboard",
                "panels": spec["panels"],
            }
        ]
        spec["panels"] = []
    return spec


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the writes. Without this flag the script only reports.",
    )
    args = parser.parse_args()

    mongo_uri = os.getenv("MONGO_URI")
    mongo_db = os.getenv("MONGO_DB", "dev")
    apps_collection = os.getenv("APPS_COLLECTION", "smartapp_apps")
    if not mongo_uri:
        sys.stderr.write("MONGO_URI is not set. Aborting.\n")
        return 2

    client = AsyncIOMotorClient(mongo_uri)
    apps = client[mongo_db][apps_collection]

    cursor = apps.find({"app_spec.kind": "dashboard"})
    migrated: List[str] = []
    skipped: List[str] = []
    async for doc in cursor:
        spec = doc.get("app_spec") or {}
        slug = spec.get("slug") or doc.get("slug") or str(doc.get("_id"))
        if spec.get("kind") != "dashboard":
            skipped.append(slug)
            continue
        new_spec = _coerce_app_spec(spec)
        new_version = int(doc.get("version") or new_spec.get("version") or 1) + 1
        new_spec["version"] = new_version
        print(
            f"  dashboard -> app + dashboard page: slug={slug!r} "
            f"version {doc.get('version')} -> {new_version}"
        )
        if args.apply:
            await apps.update_one(
                {"_id": doc["_id"]},
                {"$set": {"app_spec": new_spec, "version": new_version}},
            )
        migrated.append(slug)

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(
        f"\n{mode}: {len(migrated)} dashboard app(s) migrated, "
        f"{len(skipped)} skipped (already app)."
    )
    if not args.apply and migrated:
        print("Re-run with --apply to perform the writes.")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
