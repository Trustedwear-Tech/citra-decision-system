"""Tests for the AgentSpec.tools_v2 discriminated tool union, the
publish-time cost-gate (vision_ocr requires OCR_ENABLED), and the
AppSpec ↔ AgentSpec cross-validators (form panel ⇒ validate_form,
upload panel ⇒ vision_ocr).

Pure Pydantic-level tests (no FastAPI client) so they run in well under
a second and surface model-level regressions without spinning up auth /
Mongo / etc.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import (  # noqa: E402
    AgentSpec,
    AppSpec,
    FormPanel,
    FormOnSubmit,
    McpTool,
    PublishRequest,
    RagTool,
    ValidateFormTool,
    VisionOcrTool,
)


def _agent(tools_v2=None, system_prompt: str = "Always call validate_form first.") -> AgentSpec:
    return AgentSpec(
        agent_id="a1",
        name="A",
        system_prompt=system_prompt,
        tools_v2=tools_v2 or [],
    )


def _app(panels=None, agent_id: str | None = "a1") -> AppSpec:
    return AppSpec(
        slug="demo-app",
        title="Demo",
        agent_id=agent_id,
        panels=panels
        or [
            FormPanel(
                id="submit",
                type="form",
                schema_ref="claim_form",
                on_submit=FormOnSubmit(agent_action="process"),
            )
        ],
    )


# ---------------------------------------------------------------------------
# tools_v2 unit-level
# ---------------------------------------------------------------------------


def test_tools_v2_accepts_each_kind():
    a = _agent(
        tools_v2=[
            ValidateFormTool(name="vf", schema_ref="claim_form"),
            VisionOcrTool(name="ocr"),
            McpTool(name="lookup", source_id="insurance", tool_name="lookup_policy"),
            RagTool(name="search", source_id="insurance", top_k=5),
        ]
    )
    assert [t.kind for t in a.tools_v2] == [
        "validate_form",
        "vision_ocr",
        "mcp",
        "rag",
    ]


def test_tools_v2_rejects_duplicate_names():
    with pytest.raises(ValidationError) as exc:
        _agent(
            tools_v2=[
                ValidateFormTool(name="dup", schema_ref="x"),
                VisionOcrTool(name="dup"),
            ]
        )
    assert "duplicate tool name" in str(exc.value)


def test_validate_form_requires_system_prompt_mention():
    with pytest.raises(ValidationError) as exc:
        AgentSpec(
            agent_id="a1",
            name="A",
            system_prompt="You are an agent. Be helpful.",
            tools_v2=[ValidateFormTool(name="vf", schema_ref="x")],
        )
    msg = str(exc.value)
    assert "validate_form" in msg


def test_validate_form_must_precede_vision_ocr():
    with pytest.raises(ValidationError) as exc:
        _agent(
            tools_v2=[
                VisionOcrTool(name="ocr"),
                ValidateFormTool(name="vf", schema_ref="x"),
            ]
        )
    assert "validate_form" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# PublishRequest cross-validators
# ---------------------------------------------------------------------------


def test_publish_form_panel_requires_validate_form_tool():
    agent = _agent(
        tools_v2=[VisionOcrTool(name="ocr")],
        system_prompt="No form check needed here.",
    )
    app = _app()
    with pytest.raises(ValidationError) as exc:
        PublishRequest(app_spec=app, agent_spec=agent, session_id="s1")
    assert "validate_form" in str(exc.value).lower()


def test_publish_upload_panel_requires_vision_ocr_tool():
    agent = _agent(
        tools_v2=[ValidateFormTool(name="vf", schema_ref="claim_form")],
    )
    upload_panel = FormPanel(
        id="submit",
        type="form",
        schema_ref="claim_form",
        accepts_files=True,
        on_submit=FormOnSubmit(agent_action="process"),
    )
    app = _app(panels=[upload_panel])
    with pytest.raises(ValidationError) as exc:
        PublishRequest(app_spec=app, agent_spec=agent, session_id="s1")
    assert "vision_ocr" in str(exc.value).lower()


def test_publish_validate_form_schema_ref_must_resolve():
    agent = _agent(
        tools_v2=[ValidateFormTool(name="vf", schema_ref="nonexistent_form")],
    )
    app = _app()
    with pytest.raises(ValidationError) as exc:
        PublishRequest(app_spec=app, agent_spec=agent, session_id="s1")
    msg = str(exc.value).lower()
    assert "schema_ref" in msg or "nonexistent_form" in msg


def test_publish_happy_path_with_full_tool_set():
    agent = _agent(
        tools_v2=[
            ValidateFormTool(name="vf", schema_ref="claim_form"),
            VisionOcrTool(name="ocr"),
            McpTool(name="lookup", source_id="insurance", tool_name="lookup_policy"),
        ]
    )
    upload_panel = FormPanel(
        id="claim_form",
        type="form",
        accepts_files=True,
        accepted_file_types=["image/jpeg", "image/png"],
        on_submit=FormOnSubmit(agent_action="process"),
    )
    app = _app(panels=[upload_panel])
    # Must not raise.
    req = PublishRequest(app_spec=app, agent_spec=agent, session_id="s1")
    assert req.app_spec.slug == "demo-app"


# ---------------------------------------------------------------------------
# tool_buttons cross-validator
# ---------------------------------------------------------------------------


def test_publish_rejects_tool_button_pointing_at_unknown_tool():
    """Panels can only host buttons for tools that exist in tools_v2 —
    otherwise the runtime route would 404 silently."""
    from models import ToolButton

    agent = _agent(
        tools_v2=[
            ValidateFormTool(name="vf", schema_ref="claim_form"),
        ],
    )
    panel = FormPanel(
        id="claim_form",
        type="form",
        on_submit=FormOnSubmit(agent_action="process"),
        tool_buttons=[ToolButton(label="Refresh", tool_name="not_declared")],
    )
    app = _app(panels=[panel])
    with pytest.raises(ValidationError) as exc:
        PublishRequest(app_spec=app, agent_spec=agent, session_id="s1")
    assert "not_declared" in str(exc.value)


def test_publish_accepts_tool_button_matching_tools_v2():
    from models import ToolButton

    agent = _agent(
        tools_v2=[
            ValidateFormTool(name="vf", schema_ref="claim_form"),
            McpTool(name="run_letter", source_id="insurance", tool_name="send_letter"),
        ],
    )
    panel = FormPanel(
        id="claim_form",
        type="form",
        on_submit=FormOnSubmit(agent_action="process"),
        tool_buttons=[
            ToolButton(
                label="Send Letter",
                tool_name="run_letter",
                confirm="Are you sure?",
                args={"region": "north"},
            ),
        ],
    )
    app = _app(panels=[panel])
    req = PublishRequest(app_spec=app, agent_spec=agent, session_id="s1")
    assert req.app_spec.panels[0].tool_buttons[0].tool_name == "run_letter"
