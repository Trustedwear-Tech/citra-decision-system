# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""mint_semantic_read_token — the RAG short-circuit service-auth path.

An agent/trigger run has no end-user identity, so smart-app-service mints this
short-lived org-scoped token to read a semantic source; Citra-Service authorizes
the `semantic_service` marker and scopes the data to the source's own dept.
"""
import os

import jwt
import pytest

from citra_auth import mint_semantic_read_token

_SECRET = "test-secret-for-minting"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)
    monkeypatch.setenv("JWT_ISSUER", "Citra-AI")


def _decode(tok):
    return jwt.decode(tok, _SECRET, algorithms=["HS256"])


def test_mints_semantic_service_token_with_min_privilege():
    tok = mint_semantic_read_token(org_id="acme-power", on_behalf_of_user_id="ba@acme")
    assert tok
    claims = _decode(tok)
    assert claims["semantic_service"] is True          # the ONE capability granted
    assert claims["org_id"] == "acme-power"
    assert claims["roles"] == ["user"]                 # no admin / IT-workflow
    assert claims["on_behalf_of_user_id"] == "ba@acme"  # audit provenance
    assert claims["purpose"] == "agent_semantic_read"
    assert claims["exp"] > claims["iat"]               # short-lived
    assert "workflow_system" not in claims             # NOT the IT-workflow identity


def test_requires_org_id():
    assert mint_semantic_read_token(org_id="") is None
    assert mint_semantic_read_token(org_id=None) is None  # type: ignore[arg-type]


def test_returns_none_without_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert mint_semantic_read_token(org_id="acme-power") is None


def test_carries_optional_dept_ids():
    tok = mint_semantic_read_token(org_id="acme-power", dept_ids=["central_pmu"])
    assert _decode(tok)["dept_ids"] == ["central_pmu"]
