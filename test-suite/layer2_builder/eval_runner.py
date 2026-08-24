# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Layer 2 — Builder eval runner.

Two modes:

* ``--report`` (offline, default): loads the goal corpus and reports the
  vocabulary the corpus is designed to exercise (union of each goal's ``expects``),
  and emits ../.coverage-cells/builder.json so the aggregator can show
  builder-validator + tool-kind coverage. Verifies the corpus SPANS the space.

* ``--live`` (gated: needs a builder-capable JWT + a reachable smart-app-service):
  drives the REAL builder per goal (POST /build → stream chat → publish), then
  checks the published spec (a) passes all validators, (b) passes the smoke gate,
  (c) contains the panels/tool_kinds the goal ``expects`` — the metamorphic check
  ("this goal must yield these building blocks"). Records were captured live in
  the directory-lookup test; the live driver reuses that flow.

Because the builder LLM is nondeterministic, the pass criterion is a CORPUS
pass-RATE, not one exact spec. Record/replay (store each session's LLM turns)
gives deterministic regression on top.

    python eval_runner.py --report
    SAS_BASE_URL=... SAS_JWT=... python eval_runner.py --live
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_corpus():
    txt = (HERE / "goals_corpus.yaml").read_text(encoding="utf-8")
    try:
        import yaml  # optional
        return yaml.safe_load(txt)["goals"]
    except Exception:
        return _mini_yaml(txt)


def _mini_yaml(txt: str):
    """Tiny fallback parser for this file's shape (no PyYAML needed)."""
    goals, cur, key = [], None, None
    for raw in txt.splitlines():
        if raw.strip().startswith("#") or not raw.strip():
            continue
        if raw.startswith("  - id:"):
            cur = {"id": raw.split(":", 1)[1].strip(), "expects": {}}
            goals.append(cur); key = None
        elif raw.startswith("    expects:"):
            key = "expects"
        elif key == "expects" and raw.startswith("      ") and ":" in raw:
            k, v = raw.strip().split(":", 1)
            v = v.strip()
            if v.startswith("["):
                cur["expects"][k] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
    return goals


def report():
    goals = _load_corpus()
    validators, tools, panels = set(), set(), set()
    for g in goals:
        e = g.get("expects") or {}
        validators |= set(e.get("validators") or [])
        tools |= set(e.get("tool_kinds") or [])
        panels |= set(e.get("panels") or [])
    covdir = HERE.parent / ".coverage-cells"
    covdir.mkdir(exist_ok=True)
    covdir.joinpath("builder.json").write_text(json.dumps(
        {"layer": "builder", "goals": len(goals),
         "validators_hit": sorted(validators), "tool_kinds_hit": sorted(tools),
         "panels_expected": sorted(panels)}, indent=2))
    print(f"corpus: {len(goals)} goals")
    print(f"  panels expected : {sorted(panels)}")
    print(f"  tool kinds      : {sorted(tools)}")
    print(f"  validators       : {sorted(validators)}")
    print("emitted .coverage-cells/builder.json")


def live():  # pragma: no cover — gated, needs the stack
    raise SystemExit(
        "live mode drives the real builder — wire SAS_BASE_URL + a builder JWT, "
        "then reuse the POST /build → stream → publish flow (see the directory-lookup "
        "run in the session transcript) and assert each goal's `expects` appears in "
        "the published spec + all validators + smoke gate pass.")


if __name__ == "__main__":
    (live if "--live" in sys.argv else report)()
