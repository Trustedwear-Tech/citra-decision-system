# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Seed ONE embeddable-card app into a local Mongo, for the dev E2E.

Deliberately NOT pointed at the shared dev Atlas: this writes app + agent
documents, and a throwaway E2E has no business leaving rows in an environment
other people use. Point MONGO_URI at a scratch instance.

Publishes the app into BOTH stores — prod (`smartapp_apps`) and test
(`test_smartapp_apps`) — with its own key in each. That is the exact state that
made the environment-binding defect real: a PROMOTED app, findable by slug in
prod, whose UAT card holds an `emb_test_` key.

    MONGO_URI=mongodb://localhost:27077 MONGO_DB=citra_e2e \
      python scripts/seed_embed_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from embed_keys import mint_embed_key  # noqa: E402

SLUG = "embed-e2e-loan"
AGENT_ID = "agent_embed_e2e"
# The tenant the app belongs to. Must match the org of the officer who will
# open it — audience="org" is checked against the caller's org_id.
TENANT = os.getenv("TENANT_ID", "acme-bank")
TEST_KEY = "emb_test_e2e0000000000001"
LIVE_KEY = "emb_live_e2e0000000000002"

APP_SPEC = {
    "spec_version": "v0",
    "slug": SLUG,
    "title": "Embed E2E — Loan decision",
    "agent_id": AGENT_ID,
    "audience": "org",
    "tenant_id": TENANT,
    "owner_type": "service_account",
    "owner_id": "svc:e2e@acme-bank.citra.ai",
    # A REAL catalogue dataset from the running acme-bank dept-MCP, so the E2E
    # exercises the data plane rather than only the render path. Confirmed via
    # GET /catalogue on the discovery service; `application_id` is its key.
    "data_sources": [
        {"id": "applications", "type": "mcp", "ref": "loan_origination.loan_applications"},
        # Pinned to the record the HOST passed in. The trigger queue is not a
        # worklist — it is the action button, and it must show exactly one row.
        {"id": "applications_one", "type": "mcp",
         "ref": "loan_origination.loan_applications",
         "filters": {"application_id": "{param.id}"}},
    ],
    "pages": [
        {
            "id": "card",
            "kind": "embed",
            "title": "Loan decision",
            "panels": [
                {
                    # THE TRIGGER. A queue action is the only affordance that
                    # runs the agent from the UI; the recommendation +
                    # approve/reject + reason codes then appear in
                    # RunResultModal, which the embed redirects into the shadow
                    # root. Without this the card is a viewer.
                    "id": "trigger",
                    "type": "queue",
                    "title": "Decision",
                    "data_source": "applications_one",
                    "columns": ["application_id", "product", "status"],
                    "actions": [
                        {"label": "Review", "agent_action": "review_application"}
                    ],
                },
                {
                    "id": "decision",
                    "type": "detail",
                    # The binding this whole change exists for: no queue to
                    # click, the host passes the record id.
                    "data_source": "applications",
                    "id_field": "application_id",
                    "sections": [
                        {"type": "fields"},
                        {"type": "agent_timeline"},
                    ],
                }
            ],
        }
    ],
    "theme": {"primary": "#0b5fff", "company_name": "Acme Bank"},
    # Without a closed reason taxonomy the officer's reject is free text, which
    # consolidates far worse — and the reason code is the entire point of
    # rendering our own UI rather than handing over an API. Facets are what
    # scope a learned judgement to the cases it should fire on.
    "case_signature": {
        "facets": [
            {"family": "product", "kind": "enum", "from_column": "product",
             "values": ["business", "home", "auto", "personal"]},
            {"family": "foir_band", "kind": "band", "from_column": "foir_percent",
             "edges": [30.0, 50.0, 70.0]},
            {"family": "sourcing_channel", "kind": "enum",
             "from_column": "sourcing_channel",
             "values": ["digital", "branch", "dsa", "telesales"]},
        ],
        "reason_codes": [
            {"code": "foir_above_cap", "label": "FOIR above policy cap"},
            {"code": "income_not_corroborated",
             "label": "Income not corroborated by the proof supplied"},
            {"code": "dsa_sourced_needs_verification",
             "label": "DSA-sourced — needs extra verification"},
            {"code": "data_stale_or_wrong", "label": "Source data stale or wrong"},
        ],
        "learning": {"promotion_min_officers": 3, "clause_budget_words": 1000},
    },
}

