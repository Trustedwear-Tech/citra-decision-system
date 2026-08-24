# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Regression: discovery_cache.resolve_source must resolve SEMANTIC sources.

A semantic (RAG) source publishes an EMPTY ``query_endpoint`` by design (the RAG
short-circuit — it is answered by the Citra-Service reader, not a dept-MCP).
resolve_source used to ``continue`` past any source with an empty endpoint and
then raise ``discovery_no_endpoint`` (502), which 502'd every semantic PANEL
(``/apps/{slug}/data/{policy_library}``) before ``_resolve_mcp_rows`` could
branch to ``call_citra_semantic_search``. It must now RESOLVE semantic sources
(carrying ``source_type``) while still skipping STRUCTURED sources that lack an
endpoint.
"""
import asyncio

import pytest

import discovery_cache as dc


class _Resp:
    status_code = 200

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


class _Client:
    def __init__(self, data):
        self._d = data

    async def get(self, url, headers=None, timeout=None):
        return _Resp(self._d)


def _patch(monkeypatch, tools):
    import http_client
    monkeypatch.setattr(http_client, "get_http_client", lambda: _Client(tools))
    monkeypatch.setattr(dc, "_get_cached", lambda k: None)
    monkeypatch.setattr(dc, "_set_cached", lambda k, rs, ttl: None)


def test_resolve_source_resolves_semantic_without_query_endpoint(monkeypatch):
    _patch(monkeypatch, [
        {"source_id": "billing", "query_endpoint": "http://mcp/query",
         "source_type": "structured"},
        {"source_id": "acme_power_policy_library", "query_endpoint": "",
         "source_type": "semantic", "rag_collection": "mcp_dept_libraries"},
    ])
    rs = asyncio.run(dc.resolve_source(
        discovery_url="http://discovery", user_jwt="jwt",
        source_id="acme_power_policy_library",
    ))
    assert rs.source_id == "acme_power_policy_library"
    assert rs.source_type == "semantic"
    assert rs.query_endpoint == ""      # empty is CORRECT for a semantic source


def test_resolve_source_still_skips_structured_without_endpoint(monkeypatch):
    _patch(monkeypatch, [
        {"source_id": "broken", "query_endpoint": "", "source_type": "structured"},
    ])
    with pytest.raises(dc.DiscoveryError) as exc:
        asyncio.run(dc.resolve_source(
            discovery_url="http://discovery", user_jwt="jwt", source_id="broken",
        ))
    assert exc.value.code == "discovery_no_endpoint"
