# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""U2/U3 designed panels + icon gate (docs/runtime-ui-modernization-plan.md).

Covers:
  * hero / stat_strip / timeline validate as Panel union members; their
    constraints hold (hero actions navigate-only, stat_strip 2-6 metrics).
  * Queue/detail presentation upgrades (split view, badge_colors semantic
    enum, column_formats, profile layout) accept valid input and reject junk.
  * Publish rule I-01: icons outside ICON_NAMES are rejected with locations;
    valid icons pass; legacy stray page icons only fail at PUBLISH (model
    load stays lenient — Page.icon is a free string).
  * hero/stat_strip are allowed on dashboard pages.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from models import (
    ICON_NAMES,
    DetailPanel,
    HeroPanel,
    QueuePanel,
    StatStripPanel,
    TimelinePanel,
)
from publish_validators import validate_icons


def test_hero_validates_and_rejects_agent_actions():
    ok = HeroPanel.model_validate({
        "id": "hero", "type": "hero", "icon": "zap",
        "headline": "Recovery operations",
        "subtitle": "Theft cases and collections at a glance",
        "metric": {"name": "recovered", "agg": "sum", "field": "value_amount",
                   "data_source": "ds_ledger", "icon": "banknote"},
        "actions": [{"label": "Open queue", "icon": "inbox",
                     "navigate": {"page": "funnel"}}],
    })
    assert ok.metric.icon == "banknote"
    with pytest.raises(ValidationError, match="navigation-only"):
        HeroPanel.model_validate({
            "id": "hero", "type": "hero", "headline": "X",
            "actions": [{"label": "Approve", "agent_action": "approve_case"}],
        })


def test_stat_strip_bounds():
    m = {"name": "n", "agg": "count", "data_source": "ds"}
    StatStripPanel.model_validate(
        {"id": "s", "type": "stat_strip", "metrics": [m, {**m, "name": "n2"}]})
    with pytest.raises(ValidationError):  # 1 metric — use a dashboard tile
        StatStripPanel.model_validate({"id": "s", "type": "stat_strip", "metrics": [m]})
    with pytest.raises(ValidationError):  # 7 metrics — too dense to mean anything
        StatStripPanel.model_validate({
            "id": "s", "type": "stat_strip",
            "metrics": [{**m, "name": f"n{i}"} for i in range(7)]})


def test_timeline_requires_bindings():
    TimelinePanel.model_validate({
        "id": "t", "type": "timeline", "data_source": "ds",
        "date_field": "decided_at", "title_field": "case",
        "badge_field": "outcome_label",
        "badge_colors": {"good": "green", "bad": "red"},
    })
    with pytest.raises(ValidationError):  # no date_field
        TimelinePanel.model_validate({
            "id": "t", "type": "timeline", "data_source": "ds",
            "title_field": "case"})
    with pytest.raises(ValidationError):  # hex is not a semantic color
        TimelinePanel.model_validate({
            "id": "t", "type": "timeline", "data_source": "ds",
            "date_field": "d", "title_field": "c",
            "badge_colors": {"good": "#00ff00"}})


def test_queue_split_and_formats():
    q = QueuePanel.model_validate({
        "id": "q", "type": "queue", "data_source": "ds", "view": "split",
        "badge_colors": {"pending": "amber", "recovered": "green"},
        "secondary_columns": ["fir_reference", "detection_date"],
        "column_formats": {"assessed_amount": "currency",
                           "detection_date": "relative_time",
                           "recovery_status": "status_pill"},
    })
    assert q.view == "split"
    with pytest.raises(ValidationError):
        QueuePanel.model_validate({
            "id": "q", "type": "queue", "data_source": "ds",
            "column_formats": {"x": "rainbow"}})


def test_detail_profile_layout():
    d = DetailPanel.model_validate({
        "id": "d", "type": "detail", "linked_to": "q", "layout": "profile",
        "header_fields": ["consumer_id", "assessed_amount"],
        "status_field": "recovery_status",
        "status_colors": {"recovered": "green", "written_off": "red"},
    })
    assert d.layout == "profile"
    with pytest.raises(ValidationError):
        DetailPanel.model_validate({
            "id": "d", "type": "detail", "linked_to": "q", "layout": "poster"})


def _app(pages):
    from models import AppSpec
    return AppSpec.model_validate({
        "spec_version": "v0", "slug": "t-app", "title": "T",
        "tenant_id": "t", "owner_id": "u", "agent_id": "ag1",
        "data_sources": [{"id": "ds", "type": "static", "ref": "rows"}],
        "pages": pages,
    })


def test_icon_gate_I01():
    assert "banknote" in ICON_NAMES and "sparkle-unicorn" not in ICON_NAMES
    app = _app([{
        "id": "home", "title": "Home", "icon": "sparkle-unicorn",
        "panels": [{
            "id": "k", "type": "dashboard", "icon": "gauge",
            "metrics": [{"name": "n", "agg": "count", "data_source": "ds",
                         "icon": "made-up-icon"}],
        }],
    }])  # model load is LENIENT — legacy stored specs must still open
    errs = validate_icons(app)
    locs = {e["location"] for e in errs}
    assert "pages[0].icon" in locs
    assert "pages[0].panels[0].metrics[0].icon" in locs
    assert all(e["rule_id"] == "I-01" for e in errs)
    assert len(errs) == 2  # 'gauge' is valid — not flagged

    clean = _app([{
        "id": "home", "title": "Home", "icon": "home",
        "panels": [{"id": "k", "type": "dashboard",
                    "metrics": [{"name": "n", "agg": "count",
                                 "data_source": "ds", "icon": "banknote"}]}],
    }])
    assert validate_icons(clean) == []


def test_hero_and_stat_strip_allowed_on_dashboard_pages():
    from models import AppSpec
    AppSpec.model_validate({
        "spec_version": "v0", "slug": "t-app", "title": "T",
        "tenant_id": "t", "owner_id": "u", "agent_id": "ag1",
        "data_sources": [{"id": "ds", "type": "static", "ref": "rows"}],
        "pages": [{
            "id": "exec", "title": "Exec", "kind": "dashboard",
            "panels": [
                {"id": "h", "type": "hero", "headline": "Operations"},
                {"id": "s", "type": "stat_strip",
                 "metrics": [{"name": "a", "agg": "count", "data_source": "ds"},
                             {"name": "b", "agg": "count", "data_source": "ds"}]},
            ],
        }],
    })
