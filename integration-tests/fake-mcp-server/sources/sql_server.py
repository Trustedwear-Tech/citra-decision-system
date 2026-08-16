# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Generic SQL passthrough — minimal stub. Most tests don't exercise this."""
from __future__ import annotations

from typing import Any, Dict, List


def passthrough_sql(query: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Echo the query + filters back. Tests only assert the contract works."""
    return [
        {"_meta": "fake_sql_server", "query": query, "filters": dict(filters)}
    ]
