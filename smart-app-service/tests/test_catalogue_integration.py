# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the catalogue-driven data tools and binding validator."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings
from models import (
    Action,
    AgentSpec,
    DataBindingRead,
    DataBindings,
    DataBindingWrite,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_action(*, with_reads=True, with_writes=True, redact=True) -> Action:
    reads = [
        DataBindingRead(
            source_id="claims_db",
            dataset_id="claims_db.policies",
            columns=["policy_id", "customer_email"],
            redact_pii=redact,
        )
    ] if with_reads else []
    writes = [
        DataBindingWrite(
            source_id="claims_db",
            dataset_id="claims_db.policies",
            action_id="create_policy",
        )
    ] if with_writes else []
    return Action(
        name="file_claim",
        description="x",
        input_schema={
            "type": "object",
            "required": ["policy_id"],
            "properties": {"policy_id": {"type": "string"}},
        },
        data_bindings=DataBindings(reads=reads, writes=writes),
    )


_CATALOGUE_ENTRY = {
    "tenant_id": "bajaj",
    "source_id": "claims_db",
    "dataset_id": "claims_db.policies",
    "name": "policies",
    "kind": "sql",
    "columns": [
        {"name": "policy_id", "type": "VARCHAR", "pii": False},
        {"name": "customer_email", "type": "VARCHAR", "pii": True, "semantic_type": "email"},
        {"name": "premium", "type": "DECIMAL", "pii": False},
    ],
    "write_actions": [
        {"id": "create_policy", "verb": "create",
         "input_schema": {"required": ["policy_id"]}},
    ],
    "has_pii": True,
    "mcp_base_url": "http://claims-mcp:9000",
    "last_refreshed_at": "2026-05-01T00:00:00+00:00",
}


# ---------------------------------------------------------------------------
# build_data_tools
# ---------------------------------------------------------------------------


def test_build_data_tools_when_no_bindings_returns_empty():
    from data_tools import build_data_tools

    a = Action(name="bare", description="x")
    assert build_data_tools(a) == []


def test_build_data_tools_emits_query_and_perform_with_enums():
    from data_tools import ACTION_TOOL_NAME, QUERY_TOOL_NAME, build_data_tools

    tools = build_data_tools(_make_action())
    names = [t["function"]["name"] for t in tools]
    assert QUERY_TOOL_NAME in names
    assert ACTION_TOOL_NAME in names

    q = next(t for t in tools if t["function"]["name"] == QUERY_TOOL_NAME)
    assert q["function"]["parameters"]["properties"]["dataset_id"]["enum"] == ["claims_db.policies"]

    p = next(t for t in tools if t["function"]["name"] == ACTION_TOOL_NAME)
    assert p["function"]["parameters"]["properties"]["action_id"]["enum"] == ["create_policy"]


# ---------------------------------------------------------------------------
# dispatch_query_dataset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_query_dataset_redacts_pii():
    from data_tools import dispatch_query_dataset
    import data_tools as dt

    action = _make_action(redact=True)

    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    # Phase B2 — runtime now POSTs to dept-mcp /query (catalogue-keyed),
    # so the response is QueryResponse shape: results: ChunkResult[] with
    # raw rows attached on results[0].metadata.rows.
    fake_resp.json = lambda: {
        "results": [
            {
                "text": "Table: claims_db.policies\npolicy_id | customer_email | premium\n...",
                "score": 1.0,
                "source": "claims_db.policies",
                "metadata": {
                    "kind": "sql",
                    "sql": "SELECT * FROM policies",
                    "row_count": 2,
                    "dataset_id": "claims_db.policies",
                    "source_id": "claims_db",
                    "rows": [
                        {"policy_id": "P1", "customer_email": "a@b.com", "premium": 1500},
                        {"policy_id": "P2", "customer_email": "c@d.com", "premium": 2500},
                    ],
                },
            }
        ],
        "source_id": "claims_db",
        "source_type": "sql",
        "total": 1,
    }

    class _FakeClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, *_a, **_kw): return fake_resp

    with patch.object(dt, "fetch_catalogue_entry", new=AsyncMock(return_value=_CATALOGUE_ENTRY)), \
         patch.object(dt.httpx, "AsyncClient", _FakeClient):
        result = await dispatch_query_dataset(
            settings=Settings(),
            auth_header="Bearer x",
            tenant_id="bajaj",
            action=action,
            args={"dataset_id": "claims_db.policies", "query": "all policies"},
        )

    assert "rows" in result
    for row in result["rows"]:
        assert row["customer_email"] == "<redacted>"
        assert row["policy_id"] in ("P1", "P2")  # not redacted


