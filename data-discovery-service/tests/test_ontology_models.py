# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Ontology-review O2: the catalogue models must PRESERVE what the MCP
describe layer emits — pydantic's default extra='ignore' silently stripped the
DecisionHistory outcome-signal contract, so the outcome poller / builder could
never derive read-back config from the catalogue even though IT declared it
in sources.json.
"""
from __future__ import annotations

from models import CatalogueColumn, CatalogueDecisionHistory, CatalogueEntry


def test_decision_history_preserves_outcome_signal_contract():
    # Exactly the dict shape the MCP DatasetSchema.decision_history emits.
    d = CatalogueDecisionHistory(
        is_decision_record=True,
        decision_column="status",
        timestamp_column="decided_at",
        terminal_states=["approved", "rejected"],
        reasoning_column="notes",
        outcome_field="status",
        good_values=["repaired", "recovered"],
        bad_values=["reopened", "written_off"],
        neutral_values=["escalated"],
        outcome_hold_field="status",
        key_field="case_id",
        settling_window_days=14,
    )
    dumped = d.model_dump()
    for f in ("outcome_field", "good_values", "bad_values", "neutral_values",
              "outcome_hold_field", "key_field", "settling_window_days"):
        assert f in dumped, f"outcome-signal field {f} stripped by the model"
    assert dumped["good_values"] == ["repaired", "recovered"]
    assert dumped["settling_window_days"] == 14


def test_display_name_is_additive_never_replaces_physical_name():
    # `name` MUST stay == physical (url_columns / role matching keys on it);
    # the BA-authored describe name rides additively as display_name.
    col = CatalogueColumn(
        name="defect_photo_url", physical_name="defect_photo_url",
        type="string", display_name="Defect photo",
    )
    assert col.name == "defect_photo_url"
    assert col.display_name == "Defect photo"

    entry = CatalogueEntry(
        tenant_id="acme", source_id="ops", dataset_id="ops.inspections",
        name="equipment_inspections", physical_name="equipment_inspections",
        display_name="Equipment inspections", kind="sql",
    )
    assert entry.name == entry.physical_name
    assert entry.display_name == "Equipment inspections"
