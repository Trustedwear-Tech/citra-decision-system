# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Round-trip publish validation for the expanded UI component set.

Exercises validators.validate_app_spec (the real publish gate — JSON Schema +
Pydantic) for every component added in the UI-component expansion: notice,
calendar, map, kanban queue, gauge KPI, stepper form, funnel/scatter charts,
attachment + collapsible detail sections, typeahead/currency/time form fields.
A positive guard so a future schema/model edit that breaks one of these fails CI.
"""

from __future__ import annotations

import pytest

from validators import validate_app_spec, JsonSchemaValidationError, PydanticValidationError


def _spec(panels):
    return {
        "spec_version": "v0",
        "slug": "ui-components-demo",
        "title": "UI Components Demo",
        "agent_id": "demo_agent",
        "data_sources": [{"id": "ds", "type": "mcp", "ref": "s.t"}],
        "panels": panels,
    }


def test_notice_panel_publishes():
    validate_app_spec(_spec([
        {"id": "n", "type": "notice", "tone": "warn", "content": "Heads up."},
    ]))


def test_calendar_and_map_panels_publish():
    validate_app_spec(_spec([
        {"id": "cal", "type": "calendar", "data_source": "ds",
         "date_field": "due", "title_field": "name", "color_field": "status"},
        {"id": "map", "type": "map", "data_source": "ds",
         "lat_field": "lat", "lng_field": "lng", "label_field": "site"},
    ]))


def test_kanban_queue_publishes():
    validate_app_spec(_spec([
        {"id": "board", "type": "queue", "data_source": "ds",
         "view": "kanban", "group_by": "status", "columns": ["id", "status"]},
    ]))


def test_kanban_without_group_by_is_rejected():
    with pytest.raises((JsonSchemaValidationError, PydanticValidationError, Exception)):
        validate_app_spec(_spec([
            {"id": "board", "type": "queue", "data_source": "ds", "view": "kanban"},
        ]))


def test_gauge_kpi_publishes():
    validate_app_spec(_spec([
        {"id": "kpi", "type": "dashboard", "metrics": [
            {"name": "collected", "agg": "sum", "field": "amount",
             "target": 5_000_000, "thresholds": [0.5, 0.8]},
        ]},
    ]))


def test_stepper_form_publishes():
    validate_app_spec(_spec([
        {"id": "wiz", "type": "form", "schema_inline": {"type": "object"},
         "steps": [{"title": "Step 1", "fields": ["a"]}],
         "on_submit": {"tool_name": "t"}},
    ]))


@pytest.mark.parametrize("chart_type", ["funnel", "scatter", "bar", "line", "area", "pie"])
def test_chart_types_publish(chart_type):
    validate_app_spec(_spec([
        {"id": "c", "type": "chart", "chart_type": chart_type,
         "data_source": "ds", "x": "stage", "y": "n"},
    ]))


def test_detail_attachment_and_collapsible_publish():
    validate_app_spec(_spec([
        {"id": "board", "type": "queue", "data_source": "ds", "columns": ["id"]},
        {"id": "det", "type": "detail", "linked_to": "board", "sections": [
            {"type": "attachment", "fields": ["photo"], "collapsible": True, "collapsed": True},
            {"type": "fields"},
        ]},
    ]))


def test_typeahead_currency_time_fields_publish():
    # Form field controls are inferred from JSON-Schema hints inside
    # schema_inline; they validate as a free-form object on the panel.
    validate_app_spec(_spec([
        {"id": "f", "type": "form", "on_submit": {"tool_name": "t"},
         "schema_inline": {
             "type": "object",
             "properties": {
                 "amount": {"type": "number", "format": "currency"},
                 "at": {"type": "string", "format": "time"},
                 "ref": {"type": "string", "format": "readonly", "default": "X"},
                 "consumer": {"type": "string", "options_source": {
                     "kind": "data_source", "data_source": "ds",
                     "value_column": "id", "search": True}},
             },
         }},
    ]))
