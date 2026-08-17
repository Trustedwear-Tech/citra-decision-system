# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for PublishRequest Rule 5 — type='mcp' data sources need a
resolvable ref.

panel_data.py parses an mcp ref as ``server.tool``. A ref with no ``.``
and no ``filters.tool`` can never resolve — the panel renders a
permanent "must reference 'server.tool'" error. The publisher must
reject it so the builder fixes the source instead of shipping a dead
panel.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import PublishRequest  # noqa: E402

_AGENT = {
    "spec_version": "v0",
    "agent_id": "agent-1",
    "name": "A",
    "model_tier": "tier_a",
    "system_prompt": "Do the job.",
    "actions": [],
    "tools_v2": [],
}


def _app(ds: dict) -> dict:
    return {
        "spec_version": "v0",
        "app_id": "app-1",
        "slug": "kiln-triage",
        "title": "Kiln Triage",
        "kind": "app",
        "agent_id": "agent-1",
        "data_sources": [{"id": "ds1", **ds}],
        "panels": [{"id": "q1", "type": "queue", "data_source": "ds1"}],
    }


def _publish(ds: dict) -> None:
    PublishRequest(session_id="bs_x", app_spec=_app(ds), agent_spec=_AGENT)


def test_mcp_ref_with_slash_is_rejected():
    """The classic builder mistake: a slash-style placeholder ref."""
    with pytest.raises(ValidationError) as exc:
        _publish({"type": "mcp", "ref": "dept-mcp/plant_ops"})
    assert "not\n" in str(exc.value) or "resolvable" in str(exc.value)


def test_mcp_ref_dot_separated_is_accepted():
    _publish({"type": "mcp", "ref": "plant_ops.kiln_runs"})


def test_mcp_server_only_with_filters_tool_is_accepted():
    _publish(
        {"type": "mcp", "ref": "plant_ops", "filters": {"tool": "kiln_runs"}}
    )


def test_smart_app_records_source_is_not_subject_to_mcp_rule():
    """The no-MCP fallback — a self-contained store — always validates."""
    _publish({"type": "smart_app_records", "ref": "queue_item"})
