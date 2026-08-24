# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""One-off: align the published `kiln-ops-triage-acme` AppSpec panels with
the real `plant_ops_kiln_runs` MCP field names.

The builder named the chart/queue fields generically (`date`, `tpd`); the
cement dept-MCP collection actually exposes `run_date`, `actual_tpd`, etc.
This repoints the panels so the published demo app renders its 60 rows of
live kiln data instead of null columns. Idempotent — safe to re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pymongo import MongoClient

from config import Settings

SLUG = "kiln-ops-triage-acme"

s = Settings()
client = MongoClient(s.mongo_uri)
col = client[s.mongo_db][s.apps_collection]

doc = col.find_one({"slug": SLUG})
if not doc:
    raise SystemExit(f"app {SLUG} not found")

spec = doc["app_spec"]
changes: list[str] = []

for page in spec.get("pages", []):
    for panel in page.get("panels", []):
        pid, ptype = panel.get("id"), panel.get("type")

        if ptype == "chart" and pid == "tpd_trend":
            if panel.get("x") != "run_date":
                panel["x"] = "run_date"
                changes.append("tpd_trend.x -> run_date")
            if panel.get("y") != "actual_tpd":
                panel["y"] = "actual_tpd"
                changes.append("tpd_trend.y -> actual_tpd")

        if ptype == "queue" and pid == "flagged_runs":
            # Repoint at the unfiltered kiln_runs source (the raw MCP
            # collection has no app-decision `status` field) and use the
            # real field names so the queue renders live runs.
            if panel.get("data_source") != "kiln_runs":
                panel["data_source"] = "kiln_runs"
                changes.append("flagged_runs.data_source -> kiln_runs")
            real_cols = [
                "kiln_id", "run_date", "actual_tpd", "rated_tpd",
                "hours_run", "downtime_reason", "oee_pct",
            ]
            if panel.get("columns") != real_cols:
                panel["columns"] = real_cols
                changes.append("flagged_runs.columns -> real MCP fields")

        if ptype == "dashboard" and pid == "kpi_tiles":
            for m in panel.get("metrics", []):
                if m.get("field") == "tpd":
                    m["field"] = "actual_tpd"
                    changes.append(f"kpi_tiles metric {m.get('name')!r}.field -> actual_tpd")

if not changes:
    print("no changes needed — already aligned")
else:
    col.update_one({"slug": SLUG}, {"$set": {"app_spec": spec}})
    print("patched:")
    for c in changes:
        print(f"  - {c}")

client.close()
