"""E-04 — an override must not commit the agent's justification for the
OPPOSITE decision.

Observed on acme-bank: `record_credit_decision` declared only `status` as
officer-editable. An officer overriding rejected -> approved committed the row
as approved while `decision_reason` still read "FOIR above policy cap — 89.37%
exceeds the 50% maximum…". On a credit file that is the sentence a regulator
reads, arguing against the decision recorded beside it.

The check is name-based and says so; these tests pin both what it catches and
what it deliberately does not.
"""
from __future__ import annotations

from types import SimpleNamespace

from publish_validators import validate_editable_fields


def _tool(props, editable, name="record_credit_decision"):
    return SimpleNamespace(
        kind="mcp_action",
        name=name,
        input_schema={"properties": props},
        editable_fields=[SimpleNamespace(name=n, options=None, editable=True)
                         for n in editable],
    )


def _agent(tool):
    return SimpleNamespace(tools_v2=[tool], tools=[tool])


def _e04(errs):
    return [e for e in errs if e.get("rule_id") == "E-04"]


def test_editable_decision_without_editable_reason_is_rejected():
    tool = _tool(
        {"application_id": {"type": "string"},
         "status": {"type": "string"},
         "decision_reason": {"type": "string"}},
        editable=["status"],
    )
    errs = _e04(validate_editable_fields(None, _agent(tool)))
    assert len(errs) == 1
    assert "decision_reason" in errs[0]["reason"]
    assert "OPPOSITE" in errs[0]["reason"]


def test_no_error_once_the_justification_is_editable_too():
    tool = _tool(
        {"application_id": {"type": "string"},
         "status": {"type": "string"},
         "decision_reason": {"type": "string"}},
        editable=["status", "decision_reason"],
    )
    assert _e04(validate_editable_fields(None, _agent(tool))) == []


def test_server_filled_justification_is_not_the_officers_to_write():
    """A field the platform stamps (x-citra-fill) must stay uneditable — making
    it officer-editable would invite forging an audit value."""
    tool = _tool(
        {"status": {"type": "string"},
         "decision_note": {"type": "string", "x-citra-fill": "actor"}},
        editable=["status"],
    )
    assert _e04(validate_editable_fields(None, _agent(tool))) == []


def test_action_with_no_editable_fields_is_not_flagged():
    """Nothing is overridable, so no override can contradict anything."""
    tool = _tool({"status": {"type": "string"},
                  "decision_reason": {"type": "string"}}, editable=[])
    assert _e04(validate_editable_fields(None, _agent(tool))) == []


def test_the_lint_is_name_based_and_misses_unconventional_names():
    """Honest about its own limit: a justification field named outside the
    convention is not recognised. Documented so nobody reads a clean publish as
    proof the record cannot contradict itself."""
    tool = _tool({"status": {"type": "string"},
                  "why_text": {"type": "string"}}, editable=["status"])
    assert _e04(validate_editable_fields(None, _agent(tool))) == []
