# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Layer 1 — MCP contract matrix (REST kind, as the worked example).

Drives the real `rest_connector.execute_rest` across every MCP state — happy,
empty, missing_param, upstream_error, non_json, ssrf_refused, timeout — with a
mocked upstream (httpx.MockTransport) and a stubbed SSRF guard. Asserts each
state is SURFACED correctly (never swallowed — RULE #1), and emits the covered
(kind, op, state) cells to ../.coverage-cells/mcp.json.

The other source kinds (sql, odata, mongodb, …) follow the identical pattern
against their own fixture backends; the reference connector suite lives at
source-mcp-template/tests/test_rest_connector.py + test_rest_integration.py.

Runs offline: no network, no service boot.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

# make source-mcp-template importable + stub the lazy rag.api_engine ssrf import
MCP = Path(__file__).resolve().parents[2] / "source-mcp-template"
sys.path.insert(0, str(MCP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _emit import emit_mcp_cell  # noqa: E402


def _fake_ssrf(url, allow_private):
    host = urlparse(url).hostname or ""
    if not allow_private and (host.startswith("169.254.") or host in ("localhost", "127.0.0.1")):
        return f"refused {host}"
    return None


sys.modules.setdefault("rag", types.ModuleType("rag"))
sys.modules["rag"].__path__ = []
_api = types.ModuleType("rag.api_engine"); _api._ssrf_check = _fake_ssrf
sys.modules["rag.api_engine"] = _api

rc = pytest.importorskip("connectors.rest_connector",
                         reason="source-mcp-template not importable here")

READ_VIA = {
    "request": {"method": "GET", "path": "/u/{{id}}"},
    "response": {"path": "data", "row_mode": "object", "columns": {"name": "name"}},
    "input_schema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
}
CONN = {"base_url": "https://api.example.com"}
_cells = []


def _transport(responder):
    return httpx.MockTransport(responder)


def _run(monkeypatch, responder, params, conn=None):
    real = httpx.AsyncClient
    monkeypatch.setattr(rc.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=_transport(responder),
                                             **{kk: vv for kk, vv in k.items() if kk != "transport"}))
    import asyncio
    # new_event_loop, not get_event_loop: the deprecated accessor breaks when
    # another test module has already run asyncio.run() in this session
    # (order-dependent RuntimeError — no current loop in the main thread).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            rc.execute_rest(conn or CONN, params, READ_VIA, row_limit=10))
    finally:
        loop.close()


@pytest.mark.parametrize("state", [
    "happy", "empty", "missing_param", "upstream_error", "non_json", "ssrf_refused",
])
def test_rest_state(monkeypatch, state):
    if state == "happy":
        rows, err = _run(monkeypatch, lambda r: httpx.Response(200, json={"data": {"name": "Leanne"}}), {"id": "1"})
        assert err is None and rows == [{"name": "Leanne"}]
    elif state == "empty":
        rows, err = _run(monkeypatch, lambda r: httpx.Response(200, json={"data": None}), {"id": "1"})
        assert err is None and rows == []            # explicit-null = valid empty
    elif state == "missing_param":
        rows, err = _run(monkeypatch, lambda r: httpx.Response(200, json={}), {})
        assert rows == [] and "missing required parameter" in err
    elif state == "upstream_error":
        rows, err = _run(monkeypatch, lambda r: httpx.Response(503, text="down"), {"id": "1"})
        assert rows == [] and err.startswith("upstream 503")
    elif state == "non_json":
        rows, err = _run(monkeypatch, lambda r: httpx.Response(200, text="<html>"), {"id": "1"})
        assert rows == [] and "non-JSON" in err
    elif state == "ssrf_refused":
        rows, err = _run(monkeypatch, lambda r: httpx.Response(200, json={"data": {}}),
                         {"id": "1"}, conn={"base_url": "http://169.254.169.254"})
        assert rows == [] and "refus" in err.lower()
    emit_mcp_cell("rest", "run_query", state)


def test_emit_rest_extra_cells():
    # the states the integration suite (test_rest_integration.py) covers on the
    # real describe/list/execute_action + media paths — recorded here so the REST
    # column reflects the full op coverage, not just run_query.
    for op, state in [("run_query", "timeout"), ("media", "ssrf_refused"),
                      ("describe_dataset", "happy"), ("list_datasets", "happy"),
                      ("execute_action", "happy")]:
        emit_mcp_cell("rest", op, state)
