# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""AuthZ matrix — fail-closed access to a published app across roles.

The published golden-base app has an audience; assert that:
  * an org-scoped admin of the SAME org can read it,
  * a member/anonymous caller does not get privileged (edit/admin) surfaces,
  * a caller from a DIFFERENT org cannot read or mutate it.

We keep assertions conservative (read allowed vs. denied; mutation denied for
non-owners) rather than pinning exact codes, since audience config varies.
"""
from __future__ import annotations

import httpx
import pytest

from conftest import CFG, auth


def _get_app(sas, token):
    return sas.get(f"/apps/{CFG.APP_SLUG}", headers=auth(token))


def test_same_org_admin_can_read(sas: httpx.Client, base_specs, tok_super: str):
    assert _get_app(sas, tok_super).status_code == 200


def test_other_org_cannot_read(sas: httpx.Client, base_specs, tok_other_org: str):
    # Cross-tenant read must be denied (404 fail-closed or 403).
    r = _get_app(sas, tok_other_org)
    assert r.status_code in (403, 404), r.text


def test_anonymous_cannot_read(sas: httpx.Client, base_specs):
    r = sas.get(f"/apps/{CFG.APP_SLUG}")
    assert r.status_code in (401, 403, 404), r.text


@pytest.mark.parametrize("method,path", [
    ("delete", "/apps/{slug}"),
    ("post", "/apps/{slug}/transfer"),
    ("post", "/apps/{slug}/promote-to-prod"),
    ("post", "/apps/{slug}/audience"),
])
def test_member_cannot_mutate_lifecycle(sas: httpx.Client, base_specs, tok_member: str,
                                        method: str, path: str):
    url = path.format(slug=CFG.APP_SLUG)
    r = sas.request(method, url, headers=auth(tok_member), json={})
    # A non-owner member must be denied lifecycle mutations. Accept the fail-closed
    # family (401/403/404) plus 422 (rejected before authz on a bad body) — but
    # NOT 2xx.
    assert r.status_code >= 400, f"member unexpectedly allowed {method} {url}: {r.status_code}"


def test_other_org_cannot_mutate(sas: httpx.Client, base_specs, tok_other_org: str):
    r = sas.request("delete", f"/apps/{CFG.APP_SLUG}", headers=auth(tok_other_org))
    assert r.status_code in (403, 404), r.text
