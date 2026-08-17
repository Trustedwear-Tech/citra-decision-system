# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Ontology-driven default: a dataset marked mandatory_when_used in the
catalogue defaults the bound mcp read tool's required flag — unless the builder
explicitly authored it (app override wins)."""
from models import McpTool
from required_lookup_autowire import autowire_required_lookups


class _AgentSpec:
    def __init__(self, tools_v2):
        self.tools_v2 = tools_v2


def _mcp(**kw):
    base = dict(name="cibil_score", source_id="cibil", tool_name="cibil_score",
                dataset_id="cibil.score", dataset_kind="rest")
    base.update(kw)
    return McpTool(**base)


_CAT_MANDATORY = {("cibil", "cibil.score"): {"mandatory_when_used": True}}
_CAT_NOT = {("cibil", "cibil.score"): {"mandatory_when_used": False}}


def test_defaults_required_on_mandatory_dataset():
    spec = _AgentSpec([_mcp()])  # required omitted → not in model_fields_set
    assert autowire_required_lookups(spec, _CAT_MANDATORY) == 1
    assert spec.tools_v2[0].required is True


def test_noop_when_not_mandatory():
    spec = _AgentSpec([_mcp()])
    assert autowire_required_lookups(spec, _CAT_NOT) == 0
    assert spec.tools_v2[0].required is False


def test_noop_when_dataset_absent_from_catalogue():
    spec = _AgentSpec([_mcp()])
    assert autowire_required_lookups(spec, {}) == 0
    assert spec.tools_v2[0].required is False


def test_respects_explicit_override_false():
    # builder deliberately opted out → autowire must NOT re-enable it
    spec = _AgentSpec([_mcp(required=False)])
    assert "required" in spec.tools_v2[0].model_fields_set
    assert autowire_required_lookups(spec, _CAT_MANDATORY) == 0
    assert spec.tools_v2[0].required is False


def test_idempotent_when_already_required():
    spec = _AgentSpec([_mcp(required=True)])
    assert autowire_required_lookups(spec, _CAT_MANDATORY) == 0  # already True → no change counted
    assert spec.tools_v2[0].required is True


def test_skips_unbound_tool():
    # no dataset_id → not anchorable (W-09) → never auto-required
    spec = _AgentSpec([_mcp(dataset_id=None, dataset_kind=None)])
    assert autowire_required_lookups(spec, _CAT_MANDATORY) == 0
    assert spec.tools_v2[0].required is False


def test_ignores_non_mcp_tools():
    spec = _AgentSpec([])
    assert autowire_required_lookups(spec, _CAT_MANDATORY) == 0
