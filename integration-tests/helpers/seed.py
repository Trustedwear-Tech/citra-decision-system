# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Load fixtures into the test Mongo database.

Idempotent — re-running upserts. Called from conftest at session start
after ``db_reset.reset_all()``.

Seeds:
  • tenants            — acme-insurance-test, bravo-bank-test
  • users              — rohit (BA), alex (admin), claire (other tenant)
  • catalogue datasets — pre-crawled view of the stub MCPs
  (dept_sources seed removed 2026-07-10 — that registry is retired; sources
   come from the MCP SOURCES_FILE + discovery.)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorClient

from .jwt_mint import ACME_BA, ACME_OPS_ADMIN, BRAVO_BA

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


# ── Mongo helpers ───────────────────────────────────────────────────────


def _get_db() -> Any:
    conn = (
        os.getenv("MONGODB_CONN_STRING")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = os.getenv("MONGODB_DATABASE", "citra_integration_test")
    return AsyncIOMotorClient(conn)[db_name]


# ── Tenants + users ─────────────────────────────────────────────────────


TENANTS = [
    {
        "tenant_id": "acme-insurance-test",
        "name": "Acme Insurance (Test)",
        "industry": "insurance",
        "created_at": datetime.utcnow(),
    },
    {
        "tenant_id": "bravo-bank-test",
        "name": "Bravo Bank (Test)",
        "industry": "banking",
        "created_at": datetime.utcnow(),
    },
]

USERS = [ACME_BA, ACME_OPS_ADMIN, BRAVO_BA]


async def seed_tenants(db) -> int:
    coll = db["tenants"]
    n = 0
    for t in TENANTS:
        await coll.update_one(
            {"tenant_id": t["tenant_id"]}, {"$set": t}, upsert=True
        )
        n += 1
    return n


async def seed_users(db) -> int:
    coll = db["users"]
    n = 0
    for u in USERS:
        doc = dict(u)
        doc["created_at"] = datetime.utcnow()
        await coll.update_one({"user_id": doc["user_id"]}, {"$set": doc}, upsert=True)
        n += 1
    return n


# ── Stub MCP base URL ───────────────────────────────────────────────────
# The dept_sources seed was removed (2026-07-10): the central dept_sources
# registry is retired (sources come from the MCP SOURCES_FILE + discovery),
# and the platform readers now scope off the discovery registry. The fake MCP
# is self-contained (its own sources/ + /datasets); the catalogue is seeded
# directly below.


def _stub_mcp_base_url() -> str:
    return os.getenv("FAKE_MCP_BASE_URL", "http://fake-mcp-server:8500")




# ── Catalogue (pre-crawled view of the dept sources) ────────────────────


async def seed_catalogue(db) -> int:
    coll = db["data_catalogue"]
    base = _stub_mcp_base_url()
    entries = [
        {
            "tenant_id": "acme-insurance-test",
            "source_id": "sap_motor_claims",
            "dataset_id": "motor_claims_2024",
            "name": "Motor Claims (2024)",
            "description": "Closed motor-insurance claims from 2024 with decisions",
            "read_via": {"kind": "sql", "target": "claims.motor_2024"},
            "supports_history": True,
            "tools": [
                {
                    "name": "list_motor_claims",
                    "description": "Return motor claims filtered by date / status",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "since": {"type": "string"},
                            "status": {"type": "string"},
                            "max_results": {"type": "integer"},
                        },
                    },
                }
            ],
            "columns": [
                "claim_id", "claim_amount", "vehicle", "claimant_name",
                "incident_date", "status", "decision", "reason_code",
                "cited_policy", "adjuster_notes",
            ],
            "row_count_estimate": 5000,
            "indexed_at": datetime.utcnow(),
        },
        {
            "tenant_id": "acme-insurance-test",
            "source_id": "salesforce_opportunities",
            "dataset_id": "sf_opportunities_2024",
            "name": "Salesforce Opportunities (2024)",
            "description": "All opportunity records 2024",
            "read_via": {"kind": "sql", "target": "sf.opportunities_2024"},
            "supports_history": True,
            "tools": [
                {
                    "name": "query_opportunities",
                    "description": "SOQL-style query for opportunities",
                    "input_schema": {"type": "object"},
                }
            ],
            "columns": [
                "Id", "Name", "Amount", "StageName", "CloseDate",
                "AccountId", "Region",
            ],
            "row_count_estimate": 1000,
            "indexed_at": datetime.utcnow(),
        },
        {
            "tenant_id": "acme-insurance-test",
            "source_id": "policies_rag",
            "dataset_id": "motor_policies_v3",
            "name": "Motor Policies (RAG corpus)",
            "description": "Insurance policy documents indexed for semantic search",
            "read_via": {"kind": "semantic", "target": "policies_rag"},
            "supports_history": False,
            "tools": [
                {
                    "name": "search_policies",
                    "description": "Semantic search across policy documents",
                    "input_schema": {"type": "object"},
                }
            ],
            "columns": [],
            "row_count_estimate": 50,
            "indexed_at": datetime.utcnow(),
        },
    ]
    n = 0
    for e in entries:
        await coll.update_one(
            {
                "tenant_id": e["tenant_id"],
                "source_id": e["source_id"],
                "dataset_id": e["dataset_id"],
            },
            {"$set": e},
            upsert=True,
        )
        n += 1
    return n


# ── Top-level seed runner ───────────────────────────────────────────────


async def reset_and_seed() -> Dict[str, int]:
    """Reset and reseed in one shot.

    Called from conftest's session-start hook. Returns a dict of counts
    so the test runner can log a summary line.
    """
    from .db_reset import reset_all

    await reset_all()

    db = _get_db()
    counts = {
        "tenants": await seed_tenants(db),
        "users": await seed_users(db),
        "catalogue": await seed_catalogue(db),
    }
    logger.info("[seed] %s", counts)
    return counts


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(reset_and_seed()))
