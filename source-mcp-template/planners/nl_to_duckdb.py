# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""NL → DuckDB SQL planner.

Thin wrapper around :mod:`planners.nl_to_sql` that pins the dialect to
DuckDB. Catalogue datasets of ``kind == "duckdb"`` (file-backed Excel /
CSV / Parquet) use the same column hints + sample rows but the generated
SQL may use DuckDB-native functions (e.g. ``strftime``, ``date_trunc``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from planners import nl_to_sql


async def plan(
    question: str,
    datasets: List[Dict[str, Any]],
    *,
    row_limit: int = 50,
    extra_plan: Optional[Dict[str, Any]] = None,
    previous_attempt: Optional[Dict[str, Any]] = None,
    examples_block: str = "",
) -> Optional[str]:
    return await nl_to_sql.plan(
        question,
        datasets,
        dialect="duckdb",
        row_limit=row_limit,
        extra_plan=extra_plan,
        previous_attempt=previous_attempt,
        examples_block=examples_block,
    )
