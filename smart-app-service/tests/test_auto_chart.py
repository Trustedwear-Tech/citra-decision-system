# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for auto_chart.maybe_inject_chart_panel."""

from __future__ import annotations

from auto_chart import maybe_inject_chart_panel
from models import AppSpec


def _base_spec(panels):
    return {
        "spec_version": "v0",
        "slug": "demo-app",
        "title": "Demo",
        "agent_id": "agent_x",
        "data_sources": [
            {"id": "ds_main", "type": "static", "ref": "inline:x"}
        ],
        "panels": panels,
    }


def test_injects_line_chart_when_queue_has_time_and_numeric_columns():
    spec = AppSpec.model_validate(
        _base_spec([
            {
                "id": "claims_q",
                "type": "queue",
                "data_source": "ds_main",
                "columns": ["claim_id", "insured", "amount", "status", "sla_due"],
            }
        ])
    )
    out = maybe_inject_chart_panel(spec)
    assert len(out.panels) == 2
    chart = out.panels[1]
    assert chart.type == "chart"
    assert chart.chart_type == "line"
    assert chart.x == "sla_due"  # first time-like column
    assert chart.y == "amount"   # first numeric-like column
    assert chart.data_source == "ds_main"




def test_no_op_when_chart_already_present():
    spec = AppSpec.model_validate(
        _base_spec([
            {
                "id": "q",
                "type": "queue",
                "data_source": "ds_main",
                "columns": ["a", "amount"],
            },
            {
                "id": "trend",
                "type": "chart",
                "chart_type": "line",
                "data_source": "ds_main",
                "x": "a",
                "y": "amount",
            },
        ])
    )
    out = maybe_inject_chart_panel(spec)
    assert out is spec  # untouched




def test_no_op_when_no_queue():
    spec = AppSpec.model_validate(
        _base_spec([
            {"id": "intro", "type": "markdown", "content": "hi"}
        ])
    )
    out = maybe_inject_chart_panel(spec)
    assert out is spec


def test_chart_id_does_not_collide():
    spec = AppSpec.model_validate(
        _base_spec([
            {
                "id": "q",
                "type": "queue",
                "data_source": "ds_main",
                "columns": ["day", "revenue"],
            },
            {
                "id": "q_chart",
                "type": "markdown",
                "content": "placeholder with reserved id",
            },
        ])
    )
    out = maybe_inject_chart_panel(spec)
    chart_ids = [p.id for p in out.panels if p.type == "chart"]
    assert chart_ids == ["q_chart_2"]


def test_chart_inserted_after_dashboard_when_present():
    spec = AppSpec.model_validate(
        _base_spec([
            {
                "id": "q",
                "type": "queue",
                "data_source": "ds_main",
                "columns": ["day", "revenue"],
            },
            {
                "id": "kpi",
                "type": "dashboard",
                "metrics": [
                    {
                        "name": "rev",
                        "agg": "sum",
                        "field": "revenue",
                        "data_source": "ds_main",
                    }
                ],
            },
            {"id": "notes", "type": "markdown", "content": "x"},
        ])
    )
    out = maybe_inject_chart_panel(spec)
    types = [p.type for p in out.panels]
    assert types == ["queue", "dashboard", "chart", "markdown"]


# ---------------------------------------------------------------------------
# Embed pages — the injector must keep its hands off.
#
# REGRESSION. maybe_inject_chart_panel runs at publish AFTER AppSpec has
# validated, and AppSpec forbids chart/map on an embed page (the embed bundle
# aliases echarts and leaflet away, so the panel cannot render inside a
# customer's application). Injecting there wrote a document that the very same
# model then refused to load — bricking the app on EVERY read, including the
# publish smoke gate, with an opaque 500.
#
# That is exactly what happened to `loan-credit-decision`: the builder authored
# queue+detail correctly, publish appended `trigger_chart`, and every subsequent
# read raised ValidationError.
# ---------------------------------------------------------------------------


def _embed_page_spec(extra_pages=None):
    return {
        "spec_version": "v0",
        "slug": "embed-app",
        "title": "Embed",
        "agent_id": "agent_x",
        "data_sources": [
            {"id": "ds_main", "type": "static", "ref": "inline:x"}
        ],
        "pages": [
            {
                "id": "card",
                "kind": "embed",
                "title": "Decision",
                # Deliberately chart-worthy: a time column AND a measure. Without
                # the guard this is precisely the shape that gets a chart.
                "panels": [
                    {
                        "id": "trigger",
                        "type": "queue",
                        "data_source": "ds_main",
                        "columns": ["application_id", "amount", "created_at"],
                        # An embed page must carry a trigger to validate. A
                        # QUEUE trigger is the legacy shape (detail.actions is
                        # now preferred) — kept here deliberately, because a
                        # queue is the only panel the chart injector acts on,
                        # so it is the only shape that can reproduce the bug
                        # this fixture guards against.
                        "actions": [{"label": "Review",
                                     "agent_action": "review_application"}],
                    }
                ],
            },
            *(extra_pages or []),
        ],
    }


def test_never_injects_a_chart_into_an_embed_page():
    spec = AppSpec.model_validate(_embed_page_spec())
    out = maybe_inject_chart_panel(spec)
    types = [p.type for p in out.pages[0].panels]
    assert types == ["queue"], f"a chart was injected into an embed page: {types}"


def test_embed_page_result_still_validates():
    """The invariant the 500 violated: whatever the injector returns must be
    loadable by the model that will read it back."""
    spec = AppSpec.model_validate(_embed_page_spec())
    out = maybe_inject_chart_panel(spec)
    AppSpec.model_validate(out.model_dump(mode="json"))  # must not raise


def test_embed_page_is_skipped_but_a_standard_page_still_gets_its_chart():
    """The guard must skip the embed page, not abort injection altogether —
    a mixed app should still chart the page that can render one."""
    spec = AppSpec.model_validate(_embed_page_spec(extra_pages=[{
        "id": "ops",
        "kind": "standard",
        "title": "Ops",
        "panels": [{
            "id": "all_apps",
            "type": "queue",
            "data_source": "ds_main",
            "columns": ["application_id", "amount", "created_at"],
        }],
    }]))
    out = maybe_inject_chart_panel(spec)
    assert [p.type for p in out.pages[0].panels] == ["queue"]
    assert [p.type for p in out.pages[1].panels] == ["queue", "chart"]
