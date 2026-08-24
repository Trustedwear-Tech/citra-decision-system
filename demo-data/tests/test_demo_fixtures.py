# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Validate every demo fixture JSON against the real Pydantic models
from smart-app-service and source-mcp-template.

If a fixture fails here, smart-app-service POST /publish would reject
it too — so this is the cheap pre-flight before running the full stack.

What's validated:

  1. tenant/acme_cement_demo_users.json
     - source persona fixture: 4 user records, the input the new
       seed_demo_users.py admin-API path consumes (no bcrypt hashes
       anymore — personas are placeholders accessed via impersonation)

  2. mcp-config/cement_dept_sources.import.json
     - 6 source docs; required fields per dept_source_template.yaml

  3. apps/*.json
     - 7 PublishRequest docs (smart-app-service model)
     - Each app's AppSpec.kind rules enforced
     - tools_v2 with mcp/rag must reference real source_ids
     - DashboardPanel needs metrics

  4. workflows/cement_ingestion_workflows.import.json
     - 6 WorkflowSpec docs (smart-app-service model)
     - Each has scheduled_trigger + workflow_state_get + workflow_state_set
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest


# Import the real Pydantic models from each service.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "smart-app-service"))


DEMO_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────────────────
# Reference data shared across tests
# ──────────────────────────────────────────────────────────────────────


EXPECTED_SOURCE_IDS = {
    "plant_ops_kiln_runs",
    "quality_test_results",
    "dispatch_orders",
    "central_sap_master",
    "central_salesforce_accounts",
    "central_historian_kpi_daily",
}

EXPECTED_TENANT = "acme-cement"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────────
# 1. Users fixture
# ──────────────────────────────────────────────────────────────────────


def test_users_fixture_well_formed():
    """4 persona records in the source fixture, each shaped for the
    Citra-User-Service admin-API path (POST /api/admin/orgs +
    POST /api/admin/users). bcrypt + passwordHash are no longer in the
    fixture — demo personas are placeholders that are accessed via
    impersonation, not direct password login."""
    fixture = _load(DEMO_ROOT / "tenant" / "acme_cement_demo_users.json")
    assert isinstance(fixture, dict)
    assert fixture["tenant"]["org_id"] == "acme-cement"
    assert fixture["tenant"]["is_demo"] is True

    docs = fixture["users"]
    assert isinstance(docs, list)
    assert len(docs) == 4

    roles_seen = set()
    depts_seen = set()
    emails_seen = set()
    for d in docs:
        # No passwordHash expected — local-password login retired.
        assert "passwordHash" not in d, \
            f"{d['email']}: passwordHash leaked into source fixture"

        # role values must be in Citra-User-Service enum
        for r in d["roles"]:
            assert r in {"user", "dept_admin", "org_admin", "super_admin"}, \
                f"unknown role {r!r}"
            roles_seen.add(r)

        # demo-scoping
        assert d["is_demo"] is True
        assert d["org_id"] == "acme-cement"
        assert d["entity_type"] == "company"

        for dept in d["dept_ids"]:
            assert dept in {"plant_ops", "quality", "sales_dispatch"}, dept
            depts_seen.add(dept)

        emails_seen.add(d["email"])

    # Cover all 3 depts + at least one org_admin
    assert depts_seen == {"plant_ops", "quality", "sales_dispatch"}
    assert "org_admin" in roles_seen
    assert "dept_admin" in roles_seen
    assert len(emails_seen) == 4   # no duplicates


# ──────────────────────────────────────────────────────────────────────
# 2. dept_sources fixture
# ──────────────────────────────────────────────────────────────────────


def test_dept_sources_fixture_covers_six_sources():
    docs = _load(DEMO_ROOT / "mcp-config" / "cement_dept_sources.import.json")
    assert isinstance(docs, list)
    assert len(docs) == 6
    ids = {d["source_id"] for d in docs}
    assert ids == EXPECTED_SOURCE_IDS

    for d in docs:
        assert d["org_id"] == EXPECTED_TENANT
        assert d["is_demo"] is True
        assert d["is_active"] is True
        assert d["type"] in {"mongodb", "structured", "semantic", "rest_api"}
        assert d["connection"]["mongo_db"] == "demo-source-manufacturing"
        # Visibility must be a dict (the source-mcp-template parses it)
        assert isinstance(d["visibility"], dict)
        for role in d["visibility"].get("roles_allowed", []):
            assert role in {"user", "dept_admin", "org_admin", "super_admin"}


# ──────────────────────────────────────────────────────────────────────
# 3. Apps + agents (validates against PublishRequest)
# ──────────────────────────────────────────────────────────────────────


def _app_fixtures() -> List[Path]:
    return sorted(p for p in (DEMO_ROOT / "apps").glob("*.json"))


@pytest.mark.parametrize("fixture_path", _app_fixtures(), ids=lambda p: p.name)
def test_app_fixture_validates_as_publish_request(fixture_path: Path):
    from sas_models import PublishRequest

    raw = _load(fixture_path)
    # Strip internal _doc helper key before model validation.
    payload = {k: v for k, v in raw.items() if not k.startswith("_")}
    parsed = PublishRequest.model_validate(payload)

    # Tenancy
    assert parsed.app_spec is not None
    assert parsed.app_spec.tenant_id == EXPECTED_TENANT

    # Tools_v2 mcp/rag → must reference one of our 6 source_ids
    if parsed.agent_spec is not None:
        for tool in parsed.agent_spec.tools_v2:
            kind = tool.kind
            if kind in {"mcp", "rag"}:
                src = getattr(tool, "source_id", None)
                assert src in EXPECTED_SOURCE_IDS, \
                    f"{fixture_path.name}: tool {tool.name!r} ({kind}) " \
                    f"points at unknown source_id={src!r}"


def test_dashboard_pages_require_agent_and_no_inline_chat():
    """A dashboard is a kind='app' with a page.kind='dashboard'.

    The dashboard page must be AI-narrated (the app declares agent_id for the
    hero-brief copilot) and must NOT carry an inline agent_chat panel — the
    hero brief covers chat there.
    """
    for path in _app_fixtures():
        raw = _load(path)
        spec = raw.get("app_spec", {})
        # The legacy 'dashboard' kind is retired.
        assert spec.get("kind") != "dashboard", \
            f"{path.name}: kind='dashboard' is retired — use kind='app' with " \
            "a page.kind='dashboard'"
        dash_pages = [
            p for p in spec.get("pages", [])
            if p.get("kind") == "dashboard"
        ]
        if dash_pages:
            assert spec.get("agent_id"), \
                f"{path.name}: a dashboard page requires app_spec.agent_id"
            for page in dash_pages:
                ptypes = [p.get("type") for p in page.get("panels", [])]
                assert "agent_chat" not in ptypes, \
                    f"{path.name}: dashboard page '{page.get('id')}' must not " \
                    "contain an agent_chat panel (the hero brief covers chat)"


def test_app_slugs_unique():
    seen = set()
    for path in _app_fixtures():
        raw = _load(path)
        slug = raw["app_spec"]["slug"]
        assert slug not in seen, f"duplicate slug {slug!r} in {path.name}"
        seen.add(slug)


# ──────────────────────────────────────────────────────────────────────
# 4. Workflow fixtures (validates against WorkflowSpec)
# ──────────────────────────────────────────────────────────────────────


def test_workflow_fixtures_validate_against_workflow_spec():
    from sas_models import WorkflowSpec
    docs = _load(DEMO_ROOT / "workflows" / "cement_ingestion_workflows.import.json")
    assert isinstance(docs, list)
    assert len(docs) == 6

    expected_names = {
        f"demo_ingest_{sid}" for sid in EXPECTED_SOURCE_IDS
    }
    actual_names = {d["name"] for d in docs}
    assert actual_names == expected_names, \
        f"missing/extra workflow names: {expected_names ^ actual_names}"

    for d in docs:
        parsed = WorkflowSpec.model_validate(d)
        assert parsed.schedule.enabled
        assert parsed.schedule.cron_expression is not None

        # Every ingestion workflow must have the watermark pattern.
        node_types = [n.type for n in parsed.nodes]
        assert "workflow_state_get" in node_types, \
            f"{parsed.name} missing workflow_state_get"
        assert "workflow_state_set" in node_types, \
            f"{parsed.name} missing workflow_state_set"

        # Verify the watermark Set comes AFTER the Milvus write — otherwise
        # a mid-run crash would advance the watermark without indexing.
        set_idx = node_types.index("workflow_state_set")
        write_idx = node_types.index("milvus_writer")
        assert set_idx > write_idx, \
            f"{parsed.name}: workflow_state_set must come after milvus_writer"


# ──────────────────────────────────────────────────────────────────────
# 5. Cross-fixture consistency
# ──────────────────────────────────────────────────────────────────────


def test_every_app_tool_v2_targets_known_source():
    """Already covered per-app — but assert at suite level no orphans exist."""
    seen_sources = set()
    for path in _app_fixtures():
        raw = _load(path)
        agent = raw.get("agent_spec") or {}
        for t in agent.get("tools_v2", []):
            sid = t.get("source_id")
            if sid:
                seen_sources.add(sid)
    orphans = seen_sources - EXPECTED_SOURCE_IDS
    assert not orphans, f"apps reference unknown source_ids: {orphans}"