@pytest.mark.asyncio
async def test_dispatch_query_dataset_rejects_unbound_dataset():
    from data_tools import dispatch_query_dataset

    action = _make_action()
    result = await dispatch_query_dataset(
        settings=Settings(),
        auth_header=None,
        tenant_id="bajaj",
        action=action,
        args={"dataset_id": "other.table", "query": "x"},
    )
    assert "error" in result
    assert "not in this action's reads" in result["error"]


@pytest.mark.asyncio
async def test_dispatch_query_dataset_missing_required_args():
    from data_tools import dispatch_query_dataset

    result = await dispatch_query_dataset(
        settings=Settings(),
        auth_header=None,
        tenant_id="bajaj",
        action=_make_action(),
        args={"dataset_id": "claims_db.policies"},  # missing query
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# dispatch_perform_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_perform_action_dry_run_passthrough():
    from data_tools import dispatch_perform_action
    import data_tools as dt

    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.json = lambda: {"ok": True, "result": {"dry_run": True}, "audit_id": "a1"}

    class _FakeClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def post(self, url, json, headers):
            assert json["dry_run"] is True
            assert json["action_id"] == "create_policy"
            assert json["payload"]["policy_id"] == "P1"
            return fake_resp

    with patch.object(dt, "fetch_catalogue_entry", new=AsyncMock(return_value=_CATALOGUE_ENTRY)), \
         patch.object(dt.httpx, "AsyncClient", _FakeClient):
        result = await dispatch_perform_action(
            settings=Settings(),
            auth_header="Bearer x",
            tenant_id="bajaj",
            action=_make_action(),
            args={
                "dataset_id": "claims_db.policies",
                "action_id": "create_policy",
                "payload": {"policy_id": "P1"},
                "dry_run": True,
            },
        )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_dispatch_perform_action_rejects_unbound_action():
    from data_tools import dispatch_perform_action

    result = await dispatch_perform_action(
        settings=Settings(),
        auth_header=None,
        tenant_id="bajaj",
        action=_make_action(),
        args={
            "dataset_id": "claims_db.policies",
            "action_id": "delete_everything",
            "payload": {},
        },
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# validate_data_bindings
# ---------------------------------------------------------------------------


def _agent_spec_with(action: Action) -> AgentSpec:
    return AgentSpec(
        agent_id="ag1",
        name="Claims",
        system_prompt="x",
        actions=[action],
    )




@pytest.mark.asyncio
async def test_validate_data_bindings_unknown_dataset():
    import data_binding_validator as dbv

    spec = _agent_spec_with(_make_action())
    with patch.object(dbv, "fetch_catalogue_entry", new=AsyncMock(return_value=None)):
        errs, _ = await dbv.validate_data_bindings(
            spec, settings=Settings(), auth_header=None, tenant_id="bajaj",
        )
    codes = {e["code"] for e in errs}
    assert "E_UNKNOWN_DATASET" in codes


@pytest.mark.asyncio
async def test_validate_data_bindings_unknown_column():
    import data_binding_validator as dbv

    bad = _make_action()
    bad.data_bindings.reads[0].columns = ["policy_id", "ghost_column"]
    spec = _agent_spec_with(bad)
    with patch.object(dbv, "fetch_catalogue_entry", new=AsyncMock(return_value=_CATALOGUE_ENTRY)):
        errs, _ = await dbv.validate_data_bindings(
            spec, settings=Settings(), auth_header=None, tenant_id="bajaj",
        )
    assert any(e["code"] == "E_UNKNOWN_COLUMN" and e["column"] == "ghost_column" for e in errs)


@pytest.mark.asyncio
async def test_validate_data_bindings_unknown_action():
    import data_binding_validator as dbv

    bad = _make_action()
    bad.data_bindings.writes[0].action_id = "wipe_table"
    spec = _agent_spec_with(bad)
    with patch.object(dbv, "fetch_catalogue_entry", new=AsyncMock(return_value=_CATALOGUE_ENTRY)):
        errs, _ = await dbv.validate_data_bindings(
            spec, settings=Settings(), auth_header=None, tenant_id="bajaj",
        )
    assert any(e["code"] == "E_UNKNOWN_ACTION" for e in errs)


# ---------------------------------------------------------------------------
# trim_for_prompt
# ---------------------------------------------------------------------------


def test_trim_for_prompt_shape():
    from catalogue_client import trim_for_prompt

    trimmed = trim_for_prompt([_CATALOGUE_ENTRY])
    assert len(trimmed) == 1
    t = trimmed[0]
    assert t["id"] == "claims_db.policies"
    assert t["actions"] == ["create_policy"]
    assert t["has_pii"] is True
    assert "[PII]" in t["columns_brief"]
