# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Per-tenant LLM token metering (token_metering.py) — Wave 2 #8.

Pins:
  * record_usage inserts one row (tenant/model/surface/tokens/day); no-ops
    (logged) when there's no tenant or zero tokens; never raises on a write
    failure (derived store on the serving path);
  * usage_summary aggregates totals + by_model/by_surface/by_day Mongo-side
    ($group sums the token fields — not a capped Python rollup);
  * a billing READ raises on store failure (never silently under-reports).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import token_metering as tm


# ── fake collection: insert_one + aggregate($match,$group with $sum of field) ──
def _matches(doc, q):
    for k, v in (q or {}).items():
        val = doc.get(k)
        if isinstance(v, dict):
            if "$in" in v and val not in v["$in"]:
                return False
            if "$gte" in v and not (val is not None and val >= v["$gte"]):
                return False
            if "$lte" in v and not (val is not None and val <= v["$lte"]):
                return False
        elif val != v:
            return False
    return True


class _FakeCol:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    def aggregate(self, pipeline):
        docs = list(self.docs)
        group = None
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
        rows = []
        if group:
            field = group["_id"][1:]  # "$model" → "model"
            sums = {}
            for d in docs:
                key = d.get(field)
                acc = sums.setdefault(key, {"_id": key, "tokens_in": 0,
                                            "tokens_out": 0, "calls": 0})
                acc["tokens_in"] += int(d.get("tokens_in") or 0)
                acc["tokens_out"] += int(d.get("tokens_out") or 0)
                acc["calls"] += 1
            rows = list(sums.values())

        class _C:
            async def to_list(self, n):
                return rows[:n]

        return _C()


@pytest.fixture()
def col(monkeypatch):
    fake = _FakeCol()
    monkeypatch.setattr(tm, "_col", lambda: fake)
    return fake


def _rec(col, **kw):
    base = dict(tenant_id="acme", model="glm-4", surface="rubric_summarize",
                tokens_in=100, tokens_out=20)
    base.update(kw)
    asyncio.run(tm.record_usage(**base))


# ── record_usage ─────────────────────────────────────────────────────────────
def test_record_writes_a_row_with_day(col):
    at = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    _rec(col, at=at)
    assert len(col.docs) == 1
    row = col.docs[0]
    assert row["tenant_id"] == "acme" and row["tokens_in"] == 100
    assert row["surface"] == "rubric_summarize" and row["day"] == "2026-07-07"


def test_record_skips_no_tenant_and_zero_tokens(col):
    _rec(col, tenant_id=None)
    _rec(col, tokens_in=0, tokens_out=0)
    assert col.docs == []


def test_record_never_raises_on_store_failure(monkeypatch):
    class _Boom:
        async def insert_one(self, d):
            raise RuntimeError("db down")

    monkeypatch.setattr(tm, "_col", lambda: _Boom())
    # must not raise — metering a call can never break the served request
    asyncio.run(tm.record_usage(tenant_id="acme", model="m", surface="s",
                                tokens_in=5, tokens_out=5))


# ── usage_summary ────────────────────────────────────────────────────────────
def test_summary_aggregates_totals_and_breakdowns(col):
    d0 = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
    d1 = datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)
    _rec(col, model="glm-4", surface="rubric_summarize", tokens_in=100, tokens_out=20, at=d0)
    _rec(col, model="glm-4", surface="image_analyze", tokens_in=200, tokens_out=50, at=d1)
    _rec(col, model="deepseek", surface="image_analyze", tokens_in=10, tokens_out=5, at=d1)
    # a different tenant must not bleed into acme's bill
    _rec(col, tenant_id="beta", tokens_in=999, tokens_out=999, at=d1)

    s = asyncio.run(tm.usage_summary(tenant_ids=["acme"],
                                     since=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    assert s["totals"] == {"tokens_in": 310, "tokens_out": 75, "calls": 3}
    bym = {r["model"]: r for r in s["by_model"]}
    assert bym["glm-4"]["tokens_in"] == 300 and bym["deepseek"]["tokens_in"] == 10
    bysurf = {r["surface"]: r["calls"] for r in s["by_surface"]}
    assert bysurf == {"rubric_summarize": 1, "image_analyze": 2}
    days = [r["day"] for r in s["by_day"]]
    assert days == ["2026-07-06", "2026-07-07"]  # sorted ascending


def test_summary_window_excludes_out_of_range(col):
    _rec(col, at=datetime(2026, 6, 1, tzinfo=timezone.utc))   # before window
    _rec(col, at=datetime(2026, 7, 7, tzinfo=timezone.utc))   # in window
    s = asyncio.run(tm.usage_summary(
        tenant_ids=["acme"], since=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    assert s["totals"]["calls"] == 1


def test_summary_empty_tenants_is_safe(col):
    assert asyncio.run(tm.usage_summary(
        tenant_ids=[], since=datetime(2026, 7, 1, tzinfo=timezone.utc)
    ))["totals"]["calls"] == 0


def test_summary_raises_on_store_failure(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(tm, "_col", _boom)
    with pytest.raises(RuntimeError):
        asyncio.run(tm.usage_summary(
            tenant_ids=["acme"], since=datetime(2026, 7, 1, tzinfo=timezone.utc)))
