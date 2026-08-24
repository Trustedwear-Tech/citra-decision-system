# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
NeighborSamplesTool — verify the Milvus filter-builder + the result
reshaper directly. These are pure functions / single-responsibility
helpers, so no Milvus is needed; just import and exercise.

Coverage:
  1. ``_build_neighbor_filter('canonical', ...)`` always pins is_canonical=true
  2. ``_build_neighbor_filter('neighbors', exclude_canonical=True)`` pins
     is_canonical=false; False omits that clause
  3. decision_filter / severity_filter compose correctly
  4. ``_format_milvus_hit`` parses input_json / output_json safely (handles
     malformed JSON, missing fields)
  5. ``_query_neighbor_samples`` raises ``milvus_unavailable`` when pymilvus
     is missing (cold-start safety net)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "smart-app-service"))


def test_canonical_filter_pins_is_canonical_true():
    from tools_v2_dispatch import _build_neighbor_filter  # noqa: E402
    expr = _build_neighbor_filter(
        mode="canonical",
        decision_filter=None, severity_filter=None,
        exclude_canonical=True,  # ignored in canonical mode
    )
    assert "is_canonical == true" in expr


def test_neighbors_filter_excludes_canonical_when_requested():
    from tools_v2_dispatch import _build_neighbor_filter  # noqa: E402
    expr = _build_neighbor_filter(
        mode="neighbors",
        decision_filter=None, severity_filter=None,
        exclude_canonical=True,
    )
    assert "is_canonical == false" in expr


def test_neighbors_filter_does_not_exclude_canonical_when_off():
    from tools_v2_dispatch import _build_neighbor_filter  # noqa: E402
    expr = _build_neighbor_filter(
        mode="neighbors",
        decision_filter=None, severity_filter=None,
        exclude_canonical=False,
    )
    assert "is_canonical" not in expr


def test_decision_and_severity_compose():
    from tools_v2_dispatch import _build_neighbor_filter  # noqa: E402
    expr = _build_neighbor_filter(
        mode="neighbors",
        decision_filter="APPROVE",
        severity_filter="low",
        exclude_canonical=True,
    )
    assert 'decision == "APPROVE"' in expr
    assert 'severity == "low"' in expr
    assert " and " in expr


def test_quote_in_filter_value_is_escaped():
    """Defensive: a malformed BA-supplied string mustn't break the expr."""
    from tools_v2_dispatch import _build_neighbor_filter  # noqa: E402
    expr = _build_neighbor_filter(
        mode="neighbors",
        decision_filter='AP"PROVE',
        severity_filter=None,
        exclude_canonical=False,
    )
    # The escape need only be enough to prevent malformed Milvus expr;
    # we just assert the unescaped form does NOT appear.
    assert 'decision == "AP"PROVE"' not in expr


def test_format_hit_parses_json_strings():
    from tools_v2_dispatch import _format_milvus_hit  # noqa: E402
    hit = {
        "entity": {
            "source_id": "C001",
            "decision": "APPROVE",
            "severity": "low",
            "is_canonical": True,
            "input_json": '{"claim_amount": 800}',
            "output_json": '{"decision": "APPROVE", "amount_paid": 800}',
            "reasoning_trace": "auto-approve",
        },
        "distance": 0.93,
    }
    out = _format_milvus_hit(hit)
    assert out["source_id"] == "C001"
    assert out["decision"] == "APPROVE"
    assert out["input"] == {"claim_amount": 800}
    assert out["output"] == {"decision": "APPROVE", "amount_paid": 800}


def test_format_hit_survives_malformed_json():
    from tools_v2_dispatch import _format_milvus_hit  # noqa: E402
    out = _format_milvus_hit({
        "entity": {
            "source_id": "C002",
            "input_json": "{not valid json",
            "output_json": '{}',
        },
    })
    # Falls back to the raw string rather than crashing.
    assert out["source_id"] == "C002"
    assert out["input"] == "{not valid json"


@pytest.mark.asyncio
async def test_query_raises_when_pymilvus_missing(monkeypatch):
    """If pymilvus is not installed, the function must raise with a clear
    code — silent failure would hide a config error in prod."""
    import builtins
    from tools_v2_dispatch import (  # noqa: E402
        _query_neighbor_samples, _NeighborSamplesError,
    )

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pymilvus":
            raise ImportError("pymilvus blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(_NeighborSamplesError) as ei:
        await _query_neighbor_samples(
            collection="claims_history",
            mode="canonical",
            top_k=5,
            case_input=None,
            decision_filter=None,
            severity_filter=None,
            exclude_canonical=True,
        )
    assert ei.value.code in ("milvus_unavailable", "milvus_unconfigured")
