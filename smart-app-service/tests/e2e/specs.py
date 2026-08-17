# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Spec mutators for publish-validator negative tests.

Each mutator takes the live golden-base ``{app_spec, agent_spec}`` (guaranteed
schema-valid because it's a real published app) and injects ONE policy violation,
returning ``(payload, expected_rule)`` where ``payload`` is the
``/builder/validate`` body ``{app_spec, agent_spec}``.

We reuse REAL panels/tools from the base where possible so the mutation stays
schema-valid and isolates the target *rule* (not a schema error). A mutator
returns ``None`` when the base lacks the structure it needs — the test then
skips rather than false-fails.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Tuple

Payload = Dict[str, Any]
Result = Optional[Tuple[Payload, str]]


def _clone(base: Dict[str, Any]) -> Dict[str, Any]:
    return {"app_spec": copy.deepcopy(base["app_spec"]),
            "agent_spec": copy.deepcopy(base.get("agent_spec"))}


def _iter_panels(app_spec: Dict[str, Any]):
    for pg in (app_spec.get("pages") or []):
        for pn in (pg.get("panels") or []):
            yield pg, pn


# ── F-01 — Citra-stored media column (format:"file") ────────────────────────
def mut_f01_media_column(base: Dict[str, Any]) -> Result:
    b = _clone(base)
    # Inject a file field into the first panel that already has a schema_inline
    # (reusing a valid form panel keeps the spec schema-valid).
    for _pg, pn in _iter_panels(b["app_spec"]):
        schema = pn.get("schema_inline")
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            schema["properties"]["e2e_evidence_file"] = {
                "type": "string", "format": "file", "title": "Evidence",
            }
            return b, "F-01"
    return None  # no form panel to mutate → skip


# ── S-01 — non-internal audience ────────────────────────────────────────────
def mut_s01_external_audience(base: Dict[str, Any]) -> Result:
    b = _clone(base)
    b["app_spec"]["audience"] = "public"   # apps are internal-only
    return b, "S-01"


# ── update_identifier — mutating verb with no row key ───────────────────────
def mut_update_no_identifier(base: Dict[str, Any]) -> Result:
    b = _clone(base)
    agent = b.get("agent_spec")
    if not agent:
        return None
    tools = agent.get("tools_v2") or agent.get("tools") or []
    for t in tools:
        if isinstance(t, dict) and t.get("kind") == "mcp_action":
            # Point it at an update verb and strip any identifier from required.
            aid = (t.get("action_id") or "acme.update_row")
            base_id = aid.rsplit(".", 1)[0] if "." in aid else "acme"
            t["action_id"] = f"{base_id}.update"
            sch = t.setdefault("input_schema", {"type": "object", "properties": {}})
            sch["required"] = [k for k in (sch.get("required") or [])
                               if not (k in ("id", "_id", "pk", "key") or k.endswith("_id"))]
            return b, "update_identifier"
    return None


# ── mcp_action_input_schema — write action missing its input_schema ─────────
def mut_mcp_action_no_input_schema(base: Dict[str, Any]) -> Result:
    b = _clone(base)
    agent = b.get("agent_spec")
    if not agent:
        return None
    tools = agent.get("tools_v2") or agent.get("tools") or []
    for t in tools:
        if isinstance(t, dict) and t.get("kind") == "mcp_action":
            t.pop("input_schema", None)
            return b, "mcp_action_input_schema"
    return None


# ── H-04 — writes allowed in chat ───────────────────────────────────────────
def mut_h04_writes_in_chat(base: Dict[str, Any]) -> Result:
    b = _clone(base)
    agent = b.get("agent_spec")
    if not agent:
        return None
    agent["allow_writes_in_chat"] = True
    return b, "H-04"


# Registry: (id, mutator). Each yields (payload, expected_rule) or None→skip.
# The app_spec-only mutators (F-01, S-01) run without an agent_spec; the rest
# need the base app to ship an AgentSpec with an mcp_action.
MUTATORS = [
    ("F-01-media-column", mut_f01_media_column),
    ("S-01-external-audience", mut_s01_external_audience),
    ("update_identifier", mut_update_no_identifier),
    ("mcp_action_input_schema", mut_mcp_action_no_input_schema),
    ("H-04-writes-in-chat", mut_h04_writes_in_chat),
]
