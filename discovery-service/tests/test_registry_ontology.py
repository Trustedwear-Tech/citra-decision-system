# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Ontology-review O3: the registry must accept + surface what MCPs register.

- supports_history was silently dropped (extra='ignore') even though the
  registration comment says "citra-app-builder reads this from discovery".
- ReconcileRequest backs the staleness sweep: a source renamed/removed from
  SOURCES_FILE previously stayed active in the registry forever.
"""
from __future__ import annotations

from models import ReconcileRequest, ToolDefinition, ToolRegistrationRequest


def test_registration_accepts_and_keeps_supports_history():
    req = ToolRegistrationRequest(
        tool_id="acme-billing",
        name="Billing",
        description="Billing source",
        query_endpoint="http://mcp:8503/query",
        source_id="billing",
        org_ids=["acme"],
        api_key="k",
        supports_history=True,
    )
    assert req.supports_history is True
    # And the consumer-facing shape surfaces it (was absent → always lost).
    td = ToolDefinition(
        name="Billing", description="d", query_endpoint="e", source_id="billing",
        org_ids=["acme"], dept_ids=[], tags=[], data_types=[],
        supports_history=True,
    )
    assert td.supports_history is True


def test_supports_history_defaults_false():
    td = ToolDefinition(
        name="x", description="d", query_endpoint="e", source_id="s",
        org_ids=[], dept_ids=[], tags=[], data_types=[],
    )
    assert td.supports_history is False


def test_reconcile_request_carries_the_ownership_scope():
    r = ReconcileRequest(
        api_key="k", active_tool_ids=["a", "b"], org_id="acme", dept_ids=["billing"],
    )
    assert r.active_tool_ids == ["a", "b"]
    assert r.org_id == "acme"
    assert r.dept_ids == ["billing"]


def test_reconcile_request_REQUIRES_scope():
    """org_id + dept_ids are required, not optional.

    api_key_hash alone is NOT ownership: smart-app-service holds one fleet-wide
    MCP_SERVICE_API_KEY, so every dept MCP presents the same key. An unscoped
    reconcile deactivated every OTHER department's tools on each MCP boot, and
    with no heartbeat/reaper it never self-healed. Making the scope required
    means a registrant that can't say what it owns cannot retire anything.
    """
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReconcileRequest(api_key="k", active_tool_ids=["a"])  # no scope
    with pytest.raises(ValidationError):
        ReconcileRequest(api_key="k", active_tool_ids=["a"], org_id="acme")  # no depts
