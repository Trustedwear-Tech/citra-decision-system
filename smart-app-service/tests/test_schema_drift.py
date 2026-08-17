# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Drift guard: the committed JSON Schemas MUST match what `gen_schemas.py`
produces from `models.py`.

`models.py` is the single source of truth for the SmartApp spec shape; the
schemas under `schemas/` are GENERATED from it (never hand-edited) and are
baked into the builder image + seeded into every builder pod as the
builder-facing contract. If someone edits `models.py` without re-running
`python gen_schemas.py` and committing the result, the baked-in schema goes
stale and the builder validates drafts against an out-of-date contract.

This test fails loudly on that drift. Fix = `python gen_schemas.py` + commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gen_schemas import _SCHEMA_DIR, _dump, generate  # noqa: E402


@pytest.mark.parametrize("name", ["app_spec.schema.json", "agent_spec.schema.json"])
def test_committed_schema_matches_models(name: str) -> None:
    generated = generate()
    assert name in generated, f"gen_schemas no longer produces {name}"

    path = _SCHEMA_DIR / name
    assert path.exists(), (
        f"{name} is missing from schemas/ — run `python gen_schemas.py` and commit it."
    )

    on_disk = path.read_text(encoding="utf-8")
    expected = _dump(generated[name])
    assert on_disk == expected, (
        f"{name} has DRIFTED from models.py. The committed schema no longer "
        f"matches what gen_schemas.py produces from the Pydantic models. "
        f"Fix: run `python gen_schemas.py` from smart-app-service/ and commit "
        f"the updated schemas/{name}."
    )
