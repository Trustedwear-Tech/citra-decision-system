# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Schema ↔ model drift guard.

It asserts that the set of UI discriminator values the Pydantic models accept
equals the set the JSON schema accepts, for the constructs the builder emits:
panel types and chart_type.

WHY THIS STILL EXISTS, AND WHAT IT IS *NOT*
-------------------------------------------
This file used to open by calling ``app_spec.schema.json`` hand-authored. That
is no longer true: the schema is GENERATED from ``models.py`` by
``gen_schemas.py``, and ``test_schema_drift.py`` asserts the committed file
matches that generator byte for byte. So genuine model↔schema divergence is
already impossible, and this test is a *second* opinion rather than the primary
guard.

It earns its place on the failure message. ``test_schema_drift`` can only say
"the committed file differs from the generator" — true but unhelpful when you
are staring at a 119 KB JSON blob. This one names the actual delta ("in models
only: ['embed']"), which is what you need to see.

The cost of that is coupling to the generator's OUTPUT SHAPE, and it has bitten
once: the tests were written against the old hand-authored layout
(``$defs.Panel.oneOf`` and per-branch ``allOf``) and started raising
``KeyError: 'Panel'`` once Pydantic generated the file — a broken test reporting
a drift that did not exist. Hence the assertions below refuse to silently find
nothing: an empty result means the traversal has gone stale, not that the model
is empty, and it must fail as loudly as a real drift.
"""

from __future__ import annotations

import json
import typing
from pathlib import Path

import models

_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "schemas" / "app_spec.schema.json").read_text(
        encoding="utf-8"
    )
)


def _literal_values(annotation) -> set[str]:
    """Pull the string values out of a typing.Literal[...] annotation."""
    return {v for v in typing.get_args(annotation) if isinstance(v, str)}


def _model_panel_types() -> set[str]:
    """Every ``type`` Literal across the Panel discriminated union."""
    union = typing.get_args(models.Panel)[0]  # Annotated[Union[...], Field(...)]
    members = typing.get_args(union)
    out: set[str] = set()
    for m in members:
        out |= _literal_values(m.model_fields["type"].annotation)
    return out


def _schema_panel_types() -> set[str]:
    """Every ``type`` const across the *Panel $defs in the JSON schema.

    Pydantic emits one $def per union member (``QueuePanel``, ``ChartPanel``, …),
    each pinning its discriminator as ``properties.type.const``. There is no
    ``Panel`` wrapper def — reading one is what broke this test before.
    """
    out: set[str] = set()
    for name, body in _SCHEMA["$defs"].items():
        if not name.endswith("Panel"):
            continue
        const = (body.get("properties", {}).get("type", {}) or {}).get("const")
        if const:
            out.add(const)
    return out


def test_panel_types_in_sync() -> None:
    model_types = _model_panel_types()
    schema_types = _schema_panel_types()
    # An empty side means the traversal above no longer matches the generator's
    # output — a stale test, not a clean spec. Say so instead of passing.
    assert model_types, "found no panel types in models.py — traversal is stale"
    assert schema_types, (
        "found no panel types in app_spec.schema.json — the generated layout "
        "changed and _schema_panel_types() needs updating"
    )
    assert model_types == schema_types, (
        "Panel type drift between models.py and app_spec.schema.json:\n"
        f"  in models only:  {sorted(model_types - schema_types)}\n"
        f"  in schema only:  {sorted(schema_types - model_types)}"
    )


def test_chart_types_in_sync() -> None:
    model_chart_types = _literal_values(
        models.ChartPanel.model_fields["chart_type"].annotation
    )
    # Pydantic emits chart_type as a flat enum on ChartPanel — no allOf branches.
    chart_def = _SCHEMA["$defs"]["ChartPanel"]
    schema_chart_types = set(
        (chart_def.get("properties", {}).get("chart_type", {}) or {}).get("enum") or []
    )
    assert model_chart_types, "found no chart types in models.py — traversal is stale"
    assert schema_chart_types, (
        "found no chart_type enum in app_spec.schema.json — the generated "
        "layout changed and this traversal needs updating"
    )
    assert model_chart_types == schema_chart_types, (
        "chart_type drift between models.py and app_spec.schema.json:\n"
        f"  in models only:  {sorted(model_chart_types - schema_chart_types)}\n"
        f"  in schema only:  {sorted(schema_chart_types - model_chart_types)}"
    )


def test_detail_section_types_in_sync() -> None:
    model_types = _literal_values(models.DetailSection.model_fields["type"].annotation)
    schema_types = set(_SCHEMA["$defs"]["DetailSection"]["properties"]["type"]["enum"])
    assert model_types == schema_types, (
        "DetailSection type drift between models.py and app_spec.schema.json:\n"
        f"  in models only:  {sorted(model_types - schema_types)}\n"
        f"  in schema only:  {sorted(schema_types - model_types)}"
    )
