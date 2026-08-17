# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""LIVE auto-process commit E2E — drives the REAL commit path against the running
bsphcl MCP + Postgres. Replicates only the DB globals (NOT the scheduler — no prod
trigger firing). Binds TEST env (audit → test_auto_process_decisions; discovery
9000 resolves field_operations). Caller must capture+restore the test case in PG."""
import os, sys, asyncio, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import main
from config import get_settings
from env_context import set_current_env
from models import AppSpec
from motor.motor_asyncio import AsyncIOMotorClient
from proxy_clients import call_dept_mcp_execute_action
from trigger_runner import _mint_system_auth

CASE = "THC-2026-0000002"
NEW_STATUS = "under_recovery"
CORR = "live-commit-verify-1"


async def go():
    s = get_settings()
    cli = AsyncIOMotorClient(s.mongo_uri)
    db = cli[s.mongo_db]
    main._mongo_client = cli
    main._db = db
    main._apps_col = db[s.apps_collection]
    main._agents_col = db[s.agents_collection]
    main._workflow_staging_col = db[s.workflow_staging_collection]
    main._smart_app_records_col = db[s.smart_app_records_collection]
    set_current_env("test")

    app_doc = await db[main._test_collection_name(s.apps_collection)].find_one({"slug": "theft-case-triage"})
    if not app_doc:
        print("FAIL: theft-case-triage not found in test_smartapp_apps"); return
    app_spec = AppSpec(**app_doc["app_spec"])
    trig = next((t for t in (app_spec.triggers or []) if t.execution_mode == "auto_process"), None)
    print(f"app={app_spec.slug} trigger={trig.id if trig else None} action={trig.action if trig else None}")

    payload = {"case_id": CASE, "recovery_status": NEW_STATUS}
    pw = {"source_id": "field_operations", "dataset_id": "field_operations.theft_cases",
          "action_id": "update_recovery_status", "payload": payload,
          "_result": {"confidence": 0.95}}

    # 1) DRY-RUN pre-check (validates resolve + auth + action contract, no write)
    auth = _mint_system_auth(s, app_spec, app_doc)
    ujwt = auth.removeprefix("Bearer ").strip() if auth else None
    try:
        dr = await call_dept_mcp_execute_action(
            settings=s, user_jwt=ujwt, source_id="field_operations",
            dataset_id="field_operations.theft_cases", action_id="update_recovery_status",
            payload=payload, dry_run=True)
        print("DRY_RUN:", json.dumps(dr, default=str)[:280])
    except Exception as e:
        print(f"DRY_RUN ERROR: {type(e).__name__}: {e}")

    # 2) REAL commit via the auto-process commit path (commit + DecisionRecord)
    try:
        committed = await main.commit_auto_process_writes(
            settings=s, app_spec=app_spec, app_doc=app_doc, trigger=trig,
            inputs={"case_id": CASE, "assessed_amount": 5000}, planned_writes=[pw],
            reasons=["row.assessed_amount<10000 (live test)"], correlation_id=CORR)
        print("COMMITTED count:", committed)
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"COMMIT ERROR: {type(e).__name__}: {e}")

    # 3) DecisionRecord audit (env-routed → test_auto_process_decisions)
    dec = await main._route_col(db["auto_process_decisions"], "auto_process_decisions").find_one({"correlation_id": CORR})
    print("DECISION RECORD:", json.dumps({k: dec.get(k) for k in
          ("committed", "source_id", "dataset_id", "action_id", "policy_reason", "mode")}, default=str) if dec else "NONE")

asyncio.run(go())
