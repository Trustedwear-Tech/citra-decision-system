# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for the dashboard PAGE contract (page.kind='dashboard').

The legacy top-level kind='dashboard' is retired. A dashboard is now a
kind='app' with one page whose page.kind == 'dashboard'. That page renders
the executive KPI/chart grid topped by the hero-brief copilot, which runs
the app's narrator agent in read-only chat_mode. The page allows only
chart / dashboard (KPI) / agent_chat / markdown panels and requires the app
to declare agent_id.

Legacy specs carrying kind='dashboard' are coerced to the new shape by
AppSpec._coerce_legacy_dashboard_kind (tested below).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import AppSpec  # noqa: E402
from validators import validate_app_spec  # noqa: E402


def _dashboard_app() -> dict:
    """A kind='app' with one dashboard page — the new shape."""
    return {
        "spec_version": "v0",
        "kind": "app",
        "agent_id": "claims-bi-narrator",  # powers the hero-brief copilot
        "slug": "claims-bi",
        "title": "Claims BI",
        "tenant_id": "tenant-1",
        "data_sources": [
            {"id": "claims", "type": "mcp", "ref": "insurance/claims_master"},
        ],
        "pages": [
            {
                "id": "overview",
                "path": "/",
                "title": "Overview",
                "kind": "dashboard",
                "panels": [
                    {
                        "id": "kpis",
                        "type": "dashboard",
                        "metrics": [
                            {"name": "Open claims", "agg": "count",
                             "data_source": "claims"},
                        ],
                    },
                    {
                        "id": "trend",
                        "type": "chart",
                        "chart_type": "line",
                        "data_source": "claims",
                        "x": "created_at",
                        "y": "amount",
                        "time_grain": "day",
                        "aggregation": "sum",
                    },
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_dashboard_app_validates():
    spec, _ = validate_app_spec(_dashboard_app())
    assert spec.kind == "app"
    assert spec.pages[0].kind == "dashboard"
    assert spec.agent_id == "claims-bi-narrator"
    assert spec.pages[0].panels[0].type == "dashboard"
    assert spec.pages[0].panels[1].type == "chart"


def test_dashboard_page_allows_markdown_and_agent_chat():
    payload = _dashboard_app()
    payload["pages"][0]["panels"].insert(
        0, {"id": "brief", "type": "markdown", "content": "## Today"}
    )
    payload["pages"][0]["panels"].append(
        {"id": "ask", "type": "agent_chat", "title": "Ask"}
    )
    spec, _ = validate_app_spec(payload)
    types = {p.type for p in spec.pages[0].panels}
    assert {"markdown", "agent_chat", "chart", "dashboard"} <= types


def test_multiple_dashboard_and_standard_pages():
    """An app may hold any number of dashboard pages and standard pages."""
    payload = _dashboard_app()
    base_page = payload["pages"][0]
    payload["pages"] = [
        {**base_page, "id": "ops", "title": "Operations", "kind": "dashboard"},
        {**base_page, "id": "fin", "title": "Finance", "kind": "dashboard"},
        {**base_page, "id": "comp", "title": "Compliance", "kind": "dashboard"},
        {
            "id": "q_outages", "title": "Outages", "kind": "standard",
            "panels": [{"id": "f1", "type": "form", "schema_ref": "x"}],
        },
        {
            "id": "q_theft", "title": "Theft", "kind": "standard",
            "panels": [{"id": "f2", "type": "form", "schema_ref": "x"}],
        },
    ]
    payload["navigation"] = {"style": "sidebar", "default_page": "ops"}
    spec, _ = validate_app_spec(payload)
    dash = [p.id for p in spec.pages if p.kind == "dashboard"]
    std = [p.id for p in spec.pages if p.kind == "standard"]
    assert dash == ["ops", "fin", "comp"]
    assert std == ["q_outages", "q_theft"]


def test_standard_page_alongside_dashboard_page():
    """An app may mix a dashboard page with a standard (form) page."""
    payload = _dashboard_app()
    payload["pages"].append(
        {
            "id": "intake",
            "title": "New claim",
            "kind": "standard",
            "panels": [
                {"id": "form1", "type": "form", "schema_ref": "x"},
            ],
        }
    )
    spec, _ = validate_app_spec(payload)
    assert spec.pages[0].kind == "dashboard"
    assert spec.pages[1].kind == "standard"


# ---------------------------------------------------------------------------
# Negative cases — the dashboard PAGE contract
# ---------------------------------------------------------------------------


def test_dashboard_page_requires_agent_id():
    payload = _dashboard_app()
    payload.pop("agent_id")
    with pytest.raises((PydanticValidationError, Exception)) as exc:
        validate_app_spec(payload)
    assert "agent_id" in str(exc.value)


def test_dashboard_page_rejects_form_panel():
    payload = _dashboard_app()
    payload["pages"][0]["panels"].append(
        {"id": "intake", "type": "form", "schema_ref": "x"}
    )
    with pytest.raises(Exception) as exc:
        validate_app_spec(payload)
    assert "dashboard page" in str(exc.value).lower()


def test_dashboard_page_chart_requires_data_source():
    """ChartPanel requires data_source (base schema + page contract)."""
    payload = _dashboard_app()
    payload["pages"][0]["panels"][1].pop("data_source")
    with pytest.raises(Exception):
        validate_app_spec(payload)


def test_dashboard_page_unknown_data_source_rejected():
    payload = _dashboard_app()
    payload["pages"][0]["panels"][1]["data_source"] = "ghost"
    with pytest.raises(Exception) as exc:
        validate_app_spec(payload)
    assert "ghost" in str(exc.value)


def test_app_kind_still_requires_agent_id():
    payload = _dashboard_app()
    payload.pop("agent_id")
    payload["pages"] = [
        {
            "id": "main",
            "kind": "standard",
            "panels": [{"id": "form1", "type": "form", "schema_ref": "x"}],
        }
    ]
    with pytest.raises(Exception) as exc:
        validate_app_spec(payload)
    assert "agent_id" in str(exc.value)


# ---------------------------------------------------------------------------
# Legacy coercion shim — kind='dashboard' → kind='app' + dashboard page
# ---------------------------------------------------------------------------


def test_legacy_dashboard_kind_coerced_pages():
    """A legacy multi-page dashboard coerces, marking its first page."""
    legacy = _dashboard_app()
    legacy["kind"] = "dashboard"
    spec = AppSpec.model_validate(legacy)
    assert spec.kind == "app"
    assert spec.pages[0].kind == "dashboard"


def test_legacy_dashboard_kind_coerced_panels_only():
    """A legacy panel-only dashboard wraps panels into one dashboard page."""
    legacy = {
        "spec_version": "v0",
        "kind": "dashboard",
        "agent_id": "narrator",
        "slug": "kpi",
        "title": "KPI",
        "tenant_id": "t1",
        "data_sources": [
            {"id": "claims", "type": "mcp", "ref": "insurance/claims"},
        ],
        "panels": [
            {
                "id": "trend",
                "type": "chart",
                "chart_type": "line",
                "data_source": "claims",
                "x": "created_at",
                "y": "amount",
            },
        ],
    }
    spec = AppSpec.model_validate(legacy)
    assert spec.kind == "app"
    assert spec.panels == []
    assert len(spec.pages) == 1
    assert spec.pages[0].kind == "dashboard"
    assert spec.pages[0].panels[0].type == "chart"
