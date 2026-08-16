"""Offline unit tests for the schema-driven REST connector.

Uses httpx.MockTransport for the upstream API and stubs the lazy
`rag.api_engine._ssrf_check` import so the test needs no config/network.
"""
from __future__ import annotations

import os
import sys
import types
from urllib.parse import urlparse

import httpx
import pytest


# ── stub rag.api_engine (the connector lazily imports _ssrf_check from it) ───
def _fake_ssrf(url, allow_private):
    host = urlparse(url).hostname or ""
    if allow_private:
        return None
    if host in ("localhost", "127.0.0.1") or host.startswith("169.254.") or host.startswith("10."):
        return f"refusing private host {host!r}"
    return None


_rag = types.ModuleType("rag"); _rag.__path__ = []
_api = types.ModuleType("rag.api_engine"); _api._ssrf_check = _fake_ssrf
sys.modules.setdefault("rag", _rag)
sys.modules["rag.api_engine"] = _api

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import connectors.rest_connector as rc  # noqa: E402


@pytest.fixture()
def mock_http(monkeypatch):
    """Install a MockTransport into the connector's httpx.AsyncClient and
    capture the last request for assertions."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        return captured["responder"](request)

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(rc.httpx, "AsyncClient", factory)
    return captured


async def _run(conn, params, read_via, row_limit=50):
    return await rc.execute_rest(conn, params, read_via, row_limit=row_limit)


@pytest.mark.asyncio
async def test_object_mode_maps_columns(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(
        200, json={"data": {"score": 750, "status": "verified", "extra": 1}})
    conn = {"base_url": "https://bureau.example.com"}
    read_via = {
        "request": {"method": "GET", "path": "/v2/credit/{{pan}}"},
        "response": {"path": "data", "row_mode": "object",
                     "columns": {"credit_score": "score", "status": "status"}},
        "input_schema": {"type": "object", "required": ["pan"],
                         "properties": {"pan": {"type": "string"}}},
    }
    rows, err = await _run(conn, {"pan": "ABCPE1234F"}, read_via)
    assert err is None
    assert rows == [{"credit_score": 750, "status": "verified"}]
    assert "ABCPE1234F" in mock_http["url"]  # param went into the path


@pytest.mark.asyncio
async def test_list_mode_passthrough(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(
        200, json={"items": [{"a": 1}, {"a": 2}, {"a": 3}]})
    read_via = {"request": {"method": "GET", "path": "/x"},
                "response": {"path": "items", "row_mode": "list"}}
    rows, err = await _run({"base_url": "https://api.example.com"}, {}, read_via, row_limit=2)
    assert err is None
    assert rows == [{"a": 1}, {"a": 2}]  # capped at row_limit


@pytest.mark.asyncio
async def test_missing_required_param_fails_loud(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(200, json={})
    read_via = {"request": {"path": "/c/{{pan}}"},
                "input_schema": {"required": ["pan"]}}
    rows, err = await _run({"base_url": "https://b.example.com"}, {}, read_via)
    assert rows == []
    assert err == "missing required parameter 'pan'"


@pytest.mark.asyncio
async def test_ssrf_refused(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(200, json={"data": {}})
    read_via = {"request": {"path": "/latest/meta-data"}, "response": {"path": "data"}}
    rows, err = await _run({"base_url": "http://169.254.169.254"}, {}, read_via)
    assert rows == []
    assert "refus" in err.lower()


@pytest.mark.asyncio
async def test_non_2xx_fails_loud(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(404, text="not found")
    read_via = {"request": {"path": "/c/{{pan}}"}, "response": {"path": "data"}}
    rows, err = await _run({"base_url": "https://b.example.com"}, {"pan": "X"}, read_via)
    assert rows == []
    assert err.startswith("upstream 404")


@pytest.mark.asyncio
async def test_optional_query_param_dropped_when_absent(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(200, json={"data": {"ok": True}})
    read_via = {"request": {"path": "/c/{{pan}}", "query": {"loanId": "{{loan_id}}"}},
                "response": {"path": "data", "row_mode": "object"}}
    rows, err = await _run({"base_url": "https://b.example.com"}, {"pan": "X"}, read_via)
    assert err is None
    assert "loanId" not in mock_http["url"]  # unresolved optional was dropped, not leaked


@pytest.mark.asyncio
async def test_response_path_missing_fails_loud(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(200, json={"other": 1})
    read_via = {"request": {"path": "/c/{{pan}}"}, "response": {"path": "data.report"}}
    rows, err = await _run({"base_url": "https://b.example.com"}, {"pan": "X"}, read_via)
    assert rows == []
    assert "response path" in err


@pytest.mark.asyncio
async def test_path_param_is_url_encoded(mock_http):
    # a value with / ? = must be percent-encoded, never alter the request structure
    mock_http["responder"] = lambda req: httpx.Response(200, json={"data": {"ok": True}})
    read_via = {"request": {"path": "/c/{{pan}}"}, "response": {"path": "data", "row_mode": "object"}}
    rows, err = await _run({"base_url": "https://b.example.com"}, {"pan": "A/B?x=1"}, read_via)
    assert err is None
    assert "A%2FB%3Fx%3D1" in mock_http["url"]
    assert mock_http["url"].endswith("A%2FB%3Fx%3D1")  # no injected extra segment / query


@pytest.mark.asyncio
async def test_explicit_null_path_is_empty_not_error(mock_http):
    # {"data": null} = a VALID "no record" result, not a "path not found" error
    mock_http["responder"] = lambda req: httpx.Response(200, json={"data": None})
    read_via = {"request": {"path": "/c/{{pan}}"}, "response": {"path": "data", "row_mode": "object"}}
    rows, err = await _run({"base_url": "https://b.example.com"}, {"pan": "X"}, read_via)
    assert err is None and rows == []


@pytest.mark.asyncio
async def test_missing_request_mapping_fails_loud(mock_http):
    mock_http["responder"] = lambda req: httpx.Response(200, json={})
    rows, err = await _run({"base_url": "https://b.example.com"}, {"pan": "X"},
                           {"response": {"path": "data"}})   # no request block
    assert rows == [] and "request mapping" in err


@pytest.mark.asyncio
async def test_second_order_placeholder_not_reexpanded(mock_http):
    # a param value that itself looks like {{b}} must NOT be re-expanded to b's value
    mock_http["responder"] = lambda req: httpx.Response(200, json={"data": {"ok": True}})
    read_via = {"request": {"path": "/c/{{a}}"}, "response": {"path": "data"}}
    rows, err = await _run({"base_url": "https://b.example.com"},
                           {"a": "{{b}}", "b": "SECRET"}, read_via)
    assert err is None
    assert "SECRET" not in mock_http["url"]

@pytest.mark.asyncio
async def test_base_url_with_path_joins_cleanly(mock_http):
    # base_url ending in a path segment + a path with no leading slash → single "/"
    mock_http["responder"] = lambda req: httpx.Response(200, json={"data": {"ok": True}})
    read_via = {"request": {"path": "credit"}, "response": {"path": "data", "row_mode": "object"}}
    rows, err = await _run({"base_url": "https://b.example.com/v2"}, {}, read_via)
    assert err is None
    assert "/v2/credit" in mock_http["url"] and "v2credit" not in mock_http["url"]
