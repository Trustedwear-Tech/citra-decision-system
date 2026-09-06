# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
"""E-05 — override is not opt-in.

Observed on acme-bank: every seeded write action declared ``editable_fields:
[]``. An officer triaging a claim saw the agent's SETTLE at ₹87,900 with every
field locked — no way to change the decision, the amount, or the reason. Their
only moves were "reject and re-run" or the core system by hand, and neither is
recorded as the override it was. The builder skill had called it opt-in; it is
not. A write the officer can only rubber-stamp is not a governed decision.

These pin the three shapes the rule catches and the one it must let through.
"""
from __future__ import annotations

from types import SimpleNamespace

from publish_validators import validate_editable_fields


def _field(name, control=None, options=None):
    return SimpleNamespace(name=name, control=control, options=options, editable=True)


def _static(*values):
    return SimpleNamespace(kind="static", values=[SimpleNamespace(value=v) for v in values])


def _tool(props, editable, required=None, name="record_credit_decision"):
    return SimpleNamespace(
        kind="mcp_action", name=name,
        input_schema={"properties": props,
                      "required": required or [next(iter(props))]},
        editable_fields=editable,
    )


def _agent(tool):
    return SimpleNamespace(tools_v2=[tool], tools=[tool])


def _e05(errs):
    return [e for e in errs if e.get("rule_id") == "E-05"]


CREDIT = {
    "application_id": {"type": "string"},
    "status": {"type": "string", "description": "approved | rejected | under_review."},
    "decision_reason": {"type": "string"},
    "decided_by": {"type": "string", "x-citra-fill": "actor"},
    "decided_at": {"type": "string", "x-citra-fill": "now"},
}


def test_no_editable_fields_at_all_is_rejected():
    errs = _e05(validate_editable_fields(None, _agent(_tool(CREDIT, []))))
    assert len(errs) == 1
    assert "declares no editable_fields" in errs[0]["reason"]
    # It names the disposition as the field to declare first.
    assert "'status'" in errs[0]["reason"]


def test_editable_without_the_disposition_is_rejected():
    tool = _tool(CREDIT, [_field("decision_reason", control="textarea")])
    errs = _e05(validate_editable_fields(None, _agent(tool)))
    assert len(errs) == 1
    assert "'status' is the disposition" in errs[0]["reason"]


def test_disposition_without_options_is_rejected():
    # Editable, but the officer would type the verdict free-hand.
    tool = _tool(CREDIT, [_field("status"), _field("decision_reason", control="textarea")])
    errs = _e05(validate_editable_fields(None, _agent(tool)))
    assert len(errs) == 1
    assert "no options to pick from" in errs[0]["reason"]


def test_disposition_select_with_options_passes():
    tool = _tool(CREDIT, [
        _field("status", control="select", options=_static("approved", "rejected", "under_review")),
        _field("decision_reason", control="textarea"),
    ])
    assert _e05(validate_editable_fields(None, _agent(tool))) == []


def test_enum_field_counts_as_the_disposition():
    props = {"claim_id": {"type": "string"},
             "verdict_code": {"type": "string", "enum": ["pass", "fail"]}}
    errs = _e05(validate_editable_fields(None, _agent(_tool(props, []))))
    assert len(errs) == 1 and "'verdict_code'" in errs[0]["reason"]


def test_assignment_write_needs_some_editable_field_but_no_disposition():
    # No status-like field and no enum: the rule still refuses an empty
    # declaration (the officer must be able to change the assignee) but does
    # not demand a select once something decidable is editable.
    props = {"claim_id": {"type": "string"}, "surveyor_id": {"type": "string"}}
    errs = _e05(validate_editable_fields(None, _agent(_tool(props, [], name="assign_surveyor"))))
    assert len(errs) == 1 and "'surveyor_id'" in errs[0]["reason"]
    ok = _tool(props, [_field("surveyor_id", control="select",
                              options=SimpleNamespace(kind="agent", values=None))],
               name="assign_surveyor")
    assert _e05(validate_editable_fields(None, _agent(ok))) == []


def test_key_and_server_filled_fields_alone_need_nothing():
    # A write whose only non-key fields are filled by the server has nothing
    # for the officer to decide, so an empty declaration is honest.
    props = {"application_id": {"type": "string"},
             "decided_by": {"type": "string", "x-citra-fill": "actor"},
             "decided_at": {"type": "string", "x-citra-fill": "now"}}
    assert _e05(validate_editable_fields(None, _agent(_tool(props, [])))) == []
