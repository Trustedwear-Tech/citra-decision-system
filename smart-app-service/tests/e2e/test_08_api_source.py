# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""API-type source (REST / bureau, e.g. a CIBIL screen) coverage.

Two kinds of test here:
  * OFFLINE unit tests of the `_coerce_rows` / `_is_unrenderable_object` fix —
    proving a non-columnar API object surfaces (not a silent-empty panel).
  * GATED live scaffolds documenting the SUPPORTED agentic path (`mcp` read via
    the NL `/query`), since acme-power is SQL-only and has no API dataset.

See docs/decision-app-test-plan.md §7b for the full flow + the 5 gaps.
"""
from __future__ import annotations

import os
import sys

import pytest

# smart-app-service root (two dirs up) so we can import panel_data.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


# ── Offline: the coerce-rows / unrenderable-object fix ──────────────────────
pd = pytest.importorskip(
    "panel_data",
    reason="panel_data not importable standalone in this env (needs service deps/config)",
)


@pytest.mark.parametrize("payload,expected", [
    ([], False),                                        # empty list → renderable (empty)
    ({"rows": []}, False),                              # legit empty SQL result
    ({"rows": [], "total": 0, "truncated": False}, False),
    ({"items": [{"a": 1}]}, False),                     # normal rows
    ({"error": "boom"}, False),                         # error envelope handled elsewhere
    ({}, False),                                        # empty object → not "unrenderable"
    ({"credit_score": 750, "status": "verified"}, True),   # CIBIL-style single object
    ({"report": {"score": 800}}, True),                 # nested API object
])
def test_is_unrenderable_object(payload, expected):
    assert pd._is_unrenderable_object(payload) is expected


def test_coerce_rows_still_unwraps_known_shapes():
    assert pd._coerce_rows({"rows": [{"a": 1}, {"b": 2}]}) == [{"a": 1}, {"b": 2}]
    assert pd._coerce_rows({"result": {"items": [{"x": 1}]}}) == [{"x": 1}]
    assert pd._coerce_rows([{"a": 1}, "skip-me"]) == [{"a": 1}]


def test_coerce_rows_empty_for_object():
    # A single API object coerces to no rows — the caller then flags it via
    # _is_unrenderable_object (surfaced as a panel note, not silent).
    obj = {"credit_score": 750, "status": "verified"}
    assert pd._coerce_rows(obj) == []
    assert pd._is_unrenderable_object(obj) is True


# ── Gated live scaffold: the supported agentic API path ─────────────────────
API_DS = os.getenv("DA_API_DS")   # e.g. "fraud_bureau.cibil_scores"
apilive = pytest.mark.skipif(
    not API_DS,
    reason="set DA_API_DS to a registered rest_api dataset (+ DA_API_QUERY) to run",
)


@apilive
def test_api_source_agentic_query(sas, base_specs):
    """The supported path: an agent NL query to a rest_api-backed mcp tool
    returns a result (the MCP's LLM crafts the HTTP call). Rendered via
    detail/agent_chat, NOT a queue/table.

    Wire DA_API_DS + DA_API_QUERY + an app that exposes the tool; then assert
    the tool result is non-empty and NOT a 501 (which would mean the keyed
    /run_query stub was hit instead of NL /query).
    """
    pytest.skip("scaffold — provide an app + rest_api dataset to exercise /query")
