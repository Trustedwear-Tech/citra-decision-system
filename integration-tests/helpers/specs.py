# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Hand-crafted AppSpec / AgentSpec / WorkflowSpec builders for tests.

Building these by hand (rather than going through a full builder pod
session) lets the integration suite exercise the publish/run/narrate
surfaces without needing the multi-agent build pipeline running.

Every builder returns *plain dicts* — the smart-app-service POST /publish
endpoint validates them against its own Pydantic models, so we keep
this module dependency-free of smart-app-service imports. That means
tests can run from the integration-tests checkout alone.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional


def make_agent_spec(
    *,
    agent_id: Optional[str] = None,
    name: str = "Claims Triage Agent",
    system_prompt: str = "You triage motor insurance claims. Decide APPROVE or ESCALATE.",
    tools_v2: Optional[List[Dict[str, Any]]] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    model_tier: str = "tier_b",
) -> Dict[str, Any]:
    """A minimal AgentSpec dict that smart-app-service will accept."""
    return {
        "spec_version": "v0",
        "agent_id": agent_id or f"agent_{uuid.uuid4().hex[:10]}",
        "name": name,
        "model_tier": model_tier,
        "system_prompt": system_prompt,
        "tools_v2": tools_v2 or [],
        "actions": actions or [_default_triage_action()],
    }


def _default_triage_action() -> Dict[str, Any]:
    return {
        "name": "triage_claim",
        "description": "Decide APPROVE or ESCALATE for a single claim row.",
        "input_schema": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"},
                "claim_amount": {"type": "number"},
            },
            "required": ["claim_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["APPROVE", "ESCALATE"]},
                "amount_paid": {"type": "number"},
            },
            "required": ["decision"],
        },
    }


def make_app_spec(
    *,
    slug: str,
    title: str = "Test App",
    tenant_id: str = "acme-insurance-test",
    agent_id: Optional[str] = None,
    panels: Optional[List[Dict[str, Any]]] = None,
    kind: str = "app",
) -> Dict[str, Any]:
    return {
        "spec_version": "v0",
        "slug": slug,
        "title": title,
        "tenant_id": tenant_id,
        "kind": kind,
        "agent_id": agent_id,
        "panels": panels or [_default_form_panel()],
    }


def _default_form_panel() -> Dict[str, Any]:
    return {
        "type": "form",
        "id": "claim_form",
        "title": "New Claim",
        "schema_inline": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string", "title": "Claim ID"},
                "claim_amount": {"type": "number", "title": "Amount"},
            },
            "required": ["claim_id"],
        },
        "on_submit": {"agent_action": "triage_claim"},
    }


# ── Tool-v2 builders ────────────────────────────────────────────────────


def neighbor_samples_tool(
    *,
    name: str = "neighbor_samples",
    collection: str = "claims_history",
    mode: str = "neighbors",
    top_k: int = 3,
    decision: Optional[str] = None,
    severity: Optional[str] = None,
    exclude_canonical: bool = True,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "kind": "neighbor_samples",
        "name": name,
        "collection": collection,
        "mode": mode,
        "top_k": top_k,
        "exclude_canonical": exclude_canonical,
    }
    if decision is not None:
        out["decision"] = decision
    if severity is not None:
        out["severity"] = severity
    return out


def mcp_tool(
    *, name: str, source_id: str, tool_name: str = "list_motor_claims",
    **_unused,
) -> Dict[str, Any]:
    """McpTool entry — fields match smart-app-service models.py:McpTool exactly."""
    return {
        "kind": "mcp",
        "name": name,
        "source_id": source_id,
        "tool_name": tool_name,
    }


# ── Narrator (dashboard) helpers ────────────────────────────────────────


def make_narrator_agent_spec(
    *, agent_id: str = "claims-bi-narrator"
) -> Dict[str, Any]:
    """Minimal narrator agent for dashboard tests.

    The fake-llm pattern table keys off literal substrings of the system
    prompt + user message. We seed identifying tokens here so the
    deterministic mock can branch on them.
    """
    sys_prompt = (
        "You are the dashboard narrator for ACME claims. "
        "Produce one of: brief / why / nl_filter / anomaly responses."
    )
    actions = [
        {
            "name": "narrate_brief",
            "description": "1–2 sentence dashboard brief.",
            "input_schema": {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
            },
        },
        {
            "name": "narrate_why",
            "description": "Explain a metric movement.",
            "input_schema": {
                "type": "object",
                "properties": {"metric": {"type": "string"}},
            },
        },
        {
            "name": "narrate_nl_filter",
            "description": "Translate NL into a panel filter.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
        {
            "name": "narrate_anomaly",
            "description": "Flag anomalies in a series.",
            "input_schema": {
                "type": "object",
                "properties": {"series": {"type": "string"}},
            },
        },
    ]
    return make_agent_spec(
        agent_id=agent_id,
        name="ACME Claims Narrator",
        system_prompt=sys_prompt,
        actions=actions,
    )
