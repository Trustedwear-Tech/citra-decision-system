# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Value-stamping unit tests (docs/money-saved-roi-plan.md V2/V3).

Covers the ROI spine's core guarantees:
  * definition_version is stable for identical definitions and CHANGES on any
    edit — the day-zero freeze receipt.
  * pick_value_semantics_for_table routing (exact / suffix / single-block).
  * compute_outcome_value: prevented_loss uses the exposure FROZEN at decision
    time; recovered sums realization rows within the window; every failure
    path stamps an ERROR, never a silent zero.
  * The decision_ledger panel resolver flattens stamped values on the stable
    row contract.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests._test_helpers import client  # noqa: F401 — pytest fixture
from value_stamping import (
    _build_multi_query,
    compute_outcome_value,
    definition_version,
    pick_value_semantics_for_table,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# definition_version — the freeze receipt
# ---------------------------------------------------------------------------

def test_definition_version_stable_and_edit_sensitive():
    vs = {
        "value_kind": "recovered",
        "realization": {"dataset": "recovery.payments", "match_field": "consumer_id",
                        "amount_field": "amount", "date_field": "payment_date",
                        "window_days": 90},
        "attribution": "approved_recommendation",
    }
    v1 = definition_version(vs)
    # Key order must not matter (canonical JSON).
    v2 = definition_version(dict(reversed(list(vs.items()))))
    assert v1 == v2
    assert len(v1) == 12
    # ANY definitional edit produces a different version — a mid-pilot change
    # is visible in the ledger.
    edited = {**vs, "realization": {**vs["realization"], "window_days": 60}}
    assert definition_version(edited) != v1


# ---------------------------------------------------------------------------
# pick_value_semantics_for_table
# ---------------------------------------------------------------------------

def test_pick_by_exact_suffix_and_single_block():
    block = {"value_kind": "recovered"}
    vs_map = {"recovery.disputes": block, "claims.claims": {"value_kind": "prevented_loss"}}
    assert pick_value_semantics_for_table(vs_map, "recovery.disputes") is block
    assert pick_value_semantics_for_table(vs_map, "disputes") is block
    # Ambiguous multi-block map with an unknown table → None, never a guess.
    assert pick_value_semantics_for_table(vs_map, "nope") is None
    # Single-block map applies regardless of table naming.
    assert pick_value_semantics_for_table({"x.y": block}, "whatever") is block
    assert pick_value_semantics_for_table(None, "disputes") is None


# ---------------------------------------------------------------------------
# _build_multi_query
# ---------------------------------------------------------------------------

def test_build_multi_query_shapes():
    q = _build_multi_query("sql", "payments", "consumer_id", "C-001")
    assert q == "SELECT * FROM payments WHERE consumer_id = 'C-001'"
    # Quote-injection is escaped by the shared _sql_quote.
    q = _build_multi_query("sql", "payments", "consumer_id", "C'1")
    assert "''" in q
    q = _build_multi_query("mongodb", "payments", "consumer_id", "C-001")
    assert q == {"consumer_id": "C-001"}
    assert _build_multi_query("rest", "payments", "consumer_id", "x") is None


# ---------------------------------------------------------------------------
# compute_outcome_value — prevented_loss
# ---------------------------------------------------------------------------

_PREVENTED_VS = {
    "value_kind": "prevented_loss",
    "exposure_field": "claim_amount",
    "prevented_when": ["rejected"],
    "currency": "INR",
    "attribution": "approved_recommendation",
    "definition_version": "abc123def456",
}


def test_prevented_loss_uses_frozen_exposure():
    rec = {"context": {"claim_amount": "₹1,20,000"}, "created_at": datetime.now(timezone.utc)}
    # The CURRENT row shows a different number — must NOT be used.
    row = {"claim_amount": "999", "status": "rejected"}
    value, err = _run(compute_outcome_value(
        settings=None, rec=rec, row=row, cur_status="Rejected",
        vs=_PREVENTED_VS, user_jwt=None))
    assert err is None
    assert value["amount"] == 120000.0
    assert value["kind"] == "prevented_loss"
    assert value["definition_version"] == "abc123def456"
    assert "frozen at decision time" in value["basis"]["source"]


def test_prevented_loss_non_prevented_status_is_a_non_value():
    rec = {"context": {"claim_amount": "5000"}}
    value, err = _run(compute_outcome_value(
        settings=None, rec=rec, row={}, cur_status="approved",
        vs=_PREVENTED_VS, user_jwt=None))
    assert value is None and err is None  # no value under this definition — NOT an error


def test_prevented_loss_missing_exposure_is_a_loud_error():
    value, err = _run(compute_outcome_value(
        settings=None, rec={"context": {}}, row={}, cur_status="rejected",
        vs=_PREVENTED_VS, user_jwt=None))
    assert value is None
    assert err and "NOT stamped" in err


# ---------------------------------------------------------------------------
# compute_outcome_value — recovered (realization read)
# ---------------------------------------------------------------------------

_RECOVERED_VS = {
    "value_kind": "recovered",
    "currency": "INR",
    "attribution": "approved_recommendation",
    "definition_version": "feed00000001",
    "realization": {
        "dataset": "recovery.payments", "source_id": "recovery", "kind": "sql",
        "match_field": "consumer_id", "amount_field": "amount",
        "date_field": "payment_date", "window_days": 90,
    },
}


def _rec(consumer="C-001"):
    return {"context": {"consumer_id": consumer},
            "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)}


def _patch_read(monkeypatch, resp):
    async def _fake(**kwargs):
        _fake.kwargs = kwargs
        return resp
    import proxy_clients
    monkeypatch.setattr(proxy_clients, "call_dept_mcp_read", _fake)
    return _fake


def test_recovered_sums_rows_within_window(monkeypatch):
    fake = _patch_read(monkeypatch, {"rows": [
        {"amount": "1000", "payment_date": "2026-06-10"},   # in window
        {"amount": "2500.50", "payment_date": "2026-07-01"},  # in window
        {"amount": "9999", "payment_date": "2026-05-01"},   # BEFORE decision — excluded
        {"amount": "8888", "payment_date": "2026-12-01"},   # after 90d window — excluded
    ]})
    value, err = _run(compute_outcome_value(
        settings=None, rec=_rec(), row={}, cur_status="approved",
        vs=_RECOVERED_VS, user_jwt="jwt"))
    assert err is None
    assert value["amount"] == 3500.5
    assert value["basis"]["rows_counted"] == 2
    # The read went through the STRUCTURED plane with the right routing.
    assert fake.kwargs["source_id"] == "recovery"
    assert fake.kwargs["dataset_id"] == "recovery.payments"
    assert "WHERE consumer_id = 'C-001'" in fake.kwargs["query"]


def test_recovered_in_band_error_stamps_value_error(monkeypatch):
    _patch_read(monkeypatch, {"error": "relation payments does not exist"})
    value, err = _run(compute_outcome_value(
        settings=None, rec=_rec(), row={}, cur_status="approved",
        vs=_RECOVERED_VS, user_jwt="jwt"))
    assert value is None
    assert err and "realization read failed" in err


def test_recovered_all_unparseable_amounts_never_silent_zero(monkeypatch):
    _patch_read(monkeypatch, {"rows": [
        {"amount": "N/A", "payment_date": "2026-06-10"},
        {"amount": "??", "payment_date": "2026-06-11"},
    ]})
    value, err = _run(compute_outcome_value(
        settings=None, rec=_rec(), row={}, cur_status="approved",
        vs=_RECOVERED_VS, user_jwt="jwt"))
    assert value is None
    assert err and "unparseable" in err


def test_recovered_missing_match_value_is_a_loud_error():
    value, err = _run(compute_outcome_value(
        settings=None, rec={"context": {}}, row={}, cur_status="approved",
        vs=_RECOVERED_VS, user_jwt="jwt"))
    assert value is None
    assert err and "match value" in err


def test_recovered_genuinely_zero_rows_is_a_zero_value(monkeypatch):
    # No realization rows at all = a REAL zero (nothing recovered yet).
    _patch_read(monkeypatch, {"rows": []})
    value, err = _run(compute_outcome_value(
        settings=None, rec=_rec(), row={}, cur_status="approved",
        vs=_RECOVERED_VS, user_jwt="jwt"))
    assert err is None
    assert value["amount"] == 0.0
    assert value["basis"]["rows_counted"] == 0


# ---------------------------------------------------------------------------
# decision_ledger panel resolver
# ---------------------------------------------------------------------------

def test_value_stats_endpoint_admin_gate_and_shape(client, monkeypatch):
    import time

    import jwt as pyjwt

    import main
    from tests._test_helpers import JWT_SECRET

    def _mint(roles):
        return pyjwt.encode(
            {"sub": "u1", "user_id": "u1", "tenant_id": "acme-power",
             "roles": roles, "iat": int(time.time()),
             "exp": int(time.time()) + 600, "iss": "Citra-AI"},
            JWT_SECRET, algorithm="HS256")

    # Non-admin → 403 (same gate as decision-stats).
    r = client.get("/org/value-stats",
                   headers={"Authorization": f"Bearer {_mint(['user'])}"})
    assert r.status_code == 403

    class _FakeCol:
        def aggregate(self, pipeline):
            _FakeCol.pipeline = pipeline

            class _Cur:
                async def to_list(self, length=None):
                    return [{
                        "totals": [{"_id": {"kind": "recovered", "currency": "INR"},
                                    "amount": 3500.5, "decisions": 2}],
                        "by_app": [{"_id": {"slug": "recovery-app", "kind": "recovered",
                                            "currency": "INR"},
                                    "amount": 3500.5, "decisions": 2}],
                        "definitions": [{"versions": ["feed00000001"],
                                         "attributions": ["approved_recommendation"]}],
                    }]
            return _Cur()

        async def count_documents(self, q):
            _FakeCol.err_query = q
            return 1

    monkeypatch.setattr(main, "get_decision_records_col", lambda: _FakeCol())
    r = client.get("/org/value-stats?period=month",
                   headers={"Authorization": f"Bearer {_mint(['org_admin'])}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"] == [{"kind": "recovered", "currency": "INR",
                               "amount": 3500.5, "decisions": 2}]
    assert body["by_app"][0]["slug"] == "recovery-app"
    assert body["definition_versions"] == ["feed00000001"]
    assert body["attributions"] == ["approved_recommendation"]
    assert body["value_errors"] == 1
    assert body["baseline"] is None  # no invented baselines, ever
    # The error count query looks at value_error, NOT stamped amounts.
    assert "outcome.value_error" in _FakeCol.err_query
    assert "outcome.value.amount" not in _FakeCol.err_query


def test_value_backfill_stamps_values_never_labels(client, monkeypatch):
    """Backfill stamps outcome.value / value_error onto settled decisions and
    NEVER rewrites the historical label; already-valued records are untouched."""
    import time

    import jwt as pyjwt

    import main
    from tests._test_helpers import JWT_SECRET, _MemCol

    ledger = _MemCol([
        {"decision_id": "d1", "tenant_id": "acme-power", "slug": "recovery-tracker",
         "app_id": "app1", "agent_id": "ag1", "created_at": "2026-07-01",
         "outcome": {"label": "good"}},
        {"decision_id": "d2", "tenant_id": "acme-power", "slug": "recovery-tracker",
         "app_id": "app1", "agent_id": "ag1", "created_at": "2026-07-02",
         "outcome": {"label": "bad"}},
        # Already valued — must fall out of the query untouched.
        {"decision_id": "d3", "tenant_id": "acme-power", "slug": "recovery-tracker",
         "app_id": "app1", "agent_id": "ag1", "created_at": "2026-07-03",
         "outcome": {"label": "good", "value": {"amount": 1.0}}},
    ])
    agents = _MemCol([
        {"agent_id": "ag1", "tenant_id": "acme-power",
         "agent_spec": {"outcome_poll": {"enabled": True,
                                         "table": "field_operations.theft_cases"}}},
    ])
    apps = _MemCol([
        {"app_id": "app1", "tenant_id": "acme-power", "slug": "recovery-tracker",
         "value_semantics": {"field_operations.theft_cases": {
             "value_kind": "recovered", "definition_version": "feed00000001"}}},
    ])
    monkeypatch.setattr(main, "get_decision_records_col", lambda: ledger)
    monkeypatch.setattr(main, "get_agents_col", lambda: agents)
    monkeypatch.setattr(main, "get_apps_col", lambda: apps)

    async def _fake_mint(settings, rec):
        return "sysjwt"
    monkeypatch.setattr(main, "_mint_poll_system_jwt", _fake_mint)

    async def _fake_classify(*, settings, rec, cfg, user_jwt, value_cfg=None):
        assert value_cfg["definition_version"] == "feed00000001"
        if rec["decision_id"] == "d1":
            return {"label": "good", "value": {
                "amount": 250.0, "kind": "recovered", "currency": "USD",
                "definition_version": "feed00000001", "basis": {"rows_counted": 1}}}
        return {"label": "bad", "value_error": "realization read failed: boom"}
    monkeypatch.setattr(main, "_classify_decision_outcome", _fake_classify)

    tok = pyjwt.encode(
        {"sub": "u1", "user_id": "u1", "tenant_id": "acme-power",
         "roles": ["org_admin"], "iat": int(time.time()),
         "exp": int(time.time()) + 600, "iss": "Citra-AI"},
        JWT_SECRET, algorithm="HS256")
    r = client.post("/admin/value-backfill?slug=recovery-tracker",
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 2  # d3 excluded — already valued
    assert body["stamped"] == 1
    assert body["value_errors"] == 1

    d1 = next(d for d in ledger.docs if d["decision_id"] == "d1")
    assert d1["outcome"]["label"] == "good"  # label untouched
    assert d1["outcome"]["value"]["amount"] == 250.0
    assert d1["outcome"]["value"]["basis"]["backfilled"] is True
    d2 = next(d for d in ledger.docs if d["decision_id"] == "d2")
    assert d2["outcome"]["label"] == "bad"  # fresh 'bad' NOT written over history
    assert "value" not in d2["outcome"]
    assert "read failed" in d2["outcome"]["value_error"]
    d3 = next(d for d in ledger.docs if d["decision_id"] == "d3")
    assert d3["outcome"]["value"]["amount"] == 1.0  # untouched


def test_seeded_recovery_roi_page_resolves_against_ledger(monkeypatch):
    """The acme-power Recovery ROI page (V5 demo wiring) resolves its ledger
    queue through the real resolve_panel_data dispatch — proving the seeded
    spec, the decision_ledger data source, and the resolver agree."""
    import json
    from pathlib import Path

    from tests._test_helpers import _MemCol
    import main
    import panel_data
    from config import Settings
    from models import AppSpec

    seed = (Path(__file__).resolve().parents[2] / "demo-data" / "tenants"
            / "acme-power" / "apps" / "02_recovery_tracker.json")
    if not seed.exists():
        pytest.skip("demo-data seed not present")
    app_spec = AppSpec.model_validate(
        json.loads(seed.read_text(encoding="utf-8"))["app_spec"])
    app_spec.tenant_id = "acme-power"

    col = _MemCol([{
        "slug": "acme-power-recovery-tracker", "tenant_id": "acme-power",
        "decision_id": "d1", "mode": "human_approved", "overrides": [],
        "created_at": "2026-07-01T10:00:00+00:00",
        "recommendation": {"decision": "Pursue recovery."},
        "record_keys": [{"key_values": ["TC-1001"]}],
        "outcome": {"label": "good",
                    "value": {"amount": 1200.0, "kind": "recovered",
                              "currency": "USD",
                              "definition_version": "abc123abc123"}},
    }])
    monkeypatch.setattr(main, "get_decision_records_col", lambda: col)

    resp = _run(panel_data.resolve_panel_data(
        settings=Settings(jwt_secret="x", mongodb_uri="mongodb://x"),
        app_spec=app_spec, panel_id="roi_ledger"))
    assert resp.source_kind == "decision_ledger"
    assert resp.total == 1
    row = resp.rows[0]
    assert row["case"] == "TC-1001"
    assert row["value_amount"] == 1200.0
    assert row["currency"] == "USD"


def test_decision_ledger_resolver_flattens_stamped_values(monkeypatch):
    from tests._test_helpers import _MemCol
    import main
    import panel_data

    col = _MemCol([
        {
            "slug": "recovery-app", "tenant_id": "acme-power",
            "decision_id": "d1", "mode": "human_approved", "overrides": [],
            "created_at": "2026-07-01T10:00:00+00:00",
            "recommendation": {"decision": "Approve the payment claim."},
            "record_keys": [{"key_values": ["DSP-1"]}],
            "outcome": {"label": "positive",
                        "value": {"amount": 3500.5, "kind": "recovered",
                                  "currency": "INR",
                                  "definition_version": "feed00000001"}},
            "retrieval_count": 2,
        },
        {
            "slug": "recovery-app", "tenant_id": "acme-power",
            "decision_id": "d2", "mode": "human_rejected",
            "overrides": [{"field": "x"}],
            "created_at": "2026-07-02T10:00:00+00:00",
            "recommendation": {"decision": "Reject."},
            "record_keys": [{"key_values": ["DSP-2"]}],
            "outcome": {"label": "negative"},
        },
        {"slug": "other-app", "tenant_id": "acme-power", "decision_id": "d3"},
    ])
    monkeypatch.setattr(main, "get_decision_records_col", lambda: col)

    app_spec = SimpleNamespace(slug="recovery-app", tenant_id="acme-power")
    ds = SimpleNamespace(type="decision_ledger", ref="recovery-app")
    rows, total, truncated, note = _run(panel_data._resolve_decision_ledger_rows(
        app_spec=app_spec, ds=ds, limit=50))
    assert note is None
    assert total == 2  # other-app's row excluded
    by_id = {r["decision_id"]: r for r in rows}
    r1 = by_id["d1"]
    assert r1["value_amount"] == 3500.5
    assert r1["value_kind"] == "recovered"
    assert r1["currency"] == "INR"
    assert r1["definition_version"] == "feed00000001"
    assert r1["case"] == "DSP-1"
    assert r1["overridden"] is False
    r2 = by_id["d2"]
    assert r2["value_amount"] is None
    assert r2["overridden"] is True
    assert r2["outcome_label"] == "negative"
