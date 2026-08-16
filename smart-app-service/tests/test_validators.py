"""Tests for AppSpec / AgentSpec validators (JSON Schema + Pydantic)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validators import validate_agent_spec, validate_app_spec  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_app_spec_round_trips():
    payload = _load("claims_app_spec.json")
    spec, _ = validate_app_spec(payload)
    assert spec.slug == "claims-triage"
    assert len(spec.panels) == 5
    assert {p.type for p in spec.panels} == {
        "form",
        "queue",
        "detail",
        "dashboard",
        "agent_chat",
    }


def test_valid_agent_spec_round_trips():
    payload = _load("claims_agent_spec.json")
    spec, _ = validate_agent_spec(payload)
    assert spec.agent_id == "agent_claims_xyz"
    assert len(spec.sub_agents) == 3
    assert {sa.id for sa in spec.sub_agents} == {"compliance", "fraud_check", "report"}
    assert spec.actions[0].name == "intake_claim"


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_app_spec_rejects_bad_slug():
    payload = _load("claims_app_spec.json")
    payload["slug"] = "Bad Slug!"
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_app_spec(payload)


def test_app_spec_rejects_unknown_panel_type():
    payload = _load("claims_app_spec.json")
    payload["panels"].append({"id": "x", "type": "unknown_panel"})
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_app_spec(payload)


def test_app_spec_rejects_extra_top_level_field():
    payload = _load("claims_app_spec.json")
    payload["this_should_not_exist"] = True
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_app_spec(payload)


def test_app_spec_rejects_form_panel_without_id():
    payload = _load("claims_app_spec.json")
    bad = deepcopy(payload)
    bad["panels"][0].pop("id")
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_app_spec(bad)


def test_app_spec_rejects_dashboard_without_metrics():
    payload = _load("claims_app_spec.json")
    bad = deepcopy(payload)
    for panel in bad["panels"]:
        if panel["type"] == "dashboard":
            panel["metrics"] = []
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_app_spec(bad)


def test_agent_spec_rejects_subagent_with_invalid_id():
    payload = _load("claims_agent_spec.json")
    payload["sub_agents"][0]["id"] = "Bad-Id"
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_agent_spec(payload)


def test_agent_spec_rejects_unknown_model_tier():
    payload = _load("claims_agent_spec.json")
    payload["model_tier"] = "tier_z"
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_agent_spec(payload)


def test_agent_spec_requires_system_prompt():
    payload = _load("claims_agent_spec.json")
    payload["system_prompt"] = ""
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_agent_spec(payload)


def test_app_spec_requires_at_least_one_panel():
    payload = _load("claims_app_spec.json")
    payload["panels"] = []
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError)):
        validate_app_spec(payload)
