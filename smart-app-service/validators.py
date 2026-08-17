# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Spec validation — `models.py` (Pydantic) is the SINGLE SOURCE OF TRUTH.

There is no longer a hand-authored JSON-Schema validation layer. It duplicated
`models.py` (the recurring drift bug) and, being a `model_json_schema()`
projection, diverged from Pydantic on recursive types (it falsely rejected valid
`Condition`/auto-process specs). The roots now use ``extra="forbid"`` — legacy
root fields were purged pre-prod — so Pydantic alone enforces the full contract,
including unknown-key rejection at the root.

The JSON Schemas under `schemas/` are **GENERATED** from these models by
`gen_schemas.py` (run with `--check` in CI to fail on drift). They exist only as
a reference for the builder pod / external tooling — they are NOT a gate.

Server-stamped fields
---------------------
The AppSpec Pydantic model carries fields the server populates at publish
time (ownership audit trail, inheritance policy defaults, dataset directory
hydrated from discovery, etc.). The JSON Schema deliberately omits these
because they are NOT part of the builder-facing contract — the builder
must not author them, and external tooling must not see them as inputs.

When ``validate_app_spec`` is called with a dict produced by
``AppSpec.model_dump(mode="json")``, those server-only keys appear (with
default values from the Pydantic model) and trigger JSON Schema's
``additionalProperties: false`` rejection. To keep the contract narrow
without forcing every caller to strip-then-validate, we strip the
server-only keys here just before JSON Schema validation. The Pydantic
model still validates them strictly via ``AppSpec.model_validate``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

from models import AgentSpec, AppSpec

_SCHEMA_DIR = Path(__file__).parent / "schemas"


# Keys present in AppSpec Pydantic but NOT in app_spec.schema.json. The
# server populates these at publish time; the builder must not author
# them. We strip on the JSON-Schema-validation path so a re-validate of a
# server-emitted spec doesn't fail. Pydantic still validates them via
# AppSpec.model_validate (called after the JSON Schema check).
_APP_SPEC_SERVER_ONLY_KEYS = frozenset({
    # Author audit (Layer 1) — server stamps from JWT at first publish
    "author_user_id", "author_email", "author_at",
    # Ownership (Layer 2) — server / admin manages
    "owner_type", "owner_id", "owner_changed_at", "owner_changed_by",
    "previous_owners",
    # Scope — server derives from JWT / SA membership
    "org_id", "dept_ids",
    # Inheritance policy — server defaults (admin tunes via separate API)
    "inheritance_policy", "inheritance_target", "inheritance_grace_days",
    # Discovery hydration — server fills from dept-MCP catalogue at publish
    "dataset_directory",
})


@lru_cache(maxsize=2)
def _load_schema(name: str) -> Dict[str, Any]:
    path = _SCHEMA_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=2)
def _validator(name: str) -> Draft202012Validator:
    schema = _load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _strip_server_only(payload: Dict[str, Any], keys: frozenset) -> Dict[str, Any]:
    """Return a shallow copy of ``payload`` with ``keys`` removed.

    Shallow copy is enough — the keys we strip are scalars / lists at the
    top level. Nested fields (panels[], pages[], data_sources[]) are left
    untouched so any per-panel/per-page schema constraints still apply.
    """
    return {k: v for k, v in payload.items() if k not in keys}


def validate_app_spec(payload: Dict[str, Any]) -> Tuple[AppSpec, None]:
    """Validate the AppSpec. **Pydantic (`models.py`) is the SOLE validator** —
    the single source of truth. The roots now use ``extra="forbid"`` (legacy
    fields were purged pre-prod), so Pydantic rejects unknown top-level keys
    just like every nested model. The hand-authored JSON Schema layer was
    removed: it duplicated `models.py` (drift risk) AND, being a
    `model_json_schema()` projection, was *stricter* than Pydantic on the
    recursive `Condition` (it falsely rejected valid auto-process policies).
    The JSON Schemas under `schemas/` are now GENERATED from these models
    (`gen_schemas.py`) for builder/tooling reference only — never a gate.
    """
    return AppSpec.model_validate(payload), None


def validate_agent_spec(payload: Dict[str, Any]) -> Tuple[AgentSpec, None]:
    """Validate the AgentSpec. Pydantic is the sole validator (see
    ``validate_app_spec``)."""
    return AgentSpec.model_validate(payload), None


__all__ = [
    "validate_app_spec",
    "validate_agent_spec",
    "JsonSchemaValidationError",
    "PydanticValidationError",
]

