# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Smart App build → publish persistence test.

Skips the multi-phase builder pod (which would need K8s + the builder
image + SSE plumbing) and POSTs a hand-crafted AppSpec/AgentSpec
straight to /publish — the same path the pod takes after authoring
the spec. This exercises:

  * Pydantic validation of the spec
  * AppSpec/AgentSpec persistence (apps + agents collections)
  * Slug uniqueness within a tenant + version bump on republish
  * Cross-tenant slug squat rejection (409)
  * `requirements_unmet` round-trip (BA visibility into platform gaps)

Requires:
  • smart-app-service running with MONGODB_DATABASE=citra_integration_test
  • Mongo at the standard test port

The fake-mcp-server doesn't need to be reachable for this test —
no tools_v2 mcp/rag entries, so validate_tool_sources_resolvable is
a no-op.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpers.builder_session import publish_spec, get_app  # noqa: E402
from helpers.assert_helpers import (  # noqa: E402
    assert_app_persisted, assert_agent_persisted,
)
from helpers.specs import make_agent_spec, make_app_spec  # noqa: E402


pytestmark = pytest.mark.asyncio


SLUG = "itest-claims-triage-01"


async def test_publish_persists_app_and_agent(
    smart_app_service_url, acme_admin_headers, mongo_db,
):
    agent = make_agent_spec(name="Triage Agent")
    app = make_app_spec(slug=SLUG, agent_id=agent["agent_id"])

    resp = await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=acme_admin_headers,
    )
    body = resp.json()
    assert body["slug"] == SLUG
    assert body["version"] == 1
    assert body.get("app_id"), "publisher must assign an app_id"

    await assert_app_persisted(
        mongo_db, slug=SLUG, expected_tenant="acme-insurance-test",
    )
    await assert_agent_persisted(mongo_db, agent_id=agent["agent_id"])


async def test_republish_bumps_version(
    smart_app_service_url, acme_admin_headers, mongo_db,
):
    """Publishing the same slug again should bump version and keep app_id."""
    agent = make_agent_spec(name="Triage v2")
    app = make_app_spec(slug=SLUG, agent_id=agent["agent_id"], title="Triage v2")

    resp = await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=acme_admin_headers,
    )
    body = resp.json()
    assert body["slug"] == SLUG
    assert body["version"] >= 2, f"expected version bump, got {body['version']}"

    doc = await assert_app_persisted(mongo_db, slug=SLUG, min_version=2)
    assert (doc.get("app_spec") or {}).get("title") == "Triage v2"


async def test_cross_tenant_slug_squat_rejected(
    smart_app_service_url, bravo_ba_headers,
):
    """Bravo BA cannot republish ACME's slug."""
    agent = make_agent_spec(name="Bravo Squatter")
    app = make_app_spec(
        slug=SLUG,
        agent_id=agent["agent_id"],
        tenant_id="bravo-bank-test",
    )

    resp = await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=bravo_ba_headers,
        expect_status=409,
    )
    assert "another tenant" in resp.text.lower()


async def test_requirements_unmet_round_trip(
    smart_app_service_url, acme_admin_headers,
):
    """A BA's unmet-capability list must survive publish for admin visibility."""
    slug = "itest-unmet-caps"
    agent = make_agent_spec(name="Unmet Caps Agent")
    app = make_app_spec(slug=slug, agent_id=agent["agent_id"])
    app["requirements_unmet"] = [
        "voice_ivr_trigger_not_supported",
        "sftp_outbound_unavailable",
    ]

    await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=acme_admin_headers,
    )

    resp = await get_app(smart_app_service_url, slug=slug, headers=acme_admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    saved = (body.get("app_spec") or {}).get("requirements_unmet") or []
    assert "voice_ivr_trigger_not_supported" in saved
    assert "sftp_outbound_unavailable" in saved
