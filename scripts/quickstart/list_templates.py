#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""List the starter ontology templates — read from the files, never hard-coded.

`source-mcp-template/templates/` is the source of truth. Adding a template there
makes it appear in the wizard with no code change here, which is the point: a
hard-coded menu silently omits whatever ships next.

    python list_templates.py            # numbered menu for humans
    python list_templates.py --json     # machine-readable
    python list_templates.py --path 2   # resolve a choice to a file path

Nothing here restricts what a deployment may declare. The domain ontology is
GLOBAL — any industry, any ISO country — and `domain` is optional entirely.
These are accelerators for the cells that happen to ship a template, and worked
examples of the grammar for everyone else.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "source-mcp-template" / "templates"


def _sources(doc: dict) -> list:
    """Templates may be {"sources": [...]} or a bare list."""
    if isinstance(doc, list):
        return doc
    return doc.get("sources") or []


def _summarise(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — a broken template must not hide the rest
        print(f"  ! {path.name}: unreadable ({exc})", file=sys.stderr)
        return None

    srcs = _sources(doc)
    dom = next((s.get("domain") for s in srcs if s.get("domain")), None) or {}

    datasets = sum(len(s.get("datasets") or []) for s in srcs)
    # What the template actually demonstrates, derived from the file itself.
    checks: set[str] = set()
    for s in srcs:
        for ds in s.get("datasets") or []:
            fs = ds.get("fraud_screening") or {}
            if fs.get("applies"):
                checks.add("fraud screening")
            if fs.get("payment_proof"):
                checks.add("payment-proof vs ledger")
            if fs.get("verify_against"):
                checks.add("cross-document verify")
            if ds.get("decision_history"):
                checks.add("decision history")
            if ds.get("value_semantics"):
                checks.add("ROI / money impact")
            if (ds.get("kind") or "").lower() == "rest":
                checks.add("API-as-dataset")
            for col in ds.get("columns") or []:
                if col.get("artifact_role"):
                    checks.add("artifact roles")

    return {
        "file": path.name,
        "path": str(path),
        "vertical": dom.get("vertical"),
        "sub_vertical": dom.get("sub_vertical"),
        "country": dom.get("country"),
        "sources": len(srcs),
        "datasets": datasets,
        "demonstrates": sorted(checks),
    }


def load() -> list[dict]:
    if not TEMPLATES.is_dir():
        return []
    out = [_summarise(p) for p in sorted(TEMPLATES.glob("*.sources.json"))]
    return [o for o in out if o]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--path", type=int, metavar="N", help="print the file path for choice N")
    ap.add_argument("--count", action="store_true", help="print how many templates exist")
    args = ap.parse_args()

    items = load()
    if not items:
        print(f"No templates found in {TEMPLATES}", file=sys.stderr)
        return 1

    if args.count:
        print(len(items))
        return 0

    if args.path is not None:
        if not 1 <= args.path <= len(items):
            print(f"choice {args.path} out of range (1-{len(items)})", file=sys.stderr)
            return 2
        print(items[args.path - 1]["path"])
        return 0

    if args.json:
        print(json.dumps(items, indent=2))
        return 0

    width = max(len(f"{i['vertical']} / {i['sub_vertical']}") for i in items)
    for n, i in enumerate(items, 1):
        cell = f"{i['vertical']} / {i['sub_vertical']}"
        print(f"  {n}) {cell:<{width}}  {i['country']}   "
              f"{i['datasets']} datasets")
        if i["demonstrates"]:
            print(f"     {'':<{width}}  shows: {', '.join(i['demonstrates'])}")
    print(f"  {len(items) + 1}) none of these - my industry is different")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
