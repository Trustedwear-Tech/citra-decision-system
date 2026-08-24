# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Phase 6 long-tail — E2 gate closure regression, E7 photoset timing,
ring-key corroboration typing.

E2's guarantee: an identity artifact's reuse can NEVER reach the gate, on any
path, because apply_reuse_signal strips the raw keys the gate walks. E7 is
corroboration-only (weight 1, excluded from issue counts). Ring keys (declared
soft identifiers) link cases but score warn, never mismatch.
"""
from datetime import datetime, timedelta, timezone

import pytest

import entity_links
import fraud_checks as fc
import fraud_roles as fr
from fraud_synthesis import SIGNAL_ADVISORIES, severity_points


# ── E2: role-aware gate closure (regression pin) ─────────────────────────────
def test_identity_reuse_scores_zero_at_the_gate_evidence_scores():
    def _finding():
        return {"column": "photo", "duplicate": True,
                "phash_near_dups": [{"sha256": "x"}],
                "image_index": {"near_duplicates": [{"s": 1}]}}

    ident = _finding()
    assert fr.apply_reuse_signal(ident, artifact_role="identity") == "identity"
    total_i, counts_i = severity_points({"artifact_findings": [ident]})
    # Every reuse key was STRIPPED — nothing for the walker to score.
    assert counts_i.get("exact_duplicate", 0) == 0
    assert counts_i.get("phash_near_dup", 0) == 0
    assert counts_i.get("clip_near_duplicate", 0) == 0

    evid = _finding()
    assert fr.apply_reuse_signal(evid, artifact_role="evidence") == "fraud"
    total_e, counts_e = severity_points({"artifact_findings": [evid]})
    assert counts_e["exact_duplicate"] == 1 and total_e > 0
    # payment_proof role behaves like evidence at the gate (reuse suspicious).
    pay = _finding()
    assert fr.apply_reuse_signal(pay, artifact_role="payment_proof") == "fraud"


# ── E7: photoset-timing cluster ──────────────────────────────────────────────
class _FakeFpCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, *_a):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeFpCol:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *_a, **_k):
        return _FakeFpCursor(self.docs)


_T0 = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


def _fp(minutes_offset, record):
    return {"capture_time": _T0 + timedelta(minutes=minutes_offset),
            "refs": [{"record_ref": f"ops.inspections:{record}"}]}


@pytest.mark.anyio
async def test_cluster_fires_at_threshold_and_is_corroboration_only(monkeypatch):
    docs = [_fp(2, "INS-2"), _fp(5, "INS-3"), _fp(3, "INS-1")]  # INS-1 = self
    monkeypatch.setattr(fc, "_fingerprints_col", lambda: _FakeFpCol(docs))
    sig = await fc.photoset_timing_cluster(
        tenant_id="acme", app_slug="triage",
        record_ref="ops.inspections:INS-1", capture_times=[_T0])
    assert sig and sig["signal"] == fc.SIGNAL_PHOTOSET_TIMING
    assert sig["corroboration_only"] is True
    assert sig["other_record_count"] == 2          # self excluded
    # Weight 1 at the gate — raises it, never gates alone (gate_min_points 2).
    total, counts = severity_points({"photoset_timing": sig})
    assert counts["photoset_timing_cluster"] == 1 and total == 1
    assert "photoset_timing_cluster" in SIGNAL_ADVISORIES


@pytest.mark.anyio
async def test_cluster_below_threshold_or_outside_window_is_none(monkeypatch):
    # Only ONE other record in the window → None (two back-to-back site
    # visits are legitimate).
    monkeypatch.setattr(fc, "_fingerprints_col",
                        lambda: _FakeFpCol([_fp(2, "INS-2")]))
    assert await fc.photoset_timing_cluster(
        tenant_id="acme", app_slug="triage",
        record_ref="ops.inspections:INS-1", capture_times=[_T0]) is None
    # Two others but 4 hours away → None.
    monkeypatch.setattr(fc, "_fingerprints_col",
                        lambda: _FakeFpCol([_fp(240, "INS-2"), _fp(250, "INS-3")]))
    assert await fc.photoset_timing_cluster(
        tenant_id="acme", app_slug="triage",
        record_ref="ops.inspections:INS-1", capture_times=[_T0]) is None
    # No capture times at all → None without touching the store.
    assert await fc.photoset_timing_cluster(
        tenant_id="acme", app_slug="triage",
        record_ref="r", capture_times=[]) is None


# ── Ring keys: declared soft identifiers are corroboration, not proof ────────
def test_declared_generic_ring_key_links_but_scores_warn():
    ents = entity_links.extract_linkable(
        {"employer_name": "ACME LOGISTICS PVT LTD",
         "bank_account": "12345678901"},
        identity_fields=["employer_name", "bank_account"])
    by_field = {e["field"]: e for e in ents}
    # employer: heuristic doesn't know it → declared-generic ring key.
    emp = by_field["employer_name"]
    assert emp["entity_type"] == "id" and emp.get("declared_generic") is True
    assert entity_links._link_severity(emp) == "warn"
    # account: recognised hard identifier → mismatch severity.
    acct = by_field["bank_account"]
    assert acct["entity_type"] == "account" and not acct.get("declared_generic")
    assert entity_links._link_severity(acct) == "mismatch"
