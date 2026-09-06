# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
"""Queue cards load from the database.

A queue card's state (the chip, the modal, whether Apply is still possible)
lived in the browser tab that ran the review. The staging row already holds
the whole card and its fate; /apps/{slug}/queue-state hands it back per row.
These pin the pure conversion from a staged row to the body /run returns,
and the natural-key vocabulary that keeps one row per record.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def main_mod():
    os.environ.setdefault("JWT_SECRET", "queue-state-test-secret")
    os.environ.setdefault("MONGO_URI", "mongodb://localhost:27999/test")
    import main as _main
    return _main


def _row(**over):
    base = {
        "workflow_execution_id": "run_abc", "case_natural_key": "LAN-1", "slug": "loan",
        "status": "pending_review", "source": "queue_action",
        "llm_recommendation_text": "Approved — clean file", "llm_reasoning": "FOIR fine",
        "llm_evidence_summary": "summary", "planned_writes": [{"action_id": "record_credit_decision",
                                                               "payload": {"application_id": "LAN-1", "status": "approved"}}],
        "case_facets": ["product:personal", "sourcing_channel:dsa"], "notices": [],
        "display_context": {"application_id": "LAN-1"},
        "created_at": datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=20),
    }
    base.update(over)
    return base


def test_pending_row_becomes_a_pending_approval_card_with_its_plan(main_mod):
    b = main_mod._queue_state_body(_row())
    assert b["status"] == "pending_approval"
    assert b["decision"] == "Approved — clean file"
    assert b["planned_writes"][0]["payload"]["status"] == "approved"
    assert b["plan_hash"] == main_mod.compute_plan_hash(b["planned_writes"])
    assert b["case_facets"] == ["product:personal", "sourcing_channel:dsa"]
    assert b["notices"] == []


def test_expired_pending_row_says_so(main_mod):
    b = main_mod._queue_state_body(_row(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    assert b["status"] == "pending_approval"
    assert any(n.get("code") == "plan_expired" for n in b["notices"])


def test_applied_row_is_completed_with_its_write_events(main_mod):
    b = main_mod._queue_state_body(_row(status="applied", applied_by="ananya",
                                        audit_trail=[{"decision": "approved",
                                                      "write_events": [{"tool": "record_credit_decision", "status": "ok"}]}]))
    assert b["status"] == "completed"
    assert b["write_events"] == [{"tool": "record_credit_decision", "status": "ok"}]
    assert b["applied_by"] == "ananya"


def test_rejected_row_is_shown_as_rejected(main_mod):
    assert main_mod._queue_state_body(_row(status="rejected"))["status"] == "rejected"


@pytest.mark.parametrize("st", ["cancelled", "expired", "stale"])
def test_finished_rows_have_no_card(main_mod, st):
    assert main_mod._queue_state_body(_row(status=st)) is None


def test_records_are_keyed_by_what_the_source_calls_them(main_mod):
    f = main_mod._derive_case_natural_key
    assert f({"application_id": "LAN-1", "product": "auto"}) == "LAN-1"
    assert f({"loan_account_no": "LON-9"}) == "LON-9"
    assert f({"lead_id": "LD-3"}) == "LD-3"
    assert f({"claim_id": "CLM-2"}) == "CLM-2"
    assert f({"amount": 5}) is None
