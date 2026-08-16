# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Direct test of DuckDB service with the actual document."""
import asyncio
import json
import httpx

async def test():
    payload = {
        "query": "total number of colleges in India | number of colleges in India by state | statewise list of colleges in India with counts",
        "user_id": "rohit@trustedweartech.com",
        "document_ids": ["35bff97b-d4b9-4d4c-8673-8174aab0e327"],
        "vector_samples": [],
        "chart_type": None,
        "schema_metadata_map": {},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(
            "http://localhost:7301/api/analytical-query",
            content=json.dumps(payload, default=str),
            headers={"Content-Type": "application/json"},
        )
        result = resp.json()
        print("Success:", result.get("success"))
        print("Error:", result.get("error"))
        print("SQL:", result.get("sql", "N/A")[:500])
        print()
        chart = result.get("chart_data", {})
        print("has_structured_data:", chart.get("has_structured_data"))
        print("record_count:", chart.get("record_count"))
        print("schema_fields:", chart.get("schema_fields"))
        if chart.get("records"):
            print("First 3 records:", json.dumps(chart["records"][:3], indent=2))

asyncio.run(test())
