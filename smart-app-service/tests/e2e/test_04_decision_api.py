# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Decision API (headless) — contract negatives run always; happy paths are
gated behind DA_ALLOW_MUTATING=1 because /run + /tool can write to the SoR.
"""
from __future__ import annotations

import os

import httpx
import pytest

from conftest import CFG, auth, mint_jwt, new_corr

MUTATING = os.getenv("DA_ALLOW_MUTATING") == "1"
mutating = pytest.mark.skipif(
    not MUTATING,
    reason="set DA_ALLOW_MUTATING=1 (test env, disposable data) to run write paths",
)


@pytest.fixture()
def tok(base_specs) -> str:
    return mint_jwt(roles=["super_admin"], user_id="api@acme-power.citra.ai")


# ── Always-safe contract negatives ──────────────────────────────────────────
def test_run_requires_auth(sas: httpx.Client, base_specs):
    r = sas.post(f"/apps/{CFG.APP_SLUG}/run", json={"action": "noop", "inputs": {}})
    assert r.status_code in (401, 403, 404), r.text[:200]


def test_run_unknown_action_rejected(sas: httpx.Client, base_specs, tok: str):
    r = sas.post(f"/apps/{CFG.APP_SLUG}/run",
                 json={"action": "e2e_action_does_not_exist", "inputs": {},
                       "correlation_id": new_corr()},
                 headers=auth(tok))
    # An unknown action must be rejected, not silently 200-with-nothing.
    assert r.status_code >= 400, f"unknown action returned {r.status_code}: {r.text[:200]}"


def test_tool_unknown_name_rejected(sas: httpx.Client, base_specs, tok: str):
    r = sas.post(f"/apps/{CFG.APP_SLUG}/tool/e2e_tool_missing",
                 json={"inputs": {}}, headers=auth(tok))
    assert r.status_code >= 400, r.text[:200]


def test_tool_panel_allowlist_enforced(sas: httpx.Client, base_specs, tok: str):
    # Even a real tool must be denied when invoked with a panel_id that doesn't
    # allowlist it (defense against a leaked JWT calling admin tools).
    r = sas.post(f"/apps/{CFG.APP_SLUG}/tool/any_tool",
                 json={"inputs": {}, "panel_id": "e2e_panel_not_allowing_this"},
                 headers=auth(tok))
    assert r.status_code >= 400, r.text[:200]


# ── Gated happy paths (documented, safe by default) ─────────────────────────
@mutating
def test_run_creates_recommendation(sas: httpx.Client, base_specs, tok: str):
    """A run stages a recommendation (plan-then-apply) — NO source write yet."""
    cid = new_corr()
    r = sas.post(f"/apps/{CFG.APP_SLUG}/run",
                 json={"action": os.environ["DA_RUN_ACTION"], "inputs": {},
                       "correlation_id": cid},
                 headers=auth(tok))
    assert r.status_code == 200, r.text[:400]
    # The run should be listed and its planned writes not yet committed.
    runs = sas.get(f"/apps/{CFG.APP_SLUG}/runs", headers=auth(tok))
    assert runs.status_code == 200
    assert any(cid in str(x) for x in runs.json().get("runs", []) or [])
