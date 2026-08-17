# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Integration: catalogue.run_query(kind=rest) → query_engine → rest_connector
→ (mock) upstream API → rows. Proves the dispatch wiring my catalogue edit adds,
not just the connector in isolation.

Skips if the MCP modules can't import standalone (no config/env in this shell).
"""
from __future__ import annotations

import os
import sys
import types
from urllib.parse import urlparse

import httpx
import pytest


# stub the lazy ssrf import the connector does
def _fake_ssrf(url, allow_private):
    host = urlparse(url).hostname or ""
    if not allow_private and (host.startswith("169.254.") or host in ("localhost", "127.0.0.1")):
        return f"private host {host}"
    return None


_rag = types.ModuleType("rag"); _rag.__path__ = []
_api = types.ModuleType("rag.api_engine"); _api._ssrf_check = _fake_ssrf
sys.modules.setdefault("rag", _rag)
sys.modules["rag.api_engine"] = _api

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

catalogue = pytest.importorskip("catalogue", reason="MCP catalogue not importable standalone here")
models = pytest.importorskip("models")
import connectors.rest_connector as rc  # noqa: E402


_SOURCE = {
    "source_id": "fraud_bureau",
    "type": "rest_api",
    "connection": {"base_url": "https://bureau.example.com"},
    "datasets": [{
        "id": "fraud_bureau.cibil",
        "name": "CIBIL scores",
        "kind": "rest",
        "columns": [{"name": "credit_score", "type": "number"},
                    {"name": "status", "type": "string"}],
        "input_schema": {"type": "object", "required": ["pan"],
                         "properties": {"pan": {"type": "string"}}},
        "read_via": {"kind": "rest", "extra": {
            "request": {"method": "GET", "path": "/v2/credit/{{pan}}"},
            "response": {"path": "data", "row_mode": "object",
                         "columns": {"credit_score": "score", "status": "status"}},
        }},
    }],
}


@pytest.fixture()
def wired(monkeypatch):
    # resolve our test source in the catalogue
    monkeypatch.setattr(catalogue, "get_source", lambda sid: _SOURCE if sid == "fraud_bureau" else None)

    # mock the upstream API inside the connector's httpx client
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"data": {"score": 812, "status": "verified"}})

    real = httpx.AsyncClient
    monkeypatch.setattr(rc.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler),
                                             **{kk: vv for kk, vv in k.items() if kk != "transport"}))
    return captured


@pytest.mark.asyncio
async def test_run_query_rest_end_to_end(wired):
    req = models.RunQueryRequest(
        source_id="fraud_bureau", dataset_id="fraud_bureau.cibil",
        kind=models.DatasetKind.rest, query={"pan": "ABCPE1234F"}, row_limit=50,
    )
    resp = await catalogue.run_query(req)
    assert resp.error is None, resp.error
    assert resp.rows == [{"credit_score": 812, "status": "verified"}]
    assert resp.total == 1
    assert "ABCPE1234F" in wired["url"]


@pytest.mark.asyncio
async def test_run_query_rest_keyed_filters_shape(wired):
    # the keyed-read fast-path wraps params as {"filters": {...}} — must work too
    req = models.RunQueryRequest(
        source_id="fraud_bureau", dataset_id="fraud_bureau.cibil",
        kind=models.DatasetKind.rest, query={"filters": {"pan": "ABCPE1234F"}}, row_limit=50,
    )
    resp = await catalogue.run_query(req)
    assert resp.error is None and resp.rows[0]["credit_score"] == 812


@pytest.mark.asyncio
async def test_run_query_rest_missing_required_surfaces_error(wired):
    req = models.RunQueryRequest(
        source_id="fraud_bureau", dataset_id="fraud_bureau.cibil",
        kind=models.DatasetKind.rest, query={}, row_limit=50,
    )
    resp = await catalogue.run_query(req)
    assert resp.rows == []
    assert resp.error == "missing required parameter 'pan'"
