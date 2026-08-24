# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

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
