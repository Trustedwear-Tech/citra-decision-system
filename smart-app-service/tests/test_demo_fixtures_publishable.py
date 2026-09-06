# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
"""The seeded demo apps must publish clean on a fresh install.

`make install` / the wizard publish `demo-data/tenants/acme-bank/apps/*.json`
through /publish. Nothing checked those fixtures against the publish rules
until an installed user hit the gap: every seeded write action had
`editable_fields: []`, so an officer could not overrule a single proposed
value. A rule was added (E-05) and the fixtures fixed; this test is what keeps
the two from drifting apart again.

It runs the same pure, spec-shape rules /builder/validate and /publish run on
a stateless spec. Rules that need the catalogue (unknown dataset / column /
action) cannot run here and are exercised by the seed itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from models import AgentSpec, AppSpec
from publish_validators import (
    reject_allow_writes_in_chat,
    validate_case_signature,
    validate_case_signature_confirmed,
    validate_case_signature_projection,
    validate_chart_axes,
    validate_dashboard_page_has_narrator,
    validate_direct_write_buttons_confirm,
    validate_editable_fields,
    validate_factor_checks_can_score,
    validate_factor_set,
    validate_grounding_contract,
    validate_icons,
    validate_internal_audience,
    validate_item_tools_declare_task_type,
    validate_mcp_action_has_input_schema,
    validate_no_admin_actions,
    validate_no_delete_verbs,
    validate_no_media_columns,
    validate_required_lookup_is_bound,
    validate_rubric_finding_matches_declaration,
    validate_update_has_identifier,
)

FIXTURES = sorted(
    (Path(__file__).resolve().parents[2] / "demo-data" / "tenants" / "acme-bank" / "apps").glob("*.json")
)


def _rules(app, agent):
    return [
        ("H-04", reject_allow_writes_in_chat(agent)),
        ("T-03", validate_no_admin_actions(agent, catalogue_index=None)),
        ("G-01", validate_grounding_contract(agent)),
        ("update_identifier", validate_update_has_identifier(agent)),
        ("mcp_action_input_schema", validate_mcp_action_has_input_schema(agent)),
        ("W-01", validate_no_delete_verbs(app, agent)),
        ("D-02", validate_dashboard_page_has_narrator(app, agent)),
        ("editable_fields", validate_editable_fields(app, agent)),
        ("W-06", validate_direct_write_buttons_confirm(app, agent)),
        ("F-01", validate_no_media_columns(app)),
        ("FS-06", validate_factor_checks_can_score(app, agent)),
        ("M-01", validate_item_tools_declare_task_type(agent)),
        ("lookup_bound", validate_required_lookup_is_bound(agent)),
        ("S-01", validate_internal_audience(app)),
        ("V-CHART-01", validate_chart_axes(app)),
        ("I-01", validate_icons(app)),
        ("CS-01", validate_case_signature(app)),
        ("CS-02", validate_case_signature_projection(app)),
        ("CS-04", validate_case_signature_confirmed(app)),
        ("FS-01", validate_factor_set(app)),
        ("FS-05", validate_rubric_finding_matches_declaration(app)),
    ]


def test_there_are_four_demo_apps():
    assert [p.name for p in FIXTURES] == [
        "01_loan_triage.json", "02_collections_priority.json",
        "03_claim_triage.json", "04_sales_performance.json",
    ]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_demo_fixture_passes_every_spec_shape_publish_rule(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    app = AppSpec.model_validate(doc["app_spec"])
    agent = AgentSpec.model_validate(doc["agent_spec"])
    failures = [(rid, errs) for rid, errs in _rules(app, agent) if errs]
    assert not failures, f"{path.name} would be refused at publish: {failures}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_seeded_write_action_can_be_overruled(path):
    """The specific gap that motivated this file, pinned on its own so the
    message names it: every mcp_action declares editable fields, and the
    disposition among them is a select with options."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    for t in doc["agent_spec"].get("tools_v2", []):
        if t.get("kind") != "mcp_action":
            continue
        ef = t.get("editable_fields") or []
        assert ef, f"{path.name}: {t['name']} has no officer-editable fields"
        names = {f["name"] for f in ef}
        props = (t.get("input_schema") or {}).get("properties") or {}
        disposition = next((p for p in ("status", "decision", "outcome") if p in props), None)
        if disposition:
            assert disposition in names, f"{path.name}: {t['name']} disposition '{disposition}' is not editable"
            fs = next(f for f in ef if f["name"] == disposition)
            assert fs.get("control") == "select" and (fs.get("options") or {}).get("values"), (
                f"{path.name}: {t['name']}.{disposition} must be a select with options"
            )
