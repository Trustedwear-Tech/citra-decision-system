# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Test JWT minting + auth-header helpers.

Mints JWTs that the gateway-style auth in Citra-Service / smart-app-service
accepts. Also produces the canonical ``X-User-Id`` / ``X-Org-Id`` /
``X-Dept-Ids`` / ``X-Roles`` / ``X-Sa-Admin-Of`` header set gateway-trust
services consume.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

import jwt as _jwt


# ── Test identities ─────────────────────────────────────────────────────
# These are the personas every scenario can assume exist (seeded by
# helpers.seed.seed_users). Each scenario can also mint custom users.

ACME_BA = {
    "user_id": "u_rohit_acme",
    "org_id": "acme-insurance-test",
    "dept_ids": ["claims"],
    "roles": ["ba"],
    "email": "rohit@acme.test",
    "name": "Rohit (Acme BA)",
    "personal_sa_id": "svc:personal-u-rohit-acme@acme-insurance-test.citra.ai",
    # Tests bypass the build-pod hop and POST specs straight to /publish
    # acting as the persona. The smart-app-service /publish endpoint
    # (auth.py::require_publish_scope) only accepts JWTs carrying this
    # claim; in prod the builder pod's CITRA_JWT supplies it.
    "scope": "smart-app-builder",
}

ACME_OPS_ADMIN = {
    "user_id": "u_alex_acme",
    "org_id": "acme-insurance-test",
    "dept_ids": ["claims", "underwriting"],
    "roles": ["ba", "dept_admin", "org_admin"],
    "email": "alex@acme.test",
    "name": "Alex (Acme Ops Admin)",
    "personal_sa_id": "svc:personal-u-alex-acme@acme-insurance-test.citra.ai",
    "scope": "smart-app-builder",
}

BRAVO_BA = {
    "user_id": "u_claire_bravo",
    "org_id": "bravo-bank-test",
    "dept_ids": ["lending"],
    "roles": ["ba"],
    "email": "claire@bravo.test",
    "name": "Claire (Bravo BA)",
    "personal_sa_id": "svc:personal-u-claire-bravo@bravo-bank-test.citra.ai",
    "scope": "smart-app-builder",
}

BUILDER_SERVICE = {
    "user_id": "service:citra-app-builder",
    "org_id": "_service_",
    "dept_ids": [],
    "roles": ["service:builder"],
    "scope": "smart-app-builder",
}


# ── Minting ─────────────────────────────────────────────────────────────


def mint_jwt(user: Dict, *, ttl_seconds: int = 3600) -> str:
    """Sign a test JWT with the persona's claims."""
    secret = os.getenv("JWT_SECRET", "test-only-not-for-prod")
    now = int(time.time())
    payload = {
        "sub": user["user_id"],
        "user_id": user["user_id"],
        "org_id": user["org_id"],
        # Some downstream services still read ``tenant_id`` as a back-compat
        # alias for org_id (e.g. auth.py at smart-app-service). Set both.
        "tenant_id": user["org_id"],
        "dept_ids": user.get("dept_ids", []),
        "roles": user.get("roles", []),
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "personal_sa_id": user.get("personal_sa_id"),
        "service_account_admin_of": user.get("service_account_admin_of", []),
        "service_account_member_of": user.get("service_account_member_of", []),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if user.get("scope"):
        payload["scope"] = user["scope"]
    return _jwt.encode(payload, secret, algorithm="HS256")


def auth_headers(user: Dict) -> Dict[str, str]:
    """Combined headers for either JWT-mode or headers-mode services.

    Includes both the Bearer JWT (for JWT validators) and the canonical
    X-headers (for gateway-trust services).
    """
    jwt_token = mint_jwt(user)
    return {
        "Authorization": f"Bearer {jwt_token}",
        "X-User-Id": user["user_id"],
        "X-Org-Id": user["org_id"],
        "X-Dept-Ids": ",".join(user.get("dept_ids", [])),
        "X-Roles": ",".join(user.get("roles", [])),
        "X-Sa-Admin-Of": ",".join(user.get("service_account_admin_of", [])),
    }


def headers_only(user: Dict) -> Dict[str, str]:
    """X-headers without the JWT (for gateway-trust unit-style calls)."""
    return {
        "X-User-Id": user["user_id"],
        "X-Org-Id": user["org_id"],
        "X-Dept-Ids": ",".join(user.get("dept_ids", [])),
        "X-Roles": ",".join(user.get("roles", [])),
        "X-Sa-Admin-Of": ",".join(user.get("service_account_admin_of", [])),
    }
