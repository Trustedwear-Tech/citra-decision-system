# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Clear stale MCP cells at session start; fold per-cell files into mcp.json at
session end. Mirrors the UI layer's globalSetup/globalTeardown."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _emit import MCP_DIR, aggregate_mcp  # noqa: E402


def pytest_sessionstart(session):
    shutil.rmtree(MCP_DIR, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    n = aggregate_mcp()
    print(f"\n[coverage] wrote mcp.json with {n} cells")
