# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the catalogue-keyed query planner / orchestrator.

These tests stub the LLM-bound NL→SQL planner and the engine execution so
the orchestration logic (catalogue lookup → kind dispatch → result shaping)
can be verified without spinning up Milvus or hitting an LLM endpoint.
"""
from __future__ import annotations

import asyncio
import csv

import pytest


# ---------------------------------------------------------------------------
# Fixture — multi-kind catalogue with explicit dataset_ids
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_kind_sources(monkeypatch, tmp_path):
    from router import _sources

    # Real CSV so the duckdb path is end-to-end (requires the duckdb pkg).
    csv_path = tmp_path / "loans.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "amount"])
        w.writerow([1, 100])
        w.writerow([2, 200])

    sources = {
        "claims_db": {
            "source_id": "claims_db",
            "dept_id": "ins",
            "org_id": "test",
            "type": "structured",
            "name": "Claims DB",
            "connection": {"type": "sqlite", "env_prefix": "TEST_CLAIMS"},
            "catalogue": {
                "datasets": [
                    {
                        "id": "claims_db.policies",
                        "name": "policies",
                        "kind": "sql",
                        "description": "Policy master table",
                        "columns": [
                            {"name": "policy_id", "type": "VARCHAR", "is_primary_key": True},
                            {"name": "premium", "type": "DECIMAL"},
                        ],
                        "read_via": {"kind": "sql", "target": "policies"},
                    }
                ]
            },
        },
        "ops_files": {
            "source_id": "ops_files",
            "dept_id": "ops",
            "org_id": "test",
            "type": "structured",
            "name": "Ops files",
            "connection": {},
            "catalogue": {
                "datasets": [
                    {
                        "id": "ops_files.loans",
                        "name": "loans",
                        "kind": "duckdb",
                        "columns": [
                            {"name": "id", "type": "INTEGER"},
                            {"name": "amount", "type": "INTEGER"},
                        ],
                        "read_via": {
                            "kind": "duckdb",
                            "target": "loans",
                            "files": [str(csv_path)],
                        },
                    }
                ]
            },
        },
        "sap_po": {
            "source_id": "sap_po",
            "dept_id": "fin",
            "org_id": "test",
            "type": "structured",
            "name": "SAP Purchase Orders",
            "connection": {"type": "odata"},
            "catalogue": {
                "datasets": [
                    {
                        "id": "sap_po.purchase_orders",
                        "name": "purchase_orders",
                        "kind": "odata",
                        "columns": [{"name": "PoNumber", "type": "VARCHAR"}],
                        "read_via": {"kind": "odata", "target": "PurchaseOrders"},
                    }
                ]
            },
        },
    }
    _sources.clear()
    _sources.update(sources)
    yield _sources
    _sources.clear()


# ---------------------------------------------------------------------------
# catalogue_index.select_datasets
# ---------------------------------------------------------------------------


def test_select_datasets_by_explicit_ids(multi_kind_sources):
    import catalogue_index

    out = asyncio.run(
        catalogue_index.select_datasets(
            question="anything",
            dataset_ids=["claims_db.policies", "ops_files.loans"],
        )
    )
    ids = sorted(d["id"] for d in out)
    assert ids == ["claims_db.policies", "ops_files.loans"]
    # source_id is injected when not present on the dataset dict
    assert all(d.get("source_id") for d in out)


def test_select_datasets_by_source_id(multi_kind_sources):
    import catalogue_index

    out = asyncio.run(
        catalogue_index.select_datasets(question="anything", source_id="claims_db")
    )
    assert len(out) == 1
    assert out[0]["id"] == "claims_db.policies"


def test_select_datasets_filters_by_kind(multi_kind_sources):
    import catalogue_index

    out = asyncio.run(
        catalogue_index.select_datasets(
            question="anything",
            dataset_ids=["claims_db.policies", "ops_files.loans", "sap_po.purchase_orders"],
            kinds=["sql", "duckdb"],
        )
    )
    kinds = sorted(d["kind"] for d in out)
    assert kinds == ["duckdb", "sql"]


def test_select_datasets_unknown_id_skipped(multi_kind_sources):
    import catalogue_index

    out = asyncio.run(
        catalogue_index.select_datasets(
            question="anything",
            dataset_ids=["nope.nope", "claims_db.policies"],
        )
    )
    assert len(out) == 1
    assert out[0]["id"] == "claims_db.policies"


# ---------------------------------------------------------------------------
# query_planner.plan_and_execute — sql path with mocked planner + engine
# ---------------------------------------------------------------------------


def test_plan_and_execute_sql_routes_through_engine(multi_kind_sources, monkeypatch):
    """SQL kind: NL→SQL planner runs, engine returns rows, chunk shaped."""
    import query_planner
    from models import QueryRequest

    # Stub the NL→SQL planner — orchestrator should hand it the catalogue
    # dataset and use whatever SQL it returns.
    async def fake_plan(question, datasets, **kwargs):
        assert datasets[0]["id"] == "claims_db.policies"
        return 'SELECT "policy_id" FROM "policies"'
    monkeypatch.setattr("planners.nl_to_sql.plan", fake_plan)

    # Stub query_engine.execute so we don't need a real DB.
    from query_engine import ExecutionResult

    async def fake_execute(*, kind, source, query, row_limit, read_via=None):
        assert kind == "sql"
        assert "policy_id" in query
        return ExecutionResult(
            rows=[{"policy_id": "P1"}, {"policy_id": "P2"}],
            error=None,
            elapsed_ms=5,
            sql_used=query,
        )
    monkeypatch.setattr("query_engine.execute", fake_execute)

    req = QueryRequest(
        query="list policy ids",
        source_id="claims_db",
        dataset_ids=["claims_db.policies"],
        max_results=10,
    )
    resp = asyncio.run(query_planner.plan_and_execute(req))

    assert resp.total == 1                                  # one chunk holds the table
    assert resp.source_type.value == "structured"
    chunk = resp.results[0]
    assert "P1" in chunk.text and "P2" in chunk.text
    assert chunk.metadata["kind"] == "sql"
    assert chunk.metadata["row_count"] == 2
    assert chunk.metadata["dataset_id"] == "claims_db.policies"


def test_plan_and_execute_planner_failure_returns_error_chunk(multi_kind_sources, monkeypatch):
    """When the NL→SQL planner keeps returning None, surface an error chunk."""
    import query_planner
    from models import QueryRequest

    async def planner_fails(question, datasets, **kwargs):
        return None
    monkeypatch.setattr("planners.nl_to_sql.plan", planner_fails)

    req = QueryRequest(
        query="x",
        source_id="claims_db",
        dataset_ids=["claims_db.policies"],
    )
    resp = asyncio.run(query_planner.plan_and_execute(req))
    assert resp.total == 1
    assert "failed" in resp.results[0].text.lower()
    assert resp.results[0].metadata["error"] == "planner_failed"


def test_plan_and_execute_self_corrects_on_sql_error(multi_kind_sources, monkeypatch):
    """A SQL execution error feeds the failing SQL + DB error back into the
    next planner call, which fixes the query and succeeds on attempt 2."""
    import query_planner
    from models import QueryRequest
    from query_engine import ExecutionResult

    plan_calls = []

    async def fake_plan(question, datasets, **kwargs):
        plan_calls.append(kwargs.get("previous_attempt"))
        if len(plan_calls) == 1:
            return 'SELECT "bad_col" FROM "policies"'          # first → bad
        return 'SELECT "policy_id" FROM "policies"'            # corrected
    monkeypatch.setattr("planners.nl_to_sql.plan", fake_plan)

    async def fake_execute(*, kind, source, query, row_limit, read_via=None):
        if "bad_col" in query:
            return ExecutionResult(
                rows=[], error='column "bad_col" does not exist',
                elapsed_ms=2, sql_used=query,
            )
        return ExecutionResult(
            rows=[{"policy_id": "P1"}], error=None, elapsed_ms=3, sql_used=query,
        )
    monkeypatch.setattr("query_engine.execute", fake_execute)

    req = QueryRequest(
        query="list policy ids", source_id="claims_db",
        dataset_ids=["claims_db.policies"], max_results=10,
    )
    resp = asyncio.run(query_planner.plan_and_execute(req))

    # Two attempts: first with no feedback, second with the DB error fed back.
    assert len(plan_calls) == 2
    assert plan_calls[0] is None
    assert plan_calls[1] is not None
    assert "does not exist" in plan_calls[1]["error"]
    assert plan_calls[1]["sql"] == 'SELECT "bad_col" FROM "policies"'
    # Final answer is the corrected query's rows.
    chunk = resp.results[0]
    assert "P1" in chunk.text
    assert chunk.metadata["row_count"] == 1


def test_plan_and_execute_surfaces_real_planner_error(multi_kind_sources, monkeypatch):
    """A PlannerError (e.g. null content) is retried, then its REAL reason is
    surfaced in the failure chunk — not a detail-free planner_failed."""
    import query_planner
    from models import QueryRequest
    from planners.nl_to_sql import PlannerError

    async def always_raises(question, datasets, **kwargs):
        raise PlannerError("LLM returned empty content (truncated at max_tokens)")
    monkeypatch.setattr("planners.nl_to_sql.plan", always_raises)

    req = QueryRequest(
        query="x", source_id="claims_db", dataset_ids=["claims_db.policies"],
    )
    resp = asyncio.run(query_planner.plan_and_execute(req))
    chunk = resp.results[0]
    assert chunk.metadata["error"] == "planner_failed"
    assert "empty content" in chunk.text.lower()       # real reason, not generic
    assert chunk.metadata.get("detail")


def test_plan_and_execute_extension_kind_returns_not_implemented(multi_kind_sources, monkeypatch):
    """OData / SOQL / REST default planners return None ⇒ clear stub message."""
    import query_planner
    from models import QueryRequest

    req = QueryRequest(
        query="open POs?",
        source_id="sap_po",
        dataset_ids=["sap_po.purchase_orders"],
    )
    resp = asyncio.run(query_planner.plan_and_execute(req))
    assert resp.total == 1
    assert "extension point" in resp.results[0].text
    assert resp.results[0].metadata["error"] == "not_implemented"
    # SourceType is collapsed to "structured" for any structured-family kind.
    assert resp.source_type.value == "structured"


def test_plan_and_execute_duckdb_end_to_end(multi_kind_sources, monkeypatch):
    """End-to-end through the duckdb path: planner stubs the SQL, engine actually runs DuckDB."""
    pytest.importorskip("duckdb")
    import query_planner
    from models import QueryRequest

    async def fake_plan(question, datasets, **kwargs):
        return "SELECT id, amount FROM loans ORDER BY id"
    monkeypatch.setattr("planners.nl_to_duckdb.plan", fake_plan)

    req = QueryRequest(
        query="list loans",
        source_id="ops_files",
        dataset_ids=["ops_files.loans"],
    )
    resp = asyncio.run(query_planner.plan_and_execute(req))
    assert resp.total == 1
    chunk = resp.results[0]
    assert chunk.metadata["kind"] == "duckdb"
    assert chunk.metadata["row_count"] == 2
    assert "100" in chunk.text and "200" in chunk.text


def test_plan_and_execute_unknown_source_returns_empty(multi_kind_sources):
    """Unknown source_id with no dataset_ids ⇒ empty response (legacy fallback)."""
    import query_planner
    from models import QueryRequest

    req = QueryRequest(query="anything", source_id="does_not_exist")
    resp = asyncio.run(query_planner.plan_and_execute(req))
    assert resp.total == 0
