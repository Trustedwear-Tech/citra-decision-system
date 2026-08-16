"""NL → SOQL planner — extension stub.

Dept subclasses for Salesforce override this. When implemented,
``plan()`` returns a SOQL string such as::

    SELECT Id, Name, Amount FROM Opportunity
    WHERE StageName = 'Closed Won' AND CloseDate = LAST_N_DAYS:30

The orchestrator hands the string to ``query_engine`` whose ``soql``
branch performs the REST call.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def plan(
    question: str,
    datasets: List[Dict[str, Any]],
    **_: Any,
) -> Optional[str]:
    logger.warning(
        "[nl_to_soql] not implemented in template — override "
        "planners.nl_to_soql.plan in a dept subclass to enable Salesforce NL→query."
    )
    return None
