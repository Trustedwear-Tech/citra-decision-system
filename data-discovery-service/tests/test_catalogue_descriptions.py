# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Draft-then-review catalogue descriptions — pure cores (Wave 2 #9).

Pins:
  * draft_to_response flattens enrich_dataset output for the review UI;
  * merge_descriptions applies approved table + column descriptions, matching
    columns by physical_name then name, marks approved/manual, and reports
    unmatched keys (a typo'd column is surfaced, never silently dropped);
  * a description edit NEVER renames a column (identifiers untouched);
  * descriptions are trimmed to the catalogue budget.
"""
from __future__ import annotations

from datetime import datetime, timezone

from catalogue_descriptions import (
    DESC_COL_MAX,
    draft_to_response,
    merge_descriptions,
)

_AT = datetime(2026, 7, 7, tzinfo=timezone.utc)


def _entry():
    return {
        "dataset_id": "claims_db.policies",
        "description": "old table desc",
        "columns": [
            {"name": "policy_no", "physical_name": "POL_NO", "type": "text",
             "description": "old"},
            {"name": "status", "physical_name": "STS", "type": "text"},
            {"name": "premium", "physical_name": None, "type": "number"},  # name-only
        ],
    }


# ── draft_to_response ────────────────────────────────────────────────────────
def test_draft_to_response_flattens_with_confidence():
    draft = {
        "table": {"name": "policies", "description": "Insurance policies.", "confidence": 0.9},
        "columns": {
            "POL_NO": {"name": "POL_NO", "description": "Policy number.", "confidence": 0.8},
            "STS": {"name": "STS", "description": "Status code.", "confidence": 0.4},
        },
    }
    out = draft_to_response("claims_db.policies", draft)
    assert out["table_description"] == "Insurance policies." and out["table_confidence"] == 0.9
    by = {c["physical_name"]: c for c in out["columns"]}
    assert by["POL_NO"]["description"] == "Policy number."
    assert by["STS"]["confidence"] == 0.4       # low-confidence surfaced for triage


def test_draft_to_response_tolerates_empty():
    out = draft_to_response("d", {})
    assert out["table_description"] == "" and out["columns"] == []


# ── merge_descriptions ───────────────────────────────────────────────────────
def test_merge_applies_table_and_columns_by_physical_then_name():
    update, matched, unmatched = merge_descriptions(
        _entry(),
        table_description="Insurance policies and their status.",
        column_descriptions={"POL_NO": "The policy number.",   # by physical_name
                             "premium": "Annual premium."},      # by name (no physical)
        actor="dba@x", at=_AT)
    assert update["description"] == "Insurance policies and their status."
    cols = {c["name"]: c for c in update["columns"]}
    assert cols["policy_no"]["description"] == "The policy number."
    assert cols["premium"]["description"] == "Annual premium."
    assert "description" not in cols["status"]   # not in the approved set → untouched
    assert set(matched) == {"POL_NO", "premium"} and unmatched == []
    # governance stamps
    assert update["mapping_status"] == "approved" and update["mapping_source"] == "manual"
    assert update["descriptions_edited_by"] == "dba@x" and update["descriptions_edited_at"] == _AT


def test_merge_reports_unmatched_keys():
    _u, matched, unmatched = merge_descriptions(
        _entry(), table_description=None,
        column_descriptions={"POL_NO": "ok", "GHOST_COL": "typo"},
        actor="d", at=_AT)
    assert matched == ["POL_NO"] and unmatched == ["GHOST_COL"]


def test_merge_never_renames_a_column():
    update, _m, _u = merge_descriptions(
        _entry(), table_description=None,
        column_descriptions={"POL_NO": "desc"}, actor="d", at=_AT)
    col = next(c for c in update["columns"] if c["name"] == "policy_no")
    assert col["name"] == "policy_no" and col["physical_name"] == "POL_NO"  # identifiers intact


def test_merge_trims_to_budget():
    update, _m, _u = merge_descriptions(
        _entry(), table_description=None,
        column_descriptions={"POL_NO": "x" * (DESC_COL_MAX + 50)}, actor="d", at=_AT)
    col = next(c for c in update["columns"] if c["name"] == "policy_no")
    assert len(col["description"]) == DESC_COL_MAX and col["description"].endswith("…")


def test_merge_none_table_leaves_description_key_absent():
    update, _m, _u = merge_descriptions(
        _entry(), table_description=None, column_descriptions={}, actor="d", at=_AT)
    assert "description" not in update      # table desc not touched when None
    assert update["mapping_status"] == "approved"
