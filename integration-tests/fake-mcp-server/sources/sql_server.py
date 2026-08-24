# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Generic SQL passthrough — minimal stub. Most tests don't exercise this."""
from __future__ import annotations

from typing import Any, Dict, List


def passthrough_sql(query: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Echo the query + filters back. Tests only assert the contract works."""
    return [
        {"_meta": "fake_sql_server", "query": query, "filters": dict(filters)}
    ]
