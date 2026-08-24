# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""NL → REST planner — extension stub.

Dept subclasses targeting OpenAPI-described REST endpoints override this.
When implemented, ``plan()`` returns::

    {"method": "GET", "path": "/orders", "params": {"status": "open"}}

The orchestrator hands the dict to ``query_engine`` (or a dept override)
which performs the HTTP request and shapes the JSON response into rows.
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
        "[nl_to_rest] not implemented in template — override "
        "planners.nl_to_rest.plan in a dept subclass to enable REST NL→query."
    )
    return None
