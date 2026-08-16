# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Fake dept-MCP server for integration tests.

Mirrors the contract of source-mcp-template:

  POST /query        → run a query against one of 4 sources
  POST /datasets     → list datasets exposed (data-discovery crawl path)
  GET  /health       → liveness

Sources registered:

  ``sap_motor_claims``     — structured/SQL, supports historical + paginated
  ``salesforce_opportunities`` — structured/SOQL
  ``sql_server``           — generic SQL passthrough
  ``policies_rag``         — semantic search over policy docs

In-memory data lives under ``data/`` and ``sources/``. Every test session
starts with the same canonical data unless the test explicitly mutates
it via ``POST /admin/reset``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sources.sap_claims import (
    list_motor_claims,
    reset_sap_claims,
    seed_count as sap_seed_count,
)
from sources.salesforce import (
    query_opportunities,
    reset_salesforce,
    seed_count as sf_seed_count,
)
from sources.sql_server import passthrough_sql
from sources.policies_rag import search_policies, reset_policies

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("fake-mcp")


app = FastAPI(title="fake-mcp-server", version="0.1.0")


# ── Request/response shapes ─────────────────────────────────────────────


class QueryRequest(BaseModel):
    source_id: str
    query: Optional[str] = None
    tool_name: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    max_results: int = 50
    historical: bool = False
    offset: int = 0


# ── Health ──────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "sources": [
            "sap_motor_claims",
            "salesforce_opportunities",
            "sql_server",
            "policies_rag",
        ],
        "row_counts": {
            "sap_motor_claims": sap_seed_count(),
            "salesforce_opportunities": sf_seed_count(),
        },
    }


# ── Datasets (catalogue crawl path) ─────────────────────────────────────


@app.post("/datasets")
def list_datasets() -> Dict[str, Any]:
    return {
        "datasets": [
            {
                "source_id": "sap_motor_claims",
                "dataset_id": "motor_claims_2024",
                "name": "Motor Claims (2024)",
                "kind": "structured",
                "supports_history": True,
                "tools": ["list_motor_claims", "get_claim"],
                "columns": [
                    "claim_id", "claim_amount", "vehicle", "claimant_name",
                    "incident_date", "status", "decision", "reason_code",
                    "cited_policy", "adjuster_notes",
                ],
            },
            {
                "source_id": "salesforce_opportunities",
                "dataset_id": "sf_opportunities_2024",
                "name": "Opportunities (2024)",
                "kind": "structured",
                "supports_history": True,
                "tools": ["query_opportunities"],
                "columns": [
                    "Id", "Name", "Amount", "StageName", "CloseDate",
                    "AccountId", "Region",
                ],
            },
            {
                "source_id": "sql_server",
                "dataset_id": "generic_sql",
                "name": "Generic SQL passthrough",
                "kind": "structured",
                "supports_history": False,
                "tools": ["raw_sql"],
                "columns": [],
            },
            {
                "source_id": "policies_rag",
                "dataset_id": "motor_policies_v3",
                "name": "Motor Policies (RAG)",
                "kind": "semantic",
                "supports_history": False,
                "tools": ["search_policies"],
                "columns": [],
            },
        ]
    }


# ── /query — the workhorse endpoint ─────────────────────────────────────


@app.post("/query")
def query(req: QueryRequest) -> Dict[str, Any]:
    sid = req.source_id

    if sid == "sap_motor_claims":
        results, total, next_offset = list_motor_claims(
            filters=req.filters,
            max_results=req.max_results,
            historical=req.historical,
            offset=req.offset,
        )
        return {
            "source_id": sid,
            "source_type": "structured",
            "results": results,
            "total": total,
            "next_offset": next_offset,
        }

    if sid == "salesforce_opportunities":
        results, total, next_offset = query_opportunities(
            filters=req.filters,
            max_results=req.max_results,
            offset=req.offset,
        )
        return {
            "source_id": sid,
            "source_type": "structured",
            "results": results,
            "total": total,
            "next_offset": next_offset,
        }

    if sid == "sql_server":
        return {
            "source_id": sid,
            "source_type": "structured",
            "results": passthrough_sql(req.query or "", req.filters),
        }

    if sid == "policies_rag":
        chunks = search_policies(req.query or "", top_k=req.max_results)
        return {
            "source_id": sid,
            "source_type": "semantic",
            "results": chunks,
        }

    raise HTTPException(404, f"unknown source_id '{sid}'")


# ── Test admin endpoints ────────────────────────────────────────────────


@app.post("/admin/reset")
def admin_reset() -> Dict[str, Any]:
    """Reset all source data to canonical seed state. Used between tests."""
    reset_sap_claims()
    reset_salesforce()
    reset_policies()
    return {"status": "ok", "reset_at": "now"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8500"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
