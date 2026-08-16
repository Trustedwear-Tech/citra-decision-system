"""Tests for PublishRequest Rule 4 — tool_buttons must reference tools_v2.

The most common builder mistake is wiring an agent *action* (approve /
reject / submit) as a panel tool_button. tool_buttons are only for
deterministic tools_v2 calls. The validator must reject that with a
message that points the builder at the correct shape (QueueAction /
DetailSection type=approval) — otherwise the builder tends to just
delete the button and ship a feature-less app.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import PublishRequest  # noqa: E402


def _app(tool_name: str) -> dict:
    return {
        "spec_version": "v0",
        "app_id": "app-1",
        "slug": "kiln-triage",
        "title": "Kiln Triage",
        "kind": "app",
        "agent_id": "agent-1",
        "data_sources": [
            {"id": "ds1", "type": "smart_app_records", "ref": "queue_item"},
        ],
        "panels": [
            {
                "id": "approvals",
                "type": "queue",
                "data_source": "ds1",
                "tool_buttons": [{"label": "Approve", "tool_name": tool_name}],
            },
        ],
    }


def _agent() -> dict:
    return {
        "spec_version": "v0",
        "agent_id": "agent-1",
        "name": "Triage Agent",
        "model_tier": "tier_a",
        "system_prompt": "Do the triage job.",
        "actions": [
            {"name": "approve_corrective_action", "input_schema": {"type": "object"}},
        ],
        "tools_v2": [],
    }


def test_tool_button_pointing_at_agent_action_is_rejected_with_guidance():
    """An agent action wired as a tool_button → targeted error."""
    with pytest.raises(ValidationError) as exc:
        PublishRequest(
            session_id="bs_x",
            app_spec=_app("approve_corrective_action"),
            agent_spec=_agent(),
        )
    msg = str(exc.value)
    assert "is an AgentSpec *action*" in msg
    assert "QueueAction" in msg
    assert "type='approval'" in msg


def test_tool_button_pointing_at_nothing_is_rejected_plainly():
    """An unknown tool_name (neither tool nor action) → plain error."""
    with pytest.raises(ValidationError) as exc:
        PublishRequest(
            session_id="bs_x",
            app_spec=_app("totally_made_up_tool"),
            agent_spec=_agent(),
        )
    msg = str(exc.value)
    assert "not present in AgentSpec.tools_v2" in msg
    assert "is an AgentSpec *action*" not in msg
