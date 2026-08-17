# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests that the bundled motor-claim reference example is a valid
PublishRequest payload and exercises the full multi-tool path
(validate_form + vision_ocr + 2× mcp + rag).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXAMPLE_DIR = (
    Path(__file__).resolve().parents[1]
    / "builder-workspace"
    / "examples"
    / "motor-claim"
)


def test_motor_claim_specs_load_and_publish_validates():
    from models import AgentSpec, AppSpec, PublishRequest

    app_spec_data = json.loads((EXAMPLE_DIR / "app_spec.json").read_text())
    agent_spec_data = json.loads((EXAMPLE_DIR / "agent_spec.json").read_text())

    app_spec = AppSpec.model_validate(app_spec_data)
    agent_spec = AgentSpec.model_validate(agent_spec_data)

    # Tool kinds order matters: validate_form must precede vision_ocr.
    kinds = [t.kind for t in agent_spec.tools_v2]
    assert kinds == ["validate_form", "vision_ocr", "mcp", "mcp", "rag"]

    # Cross-validators on PublishRequest must pass on this reference.
    req = PublishRequest.model_validate(
        {
            "session_id": "bs_motor_claim",
            "app_spec": app_spec_data,
            "agent_spec": agent_spec_data,
        }
    )
    assert req.app_spec.kind == "app"
    assert req.agent_spec is not None


def test_motor_claim_app_spec_form_panel_accepts_files():
    from models import AppSpec, FormPanel

    app_spec_data = json.loads((EXAMPLE_DIR / "app_spec.json").read_text())
    app_spec = AppSpec.model_validate(app_spec_data)

    form_panels = [p for p in app_spec.panels if isinstance(p, FormPanel)]
    assert form_panels, "motor-claim example must contain a form panel"
    # At least one form panel should accept file uploads since the agent
    # depends on vision_ocr over user-uploaded photos.
    assert any(p.accepts_files for p in form_panels)


def test_motor_claim_system_prompt_mandates_validate_form():
    from models import AgentSpec

    agent_spec_data = json.loads((EXAMPLE_DIR / "agent_spec.json").read_text())
    agent_spec = AgentSpec.model_validate(agent_spec_data)
    assert "validate_form" in agent_spec.system_prompt
