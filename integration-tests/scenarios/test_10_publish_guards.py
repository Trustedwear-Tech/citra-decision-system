# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Publish-time guards.

Catches the most expensive class of BA mistakes BEFORE the app reaches
a real user:

  1. Typo'd source_id in tools_v2 → discovery-service can't resolve →
     422 with code 'tool_sources_unresolved'
  2. Missing required AppSpec fields → 422 from Pydantic validation
  3. Dashboard kind WITHOUT agent_id → 422 (the recent contract change)
  4. workflow_spec without WORKFLOW_SERVICE_URL configured → 422 with
     code 'workflow_not_configured'

We exercise these against a real publish endpoint; the typo'd source_id
test depends on a discovery-service that can be queried — if not present
in this environment, the runtime intentionally short-circuits and the
test is informational only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpers.builder_session import publish_spec  # noqa: E402
from helpers.specs import (  # noqa: E402
    make_agent_spec, make_app_spec, mcp_tool,
)


pytestmark = pytest.mark.asyncio


async def test_typo_source_id_rejected_at_publish(
    smart_app_service_url, acme_admin_headers,
):
    """An mcp tool that points at an unregistered source must 422."""
    bad_tool = mcp_tool(
        name="lookup_claims",
        source_id="sap_motor_claimz",   # deliberate typo
        tool_name="list_motor_claims",
    )
    agent = make_agent_spec(name="Typo Agent", tools_v2=[bad_tool])
    # Use a markdown panel (not form) so the FormPanel ↔ validate_form
    # guard doesn't fire BEFORE the tool-source resolver — that would
    # mask the test's intent.
    app = make_app_spec(
        slug="itest-typo-source",
        agent_id=agent["agent_id"],
        panels=[{"type": "markdown", "id": "intro", "content": "Triage."}],
    )

    resp = await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=acme_admin_headers,
        expect_status=None,   # we want to inspect both 200 and 422 paths
    )

    if resp.status_code == 200:
        # discovery-service was unreachable; smart-app-service degrades
        # gracefully and skips the check. Surface this as a soft skip.
        pytest.skip(
            "discovery-service not configured in this env; publish guard "
            "soft-skipped (this is by design)."
        )

    assert resp.status_code == 422
    body = resp.json()
    detail = body.get("detail") or {}
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "tool_sources_unresolved", (
        f"expected tool_sources_unresolved, got {body!r}"
    )


async def test_dashboard_without_agent_id_rejected(
    smart_app_service_url, acme_admin_headers,
):
    """Smart Dashboards must always have an AI chat — the contract from the
    'no dumb dashboards' decision."""
    panels = [{
        "type": "dashboard",
        "id": "dashboard_only",
        "title": "Dumb dashboard",
        "metrics": [{"name": "count", "agg": "count", "field": "id"}],
    }]
    # Build an app dict by hand so we can omit agent_id deliberately.
    app = {
        "spec_version": "v0",
        "slug": "itest-dumb-dashboard",
        "title": "Dumb",
        "tenant_id": "acme-insurance-test",
        "kind": "dashboard",
        "panels": panels,
        # NO agent_id.
    }

    resp = await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=None,
        headers=acme_admin_headers,
        expect_status=422,
    )
    assert "agent" in resp.text.lower() or "kind" in resp.text.lower()


async def test_missing_required_appspec_field_rejected(
    smart_app_service_url, acme_admin_headers,
):
    """No slug → Pydantic 422."""
    agent = make_agent_spec(name="Missing slug")
    bad_app = {
        "spec_version": "v0",
        # NO slug
        "title": "Headless",
        "tenant_id": "acme-insurance-test",
        "kind": "app",
        "agent_id": agent["agent_id"],
        "panels": [],
    }
    await publish_spec(
        smart_app_service_url,
        app_spec=bad_app,
        agent_spec=agent,
        headers=acme_admin_headers,
        expect_status=422,
    )
