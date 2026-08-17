# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""NL → OData planner — extension stub.

The dept subclass that backs an SAP / Microsoft OData service overrides
this. Default implementation returns ``None`` so callers fall through
to a clear "not implemented" path (the orchestrator surfaces a 501 in
the response metadata).

When implemented, ``plan()`` should return a dict::

    {
        "entity": "PurchaseOrders",
        "$filter": "Status eq 'Open' and CreatedAt gt 2025-01-01",
        "$select": "PoNumber,Vendor,Amount",
        "$top": 50,
        "$orderby": "CreatedAt desc",
    }

which ``query_engine`` (or a dept-specific override) translates into the
HTTP request.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def plan(
    question: str,
    datasets: List[Dict[str, Any]],
    **_: Any,
) -> Optional[Dict[str, Any]]:
    logger.warning(
        "[nl_to_odata] not implemented in template — override "
        "planners.nl_to_odata.plan in a dept subclass to enable SAP/OData NL→query."
    )
    return None
