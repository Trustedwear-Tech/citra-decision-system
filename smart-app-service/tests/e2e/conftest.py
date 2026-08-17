# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Shared fixtures for the Decision App E2E harness.

Everything is env-driven so the same suite runs against a LOCAL stack
(:9100/:3100) or the on-box TEST environment. Tests SKIP (never hard-fail) when
the stack is unreachable or `SAS_JWT_SECRET` is unset, so `pytest` is safe to run
anywhere.

Run:  see tests/e2e/README.md
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest

try:
    import jwt  # PyJWT
except Exception as exc:  # pragma: no cover
    jwt = None
    _JWT_IMPORT_ERR = exc


# ── Config ──────────────────────────────────────────────────────────────────
class Cfg:
    SAS = os.getenv("SAS_BASE_URL", "http://localhost:9100").rstrip("/")
    RUNTIME = os.getenv("RUNTIME_BASE_URL", "http://localhost:3100").rstrip("/")
    JWT_SECRET = os.getenv("SAS_JWT_SECRET")  # from Vault prod/smart-app-service
    JWT_ISSUER = os.getenv("SAS_JWT_ISSUER", "Citra-AI")
    ORG = os.getenv("DA_ORG_ID", "acme-power")
    # A published app used as the schema-valid "golden base" for mutation tests
    # and as the live target for media/runtime tests.
    APP_SLUG = os.getenv("DA_APP_SLUG", "equipment-inspection-fraud-screen")
    DS_ID = os.getenv("DA_DS_ID", "ds_inspections")
    RECORD_ID = os.getenv("DA_RECORD_ID", "INS-2026-0013")
    KEY_FIELD = os.getenv("DA_KEY_FIELD", "inspection_id")
    MEDIA_COL = os.getenv("DA_MEDIA_COL", "defect_photo_url")
    TIMEOUT = float(os.getenv("DA_HTTP_TIMEOUT", "30"))


CFG = Cfg()


# ── JWT minting ─────────────────────────────────────────────────────────────
def mint_jwt(
    *,
    roles: Optional[List[str]] = None,
    org_id: Optional[str] = None,
    user_id: str = "e2e@acme-power.citra.ai",
    sa_admin_of: Optional[List[str]] = None,
    dept_ids: Optional[List[str]] = None,
    ttl_seconds: int = 1800,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Mint an HS256 user JWT matching smart-app-service/auth.py's contract."""
    if jwt is None:  # pragma: no cover
        pytest.skip(f"PyJWT not importable: {_JWT_IMPORT_ERR}")
    if not CFG.JWT_SECRET:
        pytest.skip("SAS_JWT_SECRET not set — export it from Vault prod/smart-app-service")
    now = int(time.time())
    claims: Dict[str, Any] = {
        "user_id": user_id,
        "email": user_id,
        "org_id": org_id if org_id is not None else CFG.ORG,
        "tenant_id": org_id if org_id is not None else CFG.ORG,
        "roles": roles or [],
        "iat": now,
        "exp": now + ttl_seconds,
        "iss": CFG.JWT_ISSUER,
        "sub": user_id,
    }
    if sa_admin_of:
        claims["service_account_admin_of"] = sa_admin_of
    if dept_ids:
        claims["dept_ids"] = dept_ids
    if extra:
        claims.update(extra)
    return jwt.encode(claims, CFG.JWT_SECRET, algorithm="HS256")


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Session/stack availability ──────────────────────────────────────────────
@pytest.fixture(scope="session")
def sas() -> httpx.Client:
    with httpx.Client(base_url=CFG.SAS, timeout=CFG.TIMEOUT) as c:
        yield c


@pytest.fixture(scope="session")
def stack_up(sas: httpx.Client) -> bool:
    try:
        r = sas.get("/health")
    except Exception as exc:
        pytest.skip(f"smart-app-service not reachable at {CFG.SAS}: {exc}")
    if r.status_code != 200:
        pytest.skip(f"/health returned {r.status_code} at {CFG.SAS}")
    return True


# ── Role tokens ─────────────────────────────────────────────────────────────
@pytest.fixture()
def tok_super() -> str:
    return mint_jwt(roles=["super_admin"], user_id="super@acme-power.citra.ai")


@pytest.fixture()
def tok_org_admin() -> str:
    return mint_jwt(roles=["org_admin"], user_id="orgadmin@acme-power.citra.ai")


@pytest.fixture()
def tok_member() -> str:
    return mint_jwt(roles=[], user_id="member@acme-power.citra.ai")


@pytest.fixture()
def tok_other_org() -> str:
    return mint_jwt(roles=["org_admin"], org_id="some-other-org",
                    user_id="intruder@other.citra.ai")


# ── Live golden-base spec (guarantees schema-validity for mutation tests) ────
@pytest.fixture(scope="session")
def base_specs(sas: httpx.Client, stack_up: bool) -> Dict[str, Any]:
    """Fetch a REAL published app's spec to use as a schema-valid base.

    Mutation tests deep-copy this and inject one violation each, so the base
    always passes JSON-Schema/Pydantic and the mutation isolates the target
    publish rule. Skips the whole module if the app isn't present.
    """
    token = mint_jwt(roles=["super_admin"], user_id="base@acme-power.citra.ai")
    r = sas.get(f"/apps/{CFG.APP_SLUG}", headers=auth(token))
    if r.status_code != 200:
        pytest.skip(
            f"golden-base app '{CFG.APP_SLUG}' not found ({r.status_code}); "
            "set DA_APP_SLUG to a published app in this env"
        )
    body = r.json()
    if not body.get("app_spec"):
        pytest.skip(f"'{CFG.APP_SLUG}' response has no app_spec")
    return body  # {app_spec, agent_spec?}


def new_corr() -> str:
    return f"e2e-{uuid.uuid4().hex[:12]}"
