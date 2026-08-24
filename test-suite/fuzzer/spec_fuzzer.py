# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Spec fuzzer — generate random *valid* AppSpecs from the vocabulary, and a set
of structural INVARIANTS every spec must satisfy. This is how we cover the huge
permutation space cheaply: instead of hand-writing apps, generate thousands and
assert the invariants hold; inject a violation and assert it's caught.

The invariants mirror what the real publish validators + runtime enforce, and
deliberately encode the two REST-source bugs found in the directory-lookup
builder test, so those can never silently reappear:
  * a queue/detail over a `rest_api` dataset MUST bind the required param in
    `filters` (else the source read 502s);
  * NO chart/aggregation panel over a `rest_api` dataset (it can't be grouped).

Pure Python, deterministic (seeded) — no service imports, runs anywhere.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vocabulary as V  # noqa: E402

# Source kinds and whether they are parameterised (need a required-param filter).
PARAMETERISED_KINDS = {"rest", "odata", "soql"}
AGGREGATABLE_KINDS = {"sql", "bigquery", "duckdb", "mongodb"}  # charts OK here


# ── generator ───────────────────────────────────────────────────────────────
def gen_app(rng: random.Random, *, allow_rest: bool = True) -> Dict[str, Any]:
    """Generate a random, INTENDED-VALID AppSpec dict."""
    n_sources = rng.randint(1, 3)
    data_sources: List[Dict[str, Any]] = []
    for i in range(n_sources):
        kinds = list(V.SOURCE_KINDS) if allow_rest else [k for k in V.SOURCE_KINDS if k != "rest"]
        kind = rng.choice(kinds)
        ds: Dict[str, Any] = {
            "id": f"ds_{i}", "type": "mcp", "ref": f"src{i}.dataset{i}", "_kind": kind,
        }
        # A parameterised source MUST carry a required-param filter (see invariant).
        if kind in PARAMETERISED_KINDS:
            ds["filters"] = {"key": "{param.key}"}
        data_sources.append(ds)

    n_pages = rng.randint(1, 3)
    pages: List[Dict[str, Any]] = []
    page_ids = [f"pg_{i}" for i in range(n_pages)]
    for pi, pid in enumerate(page_ids):
        panels: List[Dict[str, Any]] = []
        for _ in range(rng.randint(1, 3)):
            ptype = rng.choice(V.PANEL_TYPES)
            panel: Dict[str, Any] = {"id": f"pn_{pi}_{len(panels)}", "type": ptype}
            if ptype in ("queue", "detail", "chart", "document_view"):
                ds = rng.choice(data_sources)
                panel["data_source"] = ds["id"]
                # a chart is only generated over an aggregatable source (valid)
                if ptype == "chart":
                    agg = [d for d in data_sources if d["_kind"] in AGGREGATABLE_KINDS]
                    if not agg:
                        panel["type"] = ptype = "queue"  # fall back — no chart over non-agg
                    else:
                        panel["data_source"] = rng.choice(agg)["id"]
                        panel["x"] = "col_a"; panel["y"] = "col_b"
            if ptype == "form":
                panel["schema_inline"] = {"type": "object", "properties": {"key": {"type": "string"}}}
                # navigate to a real page
                target = rng.choice(page_ids)
                panel["on_submit"] = {"navigate": {"page": target, "params": {"key": "{form.key}"}}}
            panels.append(panel)
        pages.append({"id": pid, "kind": "standard", "panels": panels})

    return {
        "spec_version": "v0", "kind": "app", "title": "Fuzzed App", "audience": "org",
        "data_sources": [{k: v for k, v in d.items() if k != "_kind"} for d in data_sources],
        "_source_kinds": {d["id"]: d["_kind"] for d in data_sources},  # test-only sidecar
        "pages": pages,
    }


# ── invariants (return a list of (invariant_id, detail) violations) ─────────
def check_invariants(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    v: List[Tuple[str, str]] = []
    ds_ids = {d["id"] for d in spec.get("data_sources") or []}
    kinds = spec.get("_source_kinds") or {}
    page_ids = {p["id"] for p in spec.get("pages") or []}

    for p in spec.get("pages") or []:
        for pn in p.get("panels") or []:
            pt = pn.get("type")
            # I1: panel type must be in the vocabulary
            if pt not in V.PANEL_TYPES:
                v.append(("I1-unknown-panel", f"{pn.get('id')}: {pt}"))
            dsid = pn.get("data_source")
            # I2: data_source ref must be declared
            if dsid is not None and dsid not in ds_ids:
                v.append(("I2-dangling-datasource", f"{pn.get('id')} -> {dsid}"))
            kind = kinds.get(dsid)
            # I3: chart/aggregation NOT allowed over a rest_api-ish (parameterised) source
            if pt == "chart" and kind in PARAMETERISED_KINDS:
                v.append(("I3-chart-over-rest", f"{pn.get('id')} chart over {kind} ds {dsid}"))
            # I4: a queue/detail over a parameterised source MUST bind its param filter
            if pt in ("queue", "detail") and kind in PARAMETERISED_KINDS:
                ds = next((d for d in spec["data_sources"] if d["id"] == dsid), {})
                if not (ds.get("filters") or {}):
                    v.append(("I4-rest-no-filter", f"{pn.get('id')} over {kind} ds {dsid} has no filters"))
            # I5: a form's navigate target must exist
            nav = (pn.get("on_submit") or {}).get("navigate") or {}
            if nav.get("page") and nav["page"] not in page_ids:
                v.append(("I5-dangling-navigate", f"{pn.get('id')} -> page {nav['page']}"))
    return v


# ── mutators — inject one specific violation, to prove the checks catch it ───
def mutate(spec: Dict[str, Any], kind: str, rng: random.Random) -> Dict[str, Any]:
    import copy
    s = copy.deepcopy(spec)
    panels = [(p, pn) for p in s["pages"] for pn in p.get("panels") or []]
    if kind == "I2-dangling-datasource":
        for _, pn in panels:
            if pn.get("data_source"):
                pn["data_source"] = "ds_DOES_NOT_EXIST"; return s
        panels[0][1]["data_source"] = "ds_DOES_NOT_EXIST"
    elif kind == "I3-chart-over-rest":
        s["data_sources"].append({"id": "ds_rest", "type": "mcp", "ref": "b.cibil"})
        s["_source_kinds"]["ds_rest"] = "rest"
        s["pages"][0]["panels"].append({"id": "bad_chart", "type": "chart", "data_source": "ds_rest", "x": "a", "y": "b"})
    elif kind == "I4-rest-no-filter":
        s["data_sources"].append({"id": "ds_rest2", "type": "mcp", "ref": "b.cibil"})  # no filters
        s["_source_kinds"]["ds_rest2"] = "rest"
        s["pages"][0]["panels"].append({"id": "bad_q", "type": "queue", "data_source": "ds_rest2"})
    elif kind == "I5-dangling-navigate":
        s["pages"][0]["panels"].append({"id": "bad_form", "type": "form",
            "schema_inline": {"type": "object", "properties": {}},
            "on_submit": {"navigate": {"page": "pg_NOPE", "params": {}}}})
    elif kind == "I1-unknown-panel":
        s["pages"][0]["panels"].append({"id": "bad_panel", "type": "hologram"})
    return s


if __name__ == "__main__":
    rng = random.Random(7)
    clean = sum(1 for _ in range(500) if not check_invariants(gen_app(rng)))
    print(f"500 fuzzed valid specs → {clean} clean (expect 500)")
