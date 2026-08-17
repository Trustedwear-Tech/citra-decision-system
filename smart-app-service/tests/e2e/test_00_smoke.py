# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Smoke + contract: service reachable, auth-gated, basic shapes."""
from __future__ import annotations

import httpx

from conftest import CFG, auth, mint_jwt


def test_health(sas: httpx.Client, stack_up: bool):
    r = sas.get("/health")
    assert r.status_code == 200


def test_apps_requires_auth(sas: httpx.Client, stack_up: bool):
    # No token → must NOT return a populated list. Fail-closed: 401/403, or an
    # empty audience-filtered 200 — never a 500 and never other users' apps.
    r = sas.get("/apps")
    assert r.status_code in (200, 401, 403), r.text
    if r.status_code == 200:
        assert r.json().get("total", 0) == 0, "unauthenticated caller saw apps"


def test_apps_list_with_auth(sas: httpx.Client, stack_up: bool, tok_super: str):
    r = sas.get("/apps", headers=auth(tok_super))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "apps" in body and "total" in body
    assert isinstance(body["apps"], list)


def test_unknown_app_404(sas: httpx.Client, stack_up: bool, tok_super: str):
    r = sas.get("/apps/this-slug-does-not-exist-e2e", headers=auth(tok_super))
    assert r.status_code == 404, r.text


def test_expired_token_rejected(sas: httpx.Client, stack_up: bool):
    expired = mint_jwt(roles=["super_admin"], ttl_seconds=-10)
    r = sas.get("/apps", headers=auth(expired))
    # An expired JWT must not authenticate → not a populated list.
    assert r.status_code in (401, 403) or (r.status_code == 200 and r.json().get("total", 0) == 0)
