# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Fake Salesforce opportunities source — used by the dashboard narrator tests."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


_SEED = 20260510
_REGIONS = ["North", "South", "East", "West", "Central"]
_STAGES = ["Prospecting", "Qualification", "Proposal", "Negotiation",
           "Closed Won", "Closed Lost"]


_OPPS: List[Dict[str, Any]] = []


def _generate_opps() -> List[Dict[str, Any]]:
    rng = random.Random(_SEED)
    out: List[Dict[str, Any]] = []
    base = datetime(2024, 1, 1)

    # Region weights deliberately uneven so anomaly tests have something
    # to find: South has a 3x normal in Q3 (months 7-9).
    for i in range(1000):
        close_date = base + timedelta(days=rng.randint(0, 364))
        month = close_date.month
        region = rng.choice(_REGIONS)
        stage = rng.choices(_STAGES, weights=[0.10, 0.20, 0.25, 0.20, 0.20, 0.05])[0]

        # Inject the anomaly: South region in Q3 has 3x amount on average.
        amount_base = rng.choice([5_000, 12_000, 25_000, 80_000, 150_000])
        if region == "South" and 7 <= month <= 9:
            amount_base = int(amount_base * 3)

        out.append({
            "Id": f"006{i:07x}",
            "Name": f"Opp #{i:04d} - {region}",
            "Amount": amount_base,
            "StageName": stage,
            "CloseDate": close_date.date().isoformat(),
            "AccountId": f"001{rng.randint(0, 99):07x}",
            "Region": region,
        })
    return out


def reset_salesforce() -> None:
    global _OPPS
    _OPPS = _generate_opps()


reset_salesforce()


def seed_count() -> int:
    return len(_OPPS)


def query_opportunities(
    *,
    filters: Dict[str, Any],
    max_results: int,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int, Optional[int]]:
    rows = _OPPS
    if filters:
        rows = [r for r in rows if _matches(r, filters)]
    total = len(rows)
    page = rows[offset : offset + max_results]
    next_offset = (offset + len(page)) if (offset + len(page)) < total else None
    return page, total, next_offset


def _matches(row: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    if "Region" in filters and row["Region"] != filters["Region"]:
        return False
    if "StageName" in filters and row["StageName"] != filters["StageName"]:
        return False
    if "min_amount" in filters and row["Amount"] < filters["min_amount"]:
        return False
    if "max_amount" in filters and row["Amount"] > filters["max_amount"]:
        return False
    if "date_range" in filters and ".." in str(filters["date_range"]):
        lo, hi = filters["date_range"].split("..", 1)
        if row["CloseDate"] < lo or row["CloseDate"] > hi:
            return False
    return True