AGENT_SPEC = {
    "agent_id": AGENT_ID,
    "name": "Loan decision agent",
    "description": "Recommends a credit decision on a loan application.",
    "model_tier": "large",
    "system_prompt": (
        "You are a credit officer's assistant at Acme Bank. Given a loan "
        "application, recommend APPROVE or DECLINE and state the single most "
        "important reason in one sentence. Acme's policy caps FOIR at 50% for "
        "unsecured products. Be concise."
    ),
    # A real write action on the dept-MCP. Without one there are no
    # planned_writes, so the run completes outright and there is nothing for the
    # officer to approve — and the reason capture, which is the whole point of
    # shipping UI rather than an API, never happens.
    "tools_v2": [
        {
            # A READ tool. Without one the agent has nothing to reason over and
            # burns its whole turn failing to call query_dataset. The
            # read-before-write guard also requires the record to have been read.
            "name": "lookup_application",
            "description": (
                "Look up the loan application by application_id — returns "
                "product, amount_requested, foir_percent, sourcing_channel, "
                "income_proof_type and status."
            ),
            "kind": "mcp",
            "source_id": "loan_origination",
            "tool_name": "Lending — Origination & Underwriting",
            "dataset_id": "loan_origination.loan_applications",
            "dataset_kind": "sql",
            "required": False,
        },
        {
            "name": "record_credit_decision",
            "description": (
                "Persist the credit decision on the loan application — sets "
                "status, decision_reason, decided_by and decided_at."
            ),
            "kind": "mcp_action",
            "source_id": "loan_origination",
            "dataset_id": "loan_origination.loan_applications",
            "action_id": "record_credit_decision",
            # Objects, not strings — a bare string fails AgentSpec validation.
            # Empty here, as in the production loan-triage app.
            "editable_fields": [],
            "input_schema": {
                "type": "object",
                "required": ["application_id", "status"],
                "properties": {
                    "application_id": {"type": "string"},
                    "status": {"type": "string",
                               "description": "approved | rejected | under_review"},
                    "decision_reason": {"type": "string"},
                    "decided_by": {"type": "string"},
                    "decided_at": {"type": "string"},
                },
            },
        }
    ],
    "actions": [
        {
            "name": "review_application",
            "description": (
                "Review the application against credit policy and recommend a "
                "decision."
            ),
            "model_tier": "large",
            # Plan-then-apply is universal; the officer's click is what commits.
            "approval_required": True,
            # WITHOUT THIS, AN EMBED NEVER LEARNS.
            #
            # Facets are derived from the case record. An embed passes only a
            # record ID — that is the whole contract — so with no anchor_read
            # the runtime falls back to the run's inputs, which contain
            # `application_id` and nothing the facet families read. Every
            # correction then lands with case_facets: [] — uncoded — and
            # consolidation can reinforce an existing judgement but can never
            # author a new one.
            #
            # anchor_read pre-loads the row deterministically, so the facets
            # (product / foir_band / sourcing_channel) actually derive.
            "anchor_read": {
                "source_id": "loan_origination",
                "dataset_id": "loan_origination.loan_applications",
                "key_field": "application_id",
                "kind": "sql",
            },
            "input_schema": {
                "type": "object",
                "required": ["application_id"],
                "properties": {"application_id": {"type": "string"}},
            },
        }
    ],
}


async def main() -> int:
    uri = os.getenv("MONGO_URI")
    db_name = os.getenv("MONGO_DB", "citra_e2e")
    if not uri:
        print("MONGO_URI is required (point it at a SCRATCH mongo, not dev)")
        return 1
    if "mongodb.net" in uri:
        # Guard, not politeness: this seeds fixture rows.
        print("refusing to seed into an Atlas cluster — use a local scratch mongo")
        return 2

    client = AsyncIOMotorClient(uri)
    db = client[db_name]
    now = datetime.now(timezone.utc)

    def doc(key: str) -> dict:
        return {
            "app_id": "app_embed_e2e",
            "slug": SLUG,
            "tenant_id": TENANT,
            "owner": APP_SPEC["owner_id"],
            "agent_id": AGENT_ID,
            "status": "published",
            "version": 1,
            "deployed_at": now,
            "embed_key": key,
            "app_spec": APP_SPEC,
            "grounded": False,
            "fraud_enabled": False,
        }

    agent_doc = {"agent_id": AGENT_ID, "tenant_id": TENANT, "version": 1,
                 "agent_spec": AGENT_SPEC, "updated_at": now}

    for col, key in (("smartapp_apps", LIVE_KEY), ("test_smartapp_apps", TEST_KEY)):
        await db[col].replace_one({"slug": SLUG}, doc(key), upsert=True)
    for col in ("smartapp_agents", "test_smartapp_agents"):
        await db[col].replace_one({"agent_id": AGENT_ID}, agent_doc, upsert=True)

    print(f"seeded {SLUG}")
    print(f"  prod key : {LIVE_KEY}")
    print(f"  test key : {TEST_KEY}")
    print(f"  db       : {db_name}")
    # Sanity: a fresh key mints with the right prefix (catches an import break).
    assert mint_embed_key("prod").startswith("emb_live_")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
