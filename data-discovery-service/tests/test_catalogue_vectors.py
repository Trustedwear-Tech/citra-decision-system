# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Tests for catalogue_vectors — the recall (Milvus) stage of dataset search.

Milvus + the embedding endpoint are mocked; we verify the signature text,
fail-open behaviour when disabled, and that search parses Milvus hits into
``(source_id, dataset_id)`` in rank order.
"""
from __future__ import annotations

import asyncio

import catalogue_vectors as cv
from config import Settings


def _settings(**over):
    base = dict(
        catalogue_vector_enabled=True,
        milvus_uri="http://milvus:19530",
        embedding_dimension=4,
    )
    base.update(over)
    return Settings(**base)


def test_signature_includes_name_desc_columns():
    sig = cv._signature(
        {
            "name": "Outages",
            "description": "outage register",
            "columns": [{"name": "status"}, {"name": "saidi_minutes"}],
        }
    )
    assert "Outages" in sig and "outage register" in sig
    assert "status" in sig and "saidi_minutes" in sig


def test_disabled_is_fail_open():
    s = Settings(catalogue_vector_enabled=False)
    assert asyncio.run(cv.index_entries(s, [{"dataset_id": "d"}])) == 0
    assert asyncio.run(
        cv.search(s, query="x", tenant_id="t", top_k=5)
    ) is None


def test_search_parses_hits_in_rank_order(monkeypatch):
    s = _settings()

    async def fake_embed(_s, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]
    monkeypatch.setattr(cv, "_embed", fake_embed)

    class _FakeClient:
        def search(self, **kw):
            # Milvus returns [[hit, hit, ...]] for one query vector.
            return [[
                {"entity": {"source_id": "billing", "dataset_id": "billing.bills"}},
                {"entity": {"source_id": "billing", "dataset_id": "billing.payments"}},
            ]]
    monkeypatch.setattr(cv, "_get_client", lambda _s: _FakeClient())

    out = asyncio.run(
        cv.search(s, query="unpaid bills", tenant_id="acme-power", top_k=5)
    )
    assert out == [
        ("billing", "billing.bills"),
        ("billing", "billing.payments"),
    ]


def test_search_dept_filter_expr(monkeypatch):
    s = _settings()

    async def fake_embed(_s, texts):
        return [[0.1, 0.2, 0.3, 0.4]]
    monkeypatch.setattr(cv, "_embed", fake_embed)

    captured = {}

    class _FC:
        def search(self, **kw):
            captured["filter"] = kw.get("filter")
            return [[]]
    monkeypatch.setattr(cv, "_get_client", lambda _s: _FC())

    # dept-scoped caller → filter has tenant + public + unstamped + dept-in
    asyncio.run(cv.search(s, query="x", tenant_id="acme-power", dept_ids=["billing_revenue"], top_k=5))
    f = captured["filter"]
    assert 'tenant_id == "acme-power"' in f
    assert 'public == "1"' in f and 'dept_id == ""' in f
    assert 'dept_id in ["billing_revenue"]' in f

    # all_depts (org_admin/super) → no dept clause at all
    asyncio.run(cv.search(s, query="x", tenant_id="acme-power", dept_ids=["billing_revenue"], top_k=5, all_depts=True))
    assert "dept_id" not in captured["filter"] and "public" not in captured["filter"]


def test_search_failure_raises_not_masked(monkeypatch):
    """When ENABLED, a genuine failure (embed/Milvus) must RAISE — never be
    masked as None/empty (RULE: fail loud)."""
    import pytest

    s = _settings()

    async def boom(_s, texts):
        raise RuntimeError("embed down")
    monkeypatch.setattr(cv, "_embed", boom)
    monkeypatch.setattr(cv, "_get_client", lambda _s: object())

    with pytest.raises(RuntimeError):
        asyncio.run(cv.search(s, query="x", tenant_id="t", top_k=5))


def test_search_disabled_returns_none(monkeypatch):
    """Disabled is a configured no-op (NOT a failure) → returns None so the
    caller serves the plain Mongo list."""
    s = Settings(catalogue_vector_enabled=False)
    assert asyncio.run(cv.search(s, query="x", tenant_id="t", top_k=5)) is None
