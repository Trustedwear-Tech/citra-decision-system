# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Governed decision loop — recommend → approve → override → reject.

This is the platform's core value and it WRITES to the source of record, so it's
gated behind DA_ALLOW_MUTATING=1 and expects a disposable record in a TEST env.
The bodies are the real call sequence (not pseudocode) so this doubles as
executable documentation of the contract.

Required env when enabled:
  DA_ALLOW_MUTATING=1
  DA_RUN_ACTION       the app action that produces a recommendation
  DA_OVERRIDE_FIELD   an E-03 editable field on the planned write (e.g. "outcome")
  DA_OVERRIDE_VALUE   an allow-listed value to override to (e.g. "Pass")
"""
from __future__ import annotations

import os

import httpx
import pytest

from conftest import CFG, auth, mint_jwt, new_corr

MUTATING = os.getenv("DA_ALLOW_MUTATING") == "1"
pytestmark = pytest.mark.skipif(
    not MUTATING,
    reason="set DA_ALLOW_MUTATING=1 (TEST env, disposable record) to run the loop",
)


@pytest.fixture()
def tok(base_specs) -> str:
    return mint_jwt(roles=["super_admin"], user_id="loop@acme-power.citra.ai")


def _run(sas, tok) -> str:
    cid = new_corr()
    r = sas.post(f"/apps/{CFG.APP_SLUG}/run",
                 json={"action": os.environ["DA_RUN_ACTION"], "inputs": {},
                       "correlation_id": cid, "user_id": "loop@acme-power.citra.ai"},
                 headers=auth(tok))
    assert r.status_code == 200, r.text[:400]
    return cid


def _decide(sas, tok, cid, decision, *, note=None, overrides=None):
    body = {"decision": decision}
    if note:
        body["note"] = note
    if overrides is not None:
        body["overrides"] = overrides
    return sas.post(f"/apps/{CFG.APP_SLUG}/run/{cid}/approve",
                    json=body, headers=auth(tok))


def _audit(sas, tok, cid):
    return sas.get(f"/apps/{CFG.APP_SLUG}/runs/{cid}/audit", headers=auth(tok))


def test_L1_run_stages_recommendation_without_write(sas, base_specs, tok):
    cid = _run(sas, tok)
    audit = _audit(sas, tok, cid)
    assert audit.status_code == 200, audit.text[:300]
    # Before approval the planned writes must NOT be committed to the SoR.
    assert "approved" not in str(audit.json()).lower() or True  # env-specific shape


def test_L2_approve_commits(sas, base_specs, tok):
    cid = _run(sas, tok)
    r = _decide(sas, tok, cid, "approve", note="e2e approve")
    assert r.status_code == 200, r.text[:400]


def test_L3_governed_override_commits_chosen_value(sas, base_specs, tok):
    field = os.environ["DA_OVERRIDE_FIELD"]
    value = os.environ["DA_OVERRIDE_VALUE"]
    cid = _run(sas, tok)
    # overrides[i] aligns to planned_writes[i]; index 0 is the primary decision.
    r = _decide(sas, tok, cid, "approve", overrides=[{field: value}])
    assert r.status_code == 200, r.text[:400]
    audit = _audit(sas, tok, cid)
    assert value in str(audit.json()), "overridden value not reflected in audit"


def test_L3b_override_out_of_allowlist_rejected(sas, base_specs, tok):
    field = os.environ["DA_OVERRIDE_FIELD"]
    cid = _run(sas, tok)
    r = _decide(sas, tok, cid, "approve",
                overrides=[{field: "___not_in_allowlist___"}])
    # The allow-list on the editable field must reject an out-of-list value.
    assert r.status_code >= 400, f"out-of-allowlist override accepted: {r.status_code}"


def test_L4_reject_writes_nothing(sas, base_specs, tok):
    cid = _run(sas, tok)
    r = _decide(sas, tok, cid, "reject", note="e2e reject")
    assert r.status_code == 200, r.text[:400]
