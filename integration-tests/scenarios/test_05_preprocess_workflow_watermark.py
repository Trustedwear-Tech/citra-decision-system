# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Preprocess workflow + watermark monotonicity.

The "preprocess" pattern is a cron workflow that pulls only NEW rows
since the previous run, hands them to the SmartAppInvokerNode, and
THEN writes the new watermark. The contract that matters:

  1. WorkflowStateGetNode returns the prior value (or default if none)
  2. WorkflowStateSetNode upserts the value into Mongo
  3. Repeated set with the same value is idempotent
  4. A failed downstream node leaves the watermark UNCHANGED, so the
     next run reprocesses the same window — no batch is silently lost
  5. Override key works (one workflow can read another's watermark)

We hit Mongo directly (test DB ``citra_integration_test``, collection
``workflow_state``) — no scheduler needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "citra-workflow"))

# Workflow-state nodes lazy-import ``mongodb_manager`` from Citra-Service.
sys.path.insert(0, str(ROOT / "Citra-Service"))


pytestmark = pytest.mark.asyncio


def _ctx(*, workflow_id: str, config, items=None, variables=None):
    from citra_workflow.nodes import NodeContext  # noqa: E402
    ctx = NodeContext(
        node_id="state-test",
        node_config=config,
        input_data={"items": items or []},
        variables=variables or {},
        user_id="u_test",
        execution_id=f"{workflow_id}:exec1",
        environment="test",
    )
    # WorkflowStateGet/Set look at ctx.workflow_context for workflow_id.
    ctx.workflow_context = {"workflow_id": workflow_id}
    return ctx


def _ensure_mongo_db_env():
    os.environ.setdefault("MONGODB_DATABASE", "citra_integration_test")


async def _drop_state_collection():
    import motor.motor_asyncio
    conn = os.getenv("MONGODB_CONN_STRING") or "mongodb://localhost:27017"
    db_name = os.environ["MONGODB_DATABASE"]
    client = motor.motor_asyncio.AsyncIOMotorClient(conn)
    await client[db_name]["workflow_state"].delete_many({"workflow_id": {"$regex": "^itest_"}})


@pytest_asyncio.fixture(autouse=True)
async def _reset():
    _ensure_mongo_db_env()
    try:
        await _drop_state_collection()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"workflow_state collection unreachable: {exc}")
    yield


async def test_get_returns_default_when_no_prior_state():
    from citra_workflow.nodes.state import WorkflowStateGetNode  # noqa: E402

    node = WorkflowStateGetNode()
    ctx = _ctx(
        workflow_id="itest_wf_get_default",
        config={"key": "last_run_at", "default_value": "1970-01-01T00:00:00Z"},
    )
    out = await node.execute(ctx)
    assert out["meta"]["from_default"] is True
    assert out["meta"]["value"] == "1970-01-01T00:00:00Z"
    assert ctx.variables["last_run_at"] == "1970-01-01T00:00:00Z"


async def test_set_then_get_round_trip():
    from citra_workflow.nodes.state import (  # noqa: E402
        WorkflowStateGetNode, WorkflowStateSetNode,
    )

    wf_id = "itest_wf_round_trip"

    set_ctx = _ctx(
        workflow_id=wf_id,
        config={"key": "last_run_at", "value": '"2026-05-10T12:00:00Z"'},
    )
    await WorkflowStateSetNode().execute(set_ctx)

    get_ctx = _ctx(
        workflow_id=wf_id,
        config={"key": "last_run_at", "default_value": "1970-01-01T00:00:00Z"},
    )
    out = await WorkflowStateGetNode().execute(get_ctx)
    assert out["meta"]["from_default"] is False
    assert out["meta"]["value"] == "2026-05-10T12:00:00Z"


async def test_idempotent_set_does_not_throw():
    from citra_workflow.nodes.state import WorkflowStateSetNode  # noqa: E402

    wf_id = "itest_wf_idempotent"
    config = {"key": "high_id", "value": "42"}
    await WorkflowStateSetNode().execute(_ctx(workflow_id=wf_id, config=config))
    await WorkflowStateSetNode().execute(_ctx(workflow_id=wf_id, config=config))
    # Both succeed; second is just an upsert no-op.


async def test_failed_downstream_leaves_watermark_untouched():
    """Simulate the standard 'set at end of DAG' pattern.

    If the workflow crashes between Get and Set, the watermark must NOT
    advance — next cron tick will reprocess the same window.
    """
    from citra_workflow.nodes.state import (  # noqa: E402
        WorkflowStateGetNode, WorkflowStateSetNode,
    )

    wf_id = "itest_wf_crash_safety"

    # Initial watermark from a successful prior run.
    await WorkflowStateSetNode().execute(_ctx(
        workflow_id=wf_id,
        config={"key": "watermark", "value": '"2026-05-09T00:00:00Z"'},
    ))

    # New "run" reads the watermark…
    get_ctx = _ctx(
        workflow_id=wf_id,
        config={"key": "watermark", "default_value": "1970-01-01T00:00:00Z"},
    )
    await WorkflowStateGetNode().execute(get_ctx)

    # … then a downstream node "crashes" (we just don't call SetNode).
    # Read again — should still be the OLD value, never advanced.
    get_ctx2 = _ctx(
        workflow_id=wf_id,
        config={"key": "watermark", "default_value": "1970-01-01T00:00:00Z"},
    )
    out = await WorkflowStateGetNode().execute(get_ctx2)
    assert out["meta"]["value"] == "2026-05-09T00:00:00Z"


async def test_workflow_id_override_reads_other_workflow_state():
    from citra_workflow.nodes.state import (  # noqa: E402
        WorkflowStateGetNode, WorkflowStateSetNode,
    )

    # Workflow A writes its watermark.
    await WorkflowStateSetNode().execute(_ctx(
        workflow_id="itest_wf_override_a",
        config={"key": "shared_watermark", "value": '"2026-05-10T00:00:00Z"'},
    ))

    # Workflow B reads it via override.
    out = await WorkflowStateGetNode().execute(_ctx(
        workflow_id="itest_wf_override_b",
        config={
            "key": "shared_watermark",
            "default_value": "missing",
            "workflow_id_override": "itest_wf_override_a",
        },
    ))
    assert out["meta"]["value"] == "2026-05-10T00:00:00Z"
