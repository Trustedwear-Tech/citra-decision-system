# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Grade what the BUILDER authored for each surface.

    MONGO_URI=... MONGO_DB=dev python scripts/grade_all_surfaces.py

Reads the most recent published app per surface out of the TEST store and
checks the shape against what that surface is supposed to be. Every check below
exists because getting it wrong fails in a specific way that publishing does not
catch.

Also grades the CONVERSATION: whether the builder re-asked a surface question
the BA had already answered in the picker. That is the fix this run exists to
test, and it is invisible in the spec.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Apps seeded or published by hand earlier — never grade these as builder output.
SEEDED = {"embed-e2e-loan", "loan-credit-decision"}

_passed = _failed = 0


def chk(name, ok, why=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"    ok    {name}")
    else:
        _failed += 1
        print(f"    FAIL  {name}" + (f"\n            {why}" if why else ""))


def _panels(spec, kind=None):
    out = []
    for pg in (spec.get("pages") or []):
        if kind is None or pg.get("kind") == kind:
            out += (pg.get("panels") or [])
    return out


def grade_embed(spec, doc):
    pans = _panels(spec, "embed")
    types = [p.get("type") for p in pans]
    print(f"    panels: {types}")
    chk("an embed page was authored", bool(pans),
        f"page kinds: {[p.get('kind') for p in (spec.get('pages') or [])]}")
    if not pans:
        return
    chk("NO queue on the embed page", "queue" not in types,
        "a queue pinned to one record renders dead controls and repeats the "
        "detail panel's fields — citra-embed-spec says detail only")
    det = [p for p in pans if p.get("type") == "detail"]
    chk("a detail panel carries the record", bool(det))
    if det:
        d = det[0]
        chk("the trigger is on the detail panel",
            any(a.get("agent_action") for a in (d.get("actions") or [])),
            "detail.actions[].agent_action is what runs the agent now")
        chk("bound with data_source, not linked_to",
            bool(d.get("data_source")) and not d.get("linked_to"),
            f"data_source={d.get('data_source')!r} linked_to={d.get('linked_to')!r}")
        chk("no dead approval section",
            "approval" not in [s.get("type") for s in (d.get("sections") or [])],
            "detail.approval reads a collection nothing writes to")
    chk("no chart/map on the embed page", not ({"chart", "map"} & set(types)),
        "the embed bundle excludes echarts and leaflet")
    chk("an embed key was minted", bool(doc.get("embed_key")))


def grade_app(spec, doc):
    pans = _panels(spec, "standard") or _panels(spec)
    types = [p.get("type") for p in pans]
    print(f"    panels: {types}")
    chk("a queue is present", "queue" in types, "the officer's worklist")
    chk("a detail is present", "detail" in types)
    chk("no embed page", not any(p.get("kind") == "embed" for p in (spec.get("pages") or [])))
    chk("no embed key minted for a UI app", not doc.get("embed_key"),
        f"embed_key={doc.get('embed_key')!r} — a UI app is not an external surface")


def grade_dashboard(spec, doc):
    pans = _panels(spec, "dashboard")
    types = [p.get("type") for p in pans]
    print(f"    panels: {types}")
    chk("a dashboard page was authored", bool(pans),
        f"page kinds: {[p.get('kind') for p in (spec.get('pages') or [])]}")
    # THE regression this run exists to rule out: the embed fix made the chart
    # injector skip embed pages. A guard one notch too broad would silently
    # stop charting dashboards.
    chk("the dashboard has a chart", "chart" in types,
        "a dashboard with no chart is the shape the embed chart-guard could "
        "have broken")
    chk("a narrator agent is declared", bool(spec.get("agent_id")),
        "the hero-brief copilot needs agent_id")


def grade_api(spec, doc):
    chk("headless is set", spec.get("headless") is True,
        f"headless={spec.get('headless')!r}")
    chk("NO pages authored", not spec.get("pages"),
        f"pages={[p.get('id') for p in (spec.get('pages') or [])]} — "
        "building panels for a headless app is the documented #1 mistake")
    chk("NO panels authored", not spec.get("panels"))


GRADERS = {"embed": grade_embed, "app": grade_app,
           "dashboard": grade_dashboard, "api": grade_api}

#: The builder asking this again means it ignored the BA's picker choice.
_RE_ASK = re.compile(
    r"three ways I can build|which do you want|Decision App .*Embedded card|"
    r"embedded card.*headless Decision API", re.I)


async def main() -> int:
    uri = os.getenv("MONGO_URI")
    if not uri:
        env = (Path(__file__).resolve().parents[1] / ".env").read_text(
            encoding="utf-8", errors="ignore")
        uri = re.search(r"^MONGO_URI=(.*)$", env, re.M).group(1).strip()
    db = AsyncIOMotorClient(uri)[os.getenv("MONGO_DB", "dev")]

    sessions = db["test_smartapp_build_sessions"]
    apps = db["test_smartapp_apps"]

    for surface in ("embed", "app", "dashboard", "api"):
        print(f"\n{'='*66}\n{surface.upper()}\n{'='*66}")
        # The driver mints ba-<surface>-<n>@… so the owner identifies the run.
        sess = await sessions.find_one(
            {"owner": {"$regex": f"work-ba-{surface}-"}},
            sort=[("started_at", -1)])
        if not sess:
            print("    (no build session found — did the driver run?)")
            _fail_surface()
            continue

        # ── conversation: was the already-answered question re-asked? ──
        turns = sess.get("transcript") or []
        asked = [t for t in turns
                 if _RE_ASK.search(str(t.get("content") or t.get("text") or ""))]
        if surface == "api" or surface in ("embed", "dashboard"):
            chk("did NOT re-ask the surface the BA picked", not asked,
                f"{len(asked)} turn(s) re-asked it — the picker choice was ignored")
        else:
            print("    n/a   'standard' is ambiguous (App vs talk-it-through); "
                  "asking is CORRECT here")

        doc = await apps.find_one({"slug": {"$nin": list(SEEDED)},
                                   "app_id": sess.get("app_id")}) if sess.get("app_id") else None
        if doc is None:
            doc = await apps.find_one(
                {"slug": {"$nin": list(SEEDED)}}, sort=[("deployed_at", -1)])
        if not doc:
            print("    FAIL  nothing published for this surface")
            _fail_surface()
            continue
        print(f"    app: {doc.get('slug')}  v{doc.get('version')}")
        GRADERS[surface](doc.get("app_spec") or {}, doc)

    print(f"\n\n{_passed} passed, {_failed} failed\n")
    if _failed:
        print("A failure here is a SKILL / AGENTS.md problem, not a platform "
              "one: the builder had the guidance and did not follow it.\n")
    return 1 if _failed else 0


def _fail_surface():
    global _failed
    _failed += 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
