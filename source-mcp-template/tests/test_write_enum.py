# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
"""A declared `enum` on a write action's input is enforced at execute time.

Found on acme-bank: the registry described `status` as "approved | rejected |
under_review" in prose only, so a model proposing "approve" would have been
written into a column that holds "approved". With the enum declared, the MCP
now refuses it on dry_run and on the real call alike.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalogue import _validate_payload  # noqa: E402
from models import WriteAction  # noqa: E402


def _action():
    return WriteAction(
        id="record_credit_decision", verb="update",
        input_schema={
            "required": ["application_id", "status"],
            "properties": {
                "application_id": {"type": "string"},
                "status": {"type": "string", "enum": ["approved", "rejected", "under_review"]},
                "decision_reason": {"type": "string"},
            },
        },
    )


def test_value_inside_enum_passes():
    _validate_payload(_action(), {"application_id": "LAN-1", "status": "under_review"})


def test_value_outside_enum_is_422_and_names_the_allowed_values():
    with pytest.raises(HTTPException) as ei:
        _validate_payload(_action(), {"application_id": "LAN-1", "status": "approve"})
    assert ei.value.status_code == 422
    assert "status='approve'" in ei.value.detail
    assert "under_review" in ei.value.detail


def test_fields_without_enum_are_free():
    _validate_payload(_action(), {"application_id": "LAN-1", "status": "approved",
                                  "decision_reason": "anything the officer wrote"})


def test_missing_required_still_reported_first():
    with pytest.raises(HTTPException) as ei:
        _validate_payload(_action(), {"application_id": "LAN-1"})
    assert "Missing required" in ei.value.detail
