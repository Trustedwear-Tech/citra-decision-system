# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""An API check names the lookup it judges, so the runtime can run it.

A-01 at publish: every check_evaluate tool must say which bound mcp read it
evaluates. Without it the check is a model decision - the agent fetches what it
likes and judges that. The autowire fills it in when there is exactly one REST
lookup to choose from; two or none is for the builder to say.
"""
from __future__ import annotations

from models import CheckEvaluateTool, McpTool
from publish_validators import validate_check_evaluates_a_bound_lookup
from required_lookup_autowire import autowire_required_lookups


class _Spec:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


def _lookup(name="bureau_credit", **kw):
    base = dict(name=name, source_id="bureau", tool_name="credit_score",
                dataset_id="bureau.credit_score", dataset_kind="rest",
                lookup_inputs={"required": ["pan"], "properties": {"pan": {"type": "string"}}})
    base.update(kw)
    return McpTool(**base)


def _check(**kw):
    base = dict(name="credit_check", task_type="credit-check")
    base.update(kw)
    return CheckEvaluateTool(**base)


def test_a_check_that_names_no_lookup_is_rejected_with_the_candidates():
    out = validate_check_evaluates_a_bound_lookup(_Spec([_lookup(), _check()]))
    assert len(out) == 1 and out[0]["rule_id"] == "A-01"
    assert "evaluates" in out[0]["location"] and "bureau_credit" in out[0]["reason"]


def test_a_check_naming_a_bound_lookup_passes():
    assert validate_check_evaluates_a_bound_lookup(_Spec([_lookup(), _check(evaluates="bureau_credit")])) == []


def test_a_check_naming_an_unknown_or_unbound_lookup_is_rejected():
    bad_name = validate_check_evaluates_a_bound_lookup(_Spec([_lookup(), _check(evaluates="nope")]))
    assert bad_name and "nope" in bad_name[0]["reason"] and "bureau_credit" in bad_name[0]["reason"]
    unbound = validate_check_evaluates_a_bound_lookup(
        _Spec([_lookup(dataset_id=None, dataset_kind=None, lookup_inputs=None), _check(evaluates="bureau_credit")]))
    assert unbound and "not bound" in unbound[0]["reason"]


def test_input_map_must_name_inputs_the_lookup_has():
    out = validate_check_evaluates_a_bound_lookup(
        _Spec([_lookup(), _check(evaluates="bureau_credit", input_map={"ssn": "applicant_ssn"})]))
    assert out and "ssn" in out[0]["reason"] and "pan" in out[0]["reason"]
    ok = validate_check_evaluates_a_bound_lookup(
        _Spec([_lookup(), _check(evaluates="bureau_credit", input_map={"pan": "applicant_pan"})]))
    assert ok == []


_CAT = {("bureau", "bureau.credit_score"): {"kind": "rest", "mandatory_when_used": True,
                                            "input_schema": {"required": ["pan"], "properties": {"pan": {}}}}}


def test_autowire_names_the_only_rest_lookup():
    spec = _Spec([_lookup(), _check()])
    autowire_required_lookups(spec, _CAT)
    assert spec.tools_v2[1].evaluates == "bureau_credit"
    assert validate_check_evaluates_a_bound_lookup(spec) == []


def test_autowire_leaves_an_ambiguous_choice_to_the_builder():
    spec = _Spec([_lookup(), _lookup(name="bureau_identity", dataset_id="bureau.identity"), _check()])
    cat = dict(_CAT); cat[("bureau", "bureau.identity")] = {"kind": "rest"}
    autowire_required_lookups(spec, cat)
    assert spec.tools_v2[2].evaluates is None
    out = validate_check_evaluates_a_bound_lookup(spec)
    assert out and "bureau_credit" in out[0]["reason"] and "bureau_identity" in out[0]["reason"]


def test_autowire_respects_an_authored_choice():
    spec = _Spec([_lookup(), _lookup(name="bureau_identity", dataset_id="bureau.identity"),
                  _check(evaluates="bureau_identity")])
    autowire_required_lookups(spec, _CAT)
    assert spec.tools_v2[2].evaluates == "bureau_identity"
