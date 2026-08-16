# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Multi-file-safe memory-layer coverage emission (mirror of layer1_mcp/_emit).

Each test writes one file per (signal, outcome_class) cell to
../.coverage-cells/memory/<key>.json; conftest folds them into memory.json.
"""
from __future__ import annotations

import json
from pathlib import Path

CELLS_DIR = Path(__file__).resolve().parents[1] / ".coverage-cells"
MEM_DIR = CELLS_DIR / "memory"


def emit_memory_cell(signal: str, outcome_class: str) -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    (MEM_DIR / f"{signal}__{outcome_class}.json").write_text(
        json.dumps([signal, outcome_class]))


def aggregate_memory() -> int:
    cells = []
    if MEM_DIR.exists():
        for f in MEM_DIR.glob("*.json"):
            cells.append(json.loads(f.read_text()))
    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    (CELLS_DIR / "memory.json").write_text(
        json.dumps({"layer": "memory", "cells_hit": cells}, indent=2))
    return len(cells)
