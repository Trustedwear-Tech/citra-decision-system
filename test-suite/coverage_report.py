# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Coverage aggregator — reads what each layer's tests EXERCISED (emitted as
JSON under ``.coverage-cells/``) and reports coverage against the vocabulary
denominators. This is the platform's real coverage number: "fraction of the
finite vocabulary/contract space actually exercised", not code-line %.

Each layer emits ``.coverage-cells/<layer>.json``; the schema per layer is in the
layer READMEs. Missing file = 0% for that layer (with a clear note).

    python coverage_report.py            # human-readable
    python coverage_report.py --json     # machine-readable (for CI gates)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vocabulary as V

CELLS_DIR = Path(__file__).resolve().parent / ".coverage-cells"

# per-layer (denominator, how-to-read-hits) — kept declarative
TARGETS = {"mcp": 0.90, "ui": 0.95, "builder_validators": 1.0,
           "builder_tool_kinds": 0.90, "memory": 0.90}


def _load(layer: str) -> dict | None:
    f = CELLS_DIR / f"{layer}.json"
    return json.loads(f.read_text()) if f.exists() else None


def _tuples(rows) -> set:
    return {tuple(r) for r in (rows or [])}


def compute() -> dict:
    out = {}

    # Layer 1 — MCP contract cells
    mcp = _load("mcp")
    total = V.mcp_cells()
    hit = _tuples(mcp.get("cells_hit")) & total if mcp else set()
    out["mcp"] = {"hit": len(hit), "total": len(total), "target": TARGETS["mcp"]}

    # Layer 2 — Builder: validators + tool kinds
    b = _load("builder")
    out["builder_validators"] = {
        "hit": len(set(b.get("validators_hit", [])) & set(V.PUBLISH_VALIDATORS)) if b else 0,
        "total": len(V.PUBLISH_VALIDATORS), "target": TARGETS["builder_validators"]}
    out["builder_tool_kinds"] = {
        "hit": len(set(b.get("tool_kinds_hit", [])) & set(V.TOOL_KINDS)) if b else 0,
        "total": len(V.TOOL_KINDS), "target": TARGETS["builder_tool_kinds"]}

    # Layer 3 — UI rendering matrix
    ui = _load("ui")
    total = V.ui_cells()
    hit = _tuples(ui.get("cells_hit")) & total if ui else set()
    out["ui"] = {"hit": len(hit), "total": len(total), "target": TARGETS["ui"]}

    # Layer 4 — Memory / learning
    mem = _load("memory")
    total = V.memory_cells()
    hit = _tuples(mem.get("cells_hit")) & total if mem else set()
    out["memory"] = {"hit": len(hit), "total": len(total), "target": TARGETS["memory"]}

    for k, r in out.items():
        r["pct"] = round(100 * r["hit"] / r["total"], 1) if r["total"] else 0.0
        r["pass"] = r["pct"] / 100 >= r["target"]
    return out


def main():
    rep = compute()
    if "--json" in sys.argv:
        print(json.dumps(rep, indent=2)); return
    print(f"{'layer':<22}{'covered':>12}{'pct':>8}{'target':>9}  gate")
    print("-" * 62)
    all_pass = True
    for layer, r in rep.items():
        gate = "PASS" if r["pass"] else "FAIL"
        all_pass &= r["pass"]
        print(f"{layer:<22}{r['hit']:>5}/{r['total']:<6}{r['pct']:>7}%{int(r['target']*100):>8}%  {gate}")
    print("-" * 62)
    print("OVERALL:", "PASS" if all_pass else "FAIL (below target — not production-acceptable)")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
