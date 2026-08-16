# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Clear stale memory cells at session start; fold per-cell files into
memory.json at session end (mirror of layer1_mcp/conftest.py)."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _memcells import MEM_DIR, aggregate_memory  # noqa: E402


def pytest_sessionstart(session):
    shutil.rmtree(MEM_DIR, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    n = aggregate_memory()
    print(f"\n[coverage] wrote memory.json with {n} cells")
