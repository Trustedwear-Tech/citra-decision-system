# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Cross-tenant isolation — full Smart App pipeline.

This file covers the Smart App layer:

  1. ACME admin publishes app A
  2. Bravo BA cannot read app A via GET /apps/{slug} (404 / 403)
  3. Bravo BA cannot run app A via POST /apps/{slug}/run (403)
  4. Bravo cannot publish a same-slug clone (covered by test_01)
  5. ACME's app continues to work for ACME users after Bravo's probes

This validates the ``_user_can_access`` gate in smart-app-service that
checks tenant_id on the persisted document against the publisher /
caller's JWT.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpers.builder_session import publish_spec, run_app, get_app  # noqa: E402
from helpers.specs import make_agent_spec, make_app_spec  # noqa: E402


pytestmark = pytest.mark.asyncio


SLUG = "itest-acme-isolation"


@pytest_asyncio.fixture(autouse=True)
async def _publish_acme_app(smart_app_service_url, acme_admin_headers):
    agent = make_agent_spec(name="ACME Isolation Agent")
    app = make_app_spec(slug=SLUG, agent_id=agent["agent_id"])
    await publish_spec(
        smart_app_service_url,
        app_spec=app,
        agent_spec=agent,
        headers=acme_admin_headers,
    )
    yield


async def test_bravo_cannot_read_acme_app(
    smart_app_service_url, bravo_ba_headers,
):
    resp = await get_app(
        smart_app_service_url, slug=SLUG, headers=bravo_ba_headers,
    )
    assert resp.status_code in (403, 404), (
        f"Bravo must not see ACME's app; got {resp.status_code} {resp.text}"
    )


async def test_bravo_cannot_run_acme_app(
    smart_app_service_url, bravo_ba_headers,
):
    resp = await run_app(
        smart_app_service_url,
        slug=SLUG,
        action="triage_claim",
        inputs={"claim_id": "C001", "claim_amount": 800},
        headers=bravo_ba_headers,
        expect_status=None,
    )
    assert resp.status_code in (403, 404), (
        f"Bravo must not run ACME's app; got {resp.status_code} {resp.text}"
    )


async def test_acme_user_still_works(
    smart_app_service_url, acme_ba_headers,
):
    """Bravo's failed probes must not interfere with the legitimate flow."""
    resp = await get_app(
        smart_app_service_url, slug=SLUG, headers=acme_ba_headers,
    )
    assert resp.status_code == 200, (
        f"ACME user should still see the app; got {resp.status_code} {resp.text}"
    )
