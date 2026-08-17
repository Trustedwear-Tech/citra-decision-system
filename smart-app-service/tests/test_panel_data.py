# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for /apps/{slug}/data/{panel_id} and ChartPanel validation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use")

import jwt
from fastapi.testclient import TestClient

import main
from auth import JWTAuthMiddleware
from models import AppSpec
from panel_data import resolve_panel_data


FIXTURES = Path(__file__).parent / "fixtures"


def _user_jwt(user_id: str = "user_abc", tenant_id: str = "bajaj") -> str:
    return jwt.encode(
        {"sub": user_id, "user_id": user_id, "tenant_id": tenant_id},
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


def _chart_app() -> dict:
    """Build a tiny app with a static data source + chart + queue + dashboard."""
    return {
        "spec_version": "v0",
        "slug": "sales-demo",
        "title": "Sales Demo",
        "tenant_id": "bajaj",
        "agent_id": "agent_sales_x",
        "audience": "org",
        "data_sources": [
            {
                "id": "weekly_sales",
                "type": "static",
                "ref": "inline:weekly_sales",
                "filters": {
                    "rows": [
                        {"week": "W1", "revenue": 1200, "region": "APAC"},
                        {"week": "W2", "revenue": 1450, "region": "APAC"},
                        {"week": "W3", "revenue": 1100, "region": "EU"},
                        {"week": "W4", "revenue": 1700, "region": "EU"},
                    ]
                },
            }
        ],
        "panels": [
            {
                "id": "trend",
                "type": "chart",
                "title": "Weekly revenue",
                "chart_type": "line",
                "data_source": "weekly_sales",
                "x": "week",
                "y": "revenue",
            },
            {
                "id": "rows",
                "type": "queue",
                "data_source": "weekly_sales",
                "columns": ["week", "revenue", "region"],
            },
            {
                "id": "kpis",
                "type": "dashboard",
                "metrics": [
                    {
                        "name": "total_rev",
                        "agg": "sum",
                        "field": "revenue",
                        "data_source": "weekly_sales",
                    }
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Pure resolver tests (no FastAPI / Mongo)
# ---------------------------------------------------------------------------


import asyncio


def _resolve(spec, panel_id):
    """Sync wrapper around the async resolve_panel_data for these tests.

    The MCP / RAG branches need httpx + a Settings object; the static branch
    these tests exercise does not touch the network, so a minimal Settings()
    plus asyncio.run() is sufficient.
    """
    from config import Settings

    return asyncio.run(
        resolve_panel_data(
            settings=Settings(jwt_secret="test-secret-32-chars-padding-aaaaaaa"),
            app_spec=spec,
            panel_id=panel_id,
        )
    )


def test_resolve_static_chart_returns_projected_rows():
    spec = AppSpec.model_validate(_chart_app())
    out = _resolve(spec, "trend")
    assert out.source_kind == "static"
    assert out.total == 4
    assert out.truncated is False
    assert out.columns == ["week", "revenue"]
    assert out.rows[0] == {"week": "W1", "revenue": 1200}


def test_resolve_static_queue_returns_declared_columns():
    spec = AppSpec.model_validate(_chart_app())
    out = _resolve(spec, "rows")
    assert out.columns == ["week", "revenue", "region"]
    assert len(out.rows) == 4
    assert out.rows[2]["region"] == "EU"


def test_resolve_dashboard_uses_metric_data_source():
    spec = AppSpec.model_validate(_chart_app())
    out = _resolve(spec, "kpis")
    assert out.source_kind == "static"
    # Dashboard projects only fields its metrics need.
    assert out.columns == ["revenue"]
    assert sum(r["revenue"] for r in out.rows) == 5450


def test_unknown_panel_raises_404():
    from fastapi import HTTPException

    spec = AppSpec.model_validate(_chart_app())
    with pytest.raises(HTTPException) as exc:
        _resolve(spec, "nope")
    assert exc.value.status_code == 404


def test_unbound_panel_raises_400():
    from fastapi import HTTPException

    app = _chart_app()
    app["panels"].append(
        {"id": "doc", "type": "markdown", "content": "hello"}
    )
    spec = AppSpec.model_validate(app)
    with pytest.raises(HTTPException) as exc:
        _resolve(spec, "doc")
    assert exc.value.status_code == 400




# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


class _StubCol:
    def __init__(self, docs):
        self._docs = docs

    async def find_one(self, query):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None


@pytest.fixture
def client_with_app(monkeypatch):
    spec = _chart_app()
    doc = {
        "app_id": "app_demo",
        "slug": spec["slug"],
        "tenant_id": spec["tenant_id"],
        "agent_id": spec["agent_id"],
        "status": "published",
        "version": 1,
        "app_spec": spec,
    }
    monkeypatch.setattr(main, "_apps_col", _StubCol([doc]))
    # Bypass lifespan
    main.app.router.lifespan_context = None  # type: ignore[attr-defined]
    return TestClient(main.app)


def test_endpoint_requires_jwt(client_with_app):
    r = client_with_app.get("/apps/sales-demo/data/trend")
    assert r.status_code == 401


def test_endpoint_returns_rows(client_with_app):
    r = client_with_app.get(
        "/apps/sales-demo/data/trend",
        headers={"Authorization": f"Bearer {_user_jwt()}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_kind"] == "static"
    assert body["total"] == 4
    assert body["columns"] == ["week", "revenue"]


def test_endpoint_404_for_other_tenant(client_with_app):
    other = _user_jwt(user_id="user_other", tenant_id="other_corp")
    r = client_with_app.get(
        "/apps/sales-demo/data/trend",
        headers={"Authorization": f"Bearer {other}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# ChartPanel schema acceptance
# ---------------------------------------------------------------------------


def test_chart_panel_validates_via_jsonschema():
    from validators import validate_app_spec

    validate_app_spec(_chart_app())  # should not raise


def test_chart_panel_rejects_bad_chart_type():
    """An unsupported chart_type must not publish.

    It is caught by the PYDANTIC layer now, not the JSON-Schema layer — the
    literal on ChartPanel.chart_type rejects it before jsonschema is consulted.
    Same guarantee, earlier, with a better message ("Input should be 'line',
    'bar', … input_value='donut'"), so this asserts the rejection rather than
    which validator produced it. Pinning the old exception type made the test
    fail while the behaviour it protects was working.
    """
    from jsonschema.exceptions import ValidationError as SchemaValidationError
    from pydantic import ValidationError as ModelValidationError

    from validators import validate_app_spec

    bad = _chart_app()
    bad["panels"][0]["chart_type"] = "donut"
    with pytest.raises((SchemaValidationError, ModelValidationError)) as exc:
        validate_app_spec(bad)
    assert "donut" in str(exc.value)
