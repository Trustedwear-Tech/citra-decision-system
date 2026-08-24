# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Multi-file-safe MCP coverage emission.

Each test writes one tiny file per (kind, op, state) cell to
../.coverage-cells/mcp/<key>.json; conftest.py folds them into
../.coverage-cells/mcp.json at session end. Distinct filenames mean two test
modules (the REST matrix + the all-kinds contract) never clobber each other.
"""
from __future__ import annotations

import json
from pathlib import Path

CELLS_DIR = Path(__file__).resolve().parents[1] / ".coverage-cells"
MCP_DIR = CELLS_DIR / "mcp"


def emit_mcp_cell(kind: str, op: str, state: str) -> None:
    MCP_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{kind}__{op}__{state}".replace("/", "_")
    (MCP_DIR / f"{key}.json").write_text(json.dumps([kind, op, state]))


def aggregate_mcp() -> int:
    cells = []
    if MCP_DIR.exists():
        for f in MCP_DIR.glob("*.json"):
            cells.append(json.loads(f.read_text()))
    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    (CELLS_DIR / "mcp.json").write_text(json.dumps({"layer": "mcp", "cells_hit": cells}, indent=2))
    return len(cells)
