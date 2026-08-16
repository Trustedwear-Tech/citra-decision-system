# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Planners
========
Pre-execution planners for dept-mcp tool calls. Each planner takes a user
query (+ minimal context) and produces a small JSON plan that steers the
downstream engine (SQL generation, vector retrieval, synthesis).

No-clarification contract:
  Dept MCP serves data; it cannot ask an end-user for clarification. Every
  planner picks a documented DEFAULT from `defaults.py` when a metric is
  ambiguous, and records both `chosen_interpretation` and
  `alternative_interpretations` so the caller can surface assumptions.

All planners fail-safe: any error returns None and the caller proceeds as
if no planner existed.
"""

from .defaults import DEFAULTS, render_defaults_for_prompt
from .structured_planner import plan_sql_approach, format_plan_for_prompt
from .retrieval_planner import plan_retrieval, format_retrieval_plan_for_log

__all__ = [
    "DEFAULTS",
    "render_defaults_for_prompt",
    "plan_sql_approach",
    "format_plan_for_prompt",
    "plan_retrieval",
    "format_retrieval_plan_for_log",
]
