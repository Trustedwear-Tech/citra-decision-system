# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Fuzz invariants: (1) generated valid specs satisfy every invariant;
(2) each injected violation is caught by the right invariant. Runs offline."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

import spec_fuzzer as F

SEEDS = 500


def test_valid_specs_are_clean():
    rng = random.Random(1)
    bad = []
    for _ in range(SEEDS):
        spec = F.gen_app(rng)
        viol = F.check_invariants(spec)
        if viol:
            bad.append((spec.get("title"), viol))
    assert not bad, f"{len(bad)}/{SEEDS} generated specs violated invariants: {bad[:3]}"


@pytest.mark.parametrize("inv", [
    "I1-unknown-panel", "I2-dangling-datasource", "I3-chart-over-rest",
    "I4-rest-no-filter", "I5-dangling-navigate",
])
def test_each_violation_is_caught(inv):
    rng = random.Random(42)
    caught = 0
    for _ in range(25):
        spec = F.gen_app(rng)
        mutated = F.mutate(spec, inv, rng)
        ids = {v[0] for v in F.check_invariants(mutated)}
        if inv in ids:
            caught += 1
    assert caught >= 20, f"{inv}: only caught in {caught}/25 mutated specs"


def test_emit_fuzz_coverage(tmp_path=None):
    """Record which panel types + invariants the fuzz run exercised → coverage."""
    rng = random.Random(3)
    panels_seen = set()
    for _ in range(SEEDS):
        spec = F.gen_app(rng)
        for p in spec["pages"]:
            for pn in p.get("panels") or []:
                panels_seen.add(pn.get("type"))
    invariants = ["I1-unknown-panel", "I2-dangling-datasource", "I3-chart-over-rest",
                  "I4-rest-no-filter", "I5-dangling-navigate"]
    out = {
        "layer": "fuzzer",
        "panel_types_generated": sorted(panels_seen),
        "invariants_checked": invariants,
    }
    # emit to the shared coverage dir the aggregator reads
    covdir = Path(__file__).resolve().parent.parent / ".coverage-cells"
    covdir.mkdir(exist_ok=True)
    (covdir / "fuzzer.json").write_text(json.dumps(out, indent=2))
    assert len(panels_seen) >= 6  # the fuzzer should span a good chunk of the vocabulary
