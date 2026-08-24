# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Default Interpretations
=======================
Canonical answers for commonly-ambiguous metrics. Dept MCP runs in
no-clarification mode: when a user query is ambiguous, the planner MUST
pick one of these defaults AND record the alternatives it rejected in
`alternative_interpretations` so the caller can surface assumptions.

Kept small on purpose — only add entries for metrics we've actually seen
produce silently-wrong answers in the field.
"""

from __future__ import annotations

from typing import Dict, List

# Each entry: metric key → (default interpretation, alternative interpretations)
DEFAULTS: Dict[str, Dict[str, object]] = {
    "pnl": {
        "default": "realized FIFO P&L (match buys and sells in FIFO order; open positions excluded)",
        "alternatives": [
            "realized LIFO P&L",
            "cash-flow P&L (SUM of signed cashflows)",
            "mark-to-market P&L (include open positions at latest price)",
        ],
    },
    "profit": {
        "default": "gross profit (revenue - cogs)",
        "alternatives": ["net profit (after opex, tax)", "operating profit (EBIT)"],
    },
    "margin": {
        "default": "gross margin = (revenue - cogs) / revenue",
        "alternatives": ["net margin", "operating margin (EBIT margin)", "contribution margin"],
    },
    "growth": {
        "default": "year-over-year growth (YoY)",
        "alternatives": ["month-over-month (MoM)", "quarter-over-quarter (QoQ)", "CAGR"],
    },
    "average": {
        "default": "arithmetic mean",
        "alternatives": ["median", "weighted average", "geometric mean"],
    },
    "churn": {
        "default": "gross customer-count churn in period (customers lost / customers at start)",
        "alternatives": ["revenue churn", "net churn (after upgrades)", "logo churn over trailing 12 months"],
    },
    "retention": {
        "default": "customer retention = 1 - gross customer-count churn",
        "alternatives": ["revenue retention", "net revenue retention (NRR)"],
    },
    "active_users": {
        "default": "monthly active users (MAU) — distinct users in the trailing 30 days",
        "alternatives": ["DAU", "WAU", "all-time distinct users"],
    },
    "conversion": {
        "default": "last-touch conversion rate",
        "alternatives": ["first-touch conversion", "multi-touch attribution"],
    },
    "top_n": {
        "default": "top N by the single metric the user named, ties broken deterministically",
        "alternatives": ["top N by a composite score", "top N per group (ROW_NUMBER per partition)"],
    },
    "time_window": {
        "default": "trailing window from the latest row in the data (NOT today)",
        "alternatives": ["calendar-to-date", "explicit date range from the question"],
    },
}


def render_defaults_for_prompt() -> str:
    """Format the defaults table as a compact prompt block."""
    lines = ["Default interpretations (use these unless the question is explicit):"]
    for key, spec in DEFAULTS.items():
        lines.append(f'  - {key}: {spec["default"]}')
    return "\n".join(lines)
