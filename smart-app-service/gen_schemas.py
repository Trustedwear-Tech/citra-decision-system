# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Generate the builder-facing JSON Schemas FROM the Pydantic models.

`models.py` is the single source of truth for the SmartApp spec SHAPE. The
JSON Schemas under `schemas/` are **generated** from it — never hand-edited —
so they can never drift from the contract the runtime actually enforces.

Why a generator and not just `model_json_schema()` verbatim: the runtime
models are deliberately LENIENT at the ROOT (`AppSpec`/`AgentSpec` accept extra
top-level keys) so that *legacy stored apps* — which carry retired root fields
like `superset` / `visibility` / `workflow_bindings` — still load. The
builder-facing schema must be STRICTER than that: a *new* spec must not invent
root fields. So this generator takes the Pydantic shape and applies the
publish-time strictness the runtime can't:

  1. root `additionalProperties: false`  (reject unknown top-level keys at publish)
  2. `spec_version` in root `required`     (it has a default in the model)
  3. strip the SERVER-STAMPED keys          (the builder must not author them)

Nested objects already emit `additionalProperties:false` (they use
`extra="forbid"`), so only the root needs it added.

Run:  python gen_schemas.py        (writes schemas/app_spec.schema.json + agent_spec.schema.json)
      python gen_schemas.py --check (exit 1 if the on-disk schemas differ from freshly generated)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from models import AppSpec, AgentSpec

_SCHEMA_DIR = Path(__file__).parent / "schemas"

# Server-stamped keys the builder must NOT author — stripped from the
# builder-facing schema. Mirrors validators._APP_SPEC_SERVER_ONLY_KEYS.
_APP_SERVER_ONLY = {
    "author_user_id", "author_email", "author_at",
    "owner_type", "owner_id", "owner_changed_at", "owner_changed_by",
    "previous_owners", "org_id", "dept_ids",
    "inheritance_policy", "inheritance_target", "inheritance_grace_days",
    "dataset_directory",
}


def _harden(schema: dict, *, strip: set[str] = frozenset()) -> dict:
    """Apply builder-facing strictness to a model_json_schema() root object."""
    props = schema.get("properties") or {}
    for k in strip:
        props.pop(k, None)
    schema["properties"] = props
    # 1. root rejects unknown keys (the runtime root is lenient for legacy)
    schema["additionalProperties"] = False
    # 2. spec_version required (model defaults it)
    req = [r for r in (schema.get("required") or []) if r not in strip]
    if "spec_version" in props and "spec_version" not in req:
        req.append("spec_version")
    schema["required"] = sorted(req)
    return schema


def generate() -> dict[str, dict]:
    app = _harden(AppSpec.model_json_schema(), strip=_APP_SERVER_ONLY)
    agent = _harden(AgentSpec.model_json_schema())
    return {"app_spec.schema.json": app, "agent_spec.schema.json": agent}


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    gen = generate()
    drift = False
    for name, schema in gen.items():
        path = _SCHEMA_DIR / name
        new = _dump(schema)
        if check:
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            if old != new:
                drift = True
                print(f"DRIFT: {name} on disk differs from generated — run `python gen_schemas.py`")
        else:
            path.write_text(new, encoding="utf-8")
            print(f"wrote {name} ({len(new)} bytes)")
    if check and drift:
        return 1
    if check:
        print("schemas in sync with models.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
