# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Publish-validator coverage via POST /builder/validate.

/builder/validate runs the SAME spec-shape rules as /publish but persists
nothing and needs no builder pod — so it's the fastest, most deterministic way
to prove each rule rejects its violation.

Contract (from main.py::builder_validate):
  * clean spec          → 200 {"ok": true}
  * schema/pydantic fail → 422 {"detail": "Spec validation failed: ..."}
  * rule fail           → 422 {"detail": {"code":"spec_rules_failed",
                                           "errors":[{"rule":"F-01",...}, ...]}}
"""
from __future__ import annotations

import httpx
import pytest

from conftest import auth, mint_jwt
from specs import MUTATORS


def _validate(sas: httpx.Client, payload: dict, token: str) -> httpx.Response:
    body = {"app_spec": payload["app_spec"]}
    if payload.get("agent_spec") is not None:
        body["agent_spec"] = payload["agent_spec"]
    return sas.post("/builder/validate", json=body, headers=auth(token))


def _rule_codes(resp: httpx.Response) -> list[str]:
    if resp.status_code != 422:
        return []
    detail = resp.json().get("detail")
    if isinstance(detail, dict):
        return [e.get("rule") for e in (detail.get("errors") or [])]
    return []  # plain-string detail == schema error, not a rule


@pytest.fixture()
def tok(base_specs) -> str:
    return mint_jwt(roles=["super_admin"], user_id="validate@acme-power.citra.ai")


def test_clean_base_passes(sas: httpx.Client, base_specs, tok: str):
    """The real published app must re-validate clean (200 ok)."""
    r = _validate(sas, base_specs, tok)
    assert r.status_code == 200, (
        f"golden-base app did not re-validate clean: {r.status_code} {r.text[:400]}"
    )
    assert r.json().get("ok") is True


@pytest.mark.parametrize("mut_id,mutator", MUTATORS, ids=[m[0] for m in MUTATORS])
def test_validator_rejects_violation(sas: httpx.Client, base_specs, tok: str,
                                     mut_id: str, mutator):
    result = mutator(base_specs)
    if result is None:
        pytest.skip(f"{mut_id}: golden base lacks the structure to mutate "
                    "(e.g. no form panel / no mcp_action agent) in this env")
    payload, expected_rule = result
    r = _validate(sas, payload, tok)
    assert r.status_code == 422, (
        f"{mut_id}: expected 422 rejection, got {r.status_code}: {r.text[:400]}"
    )
    codes = _rule_codes(r)
    assert expected_rule in codes, (
        f"{mut_id}: expected rule '{expected_rule}' in errors, got {codes}. "
        f"(If empty, the mutation tripped a schema error first — refine the mutator.) "
        f"Body: {r.text[:400]}"
    )
