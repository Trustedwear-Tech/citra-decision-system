# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Ontology-driven default for policy-required data lookups.

A dept-source dataset marked ``mandatory_when_used: true`` in the catalogue (a
bureau / KYC / sanctions record) obliges every decision app that reads it to run
the check before staging a write. This deterministic pass — mirroring
``fraud_roles.autowire_fraud_roles`` — defaults the ``required`` flag on each mcp
READ tool bound to such a dataset. IT declares intent once in ``sources.json``;
every consuming app inherits it, and the read-before-write gate
(``evidence_guard.required_lookup_tools``) then enforces it.

The app can still OVERRIDE by explicitly authoring ``required`` on the tool
(true or false) — the autowire only fills a value the builder left unset.
Deterministic + idempotent; mutates ``agent_spec.tools_v2`` in place.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fraud_roles import _tool_attr, _tool_set

logger = logging.getLogger(__name__)


def _required_explicitly_authored(tool: Any) -> bool:
    """True if the builder EXPLICITLY set ``required`` on this tool, so the
    ontology default must not override it.

    Dict tools: key present. Pydantic tools: field in ``model_fields_set`` (which
    reflects exactly the keys the builder's JSON carried). An ambiguous/empty
    ``model_fields_set`` returns False, so the mandate applies — governance
    strengthens on doubt rather than silently dropping a required check.
    """
    if isinstance(tool, dict):
        return "required" in tool
    fset = getattr(tool, "model_fields_set", None)
    return bool(fset) and "required" in fset


def autowire_required_lookups(agent_spec: Any, catalogue: Any) -> int:
    """Default ``required=True`` on each bound mcp read tool whose dataset is
    marked ``mandatory_when_used`` in the catalogue. Returns the count changed.

    ``catalogue`` is the validator's ``{(source_id, dataset_id): entry}`` map.
    Only BOUND tools (``dataset_id`` set) are eligible — an unbound required tool
    is rejected by publish (W-09), so wiring one here would only block publish.
    """
    by_ref: Dict[str, Dict[str, Any]] = {}
    for (_src, ds), entry in (catalogue or {}).items():
        if entry:
            by_ref[ds] = entry

    changed = 0
    for tool in (getattr(agent_spec, "tools_v2", None) or []):
        if _tool_attr(tool, "kind") != "mcp":
            continue
        ds_id = _tool_attr(tool, "dataset_id")
        if not ds_id:
            continue  # unbound → not anchorable (W-09) → skip
        entry = by_ref.get(ds_id)
        if not entry or entry.get("mandatory_when_used") is not True:
            continue
        if _required_explicitly_authored(tool):
            continue  # app override wins
        if _tool_attr(tool, "required") is not True:
            _tool_set(tool, "required", True)
            changed += 1
            logger.info(
                "[required-autowire] dataset %r is mandatory_when_used — defaulted "
                "tool %r required=true", ds_id, _tool_attr(tool, "name"),
            )
    return changed
