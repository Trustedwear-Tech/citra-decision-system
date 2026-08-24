# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the shared query_engine that backs both /query and /run_query."""
from __future__ import annotations

import asyncio
import csv
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# SELECT-only enforcement (DuckDB dialect)
# ---------------------------------------------------------------------------


def test_duckdb_guard_rejects_dml():
    from query_engine import _enforce_select_only_duckdb

    safe, err = _enforce_select_only_duckdb("DELETE FROM t", row_limit=10)
    assert err is not None
    assert safe == ""


def test_duckdb_guard_rejects_multi_statement():
    from query_engine import _enforce_select_only_duckdb

    safe, err = _enforce_select_only_duckdb("SELECT 1; SELECT 2", row_limit=10)
    assert err is not None


def test_duckdb_guard_wraps_with_limit():
    from query_engine import _enforce_select_only_duckdb

    safe, err = _enforce_select_only_duckdb("SELECT * FROM t", row_limit=25)
    assert err is None
    assert "LIMIT 25" in safe.upper()
    # Wrapping is the cross-dialect approach — original SQL lives in a sub-query.
    assert "_capped" in safe


# ---------------------------------------------------------------------------
# DuckDB execution path — only runs if duckdb is installed locally
# ---------------------------------------------------------------------------


def _require_duckdb():
    pytest.importorskip("duckdb", reason="duckdb optional dep not installed")


def test_duckdb_runs_query_over_csv_file():
    """End-to-end: write a CSV, register it via read_via, SELECT through engine."""
    _require_duckdb()
    from query_engine import execute

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "loans.csv")
        with open(csv_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["farmer_id", "amount", "crop"])
            w.writerow([1, 5000, "wheat"])
            w.writerow([2, 7500, "paddy"])
            w.writerow([3, 3000, "maize"])

        result = asyncio.run(
            execute(
                kind="duckdb",
                source={"source_id": "ops"},
                query='SELECT crop, SUM(amount) AS total FROM loans GROUP BY crop ORDER BY total DESC',
                row_limit=10,
                read_via={"kind": "duckdb", "table": "loans", "files": [csv_path]},
            )
        )

    assert result.error is None, f"unexpected error: {result.error}"
    assert len(result.rows) == 3
    # Highest total first (paddy = 7500).
    assert result.rows[0]["crop"] == "paddy"
    assert int(result.rows[0]["total"]) == 7500


def test_duckdb_rejects_invalid_table_identifier():
    from query_engine import execute

    result = asyncio.run(
        execute(
            kind="duckdb",
            source={"source_id": "ops"},
            query="SELECT 1",
            row_limit=10,
            read_via={"kind": "duckdb", "table": "bad name; DROP", "files": ["/tmp/x.csv"]},
        )
    )
    assert result.error is not None
    assert "Invalid table identifier" in result.error


def test_duckdb_requires_files():
    from query_engine import execute

    result = asyncio.run(
        execute(
            kind="duckdb",
            source={"source_id": "ops"},
            query="SELECT 1",
            row_limit=10,
            read_via={"kind": "duckdb", "table": "x", "files": []},
        )
    )
    assert result.error is not None
    assert "files[]" in result.error


# ---------------------------------------------------------------------------
# Catalogue-side wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_source(monkeypatch, tmp_path):
    """Source with a duckdb-catalogued dataset pointing at a real CSV on disk."""
    from router import _sources

    csv_path = tmp_path / "farmers.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "name"])
        w.writerow([1, "Alice"])
        w.writerow([2, "Bob"])

    sources = {
        "ops_files": {
            "source_id": "ops_files",
            "dept_id": "ops",
            "org_id": "test",
            "type": "structured",
            "name": "Ops file drops",
            "connection": {},
            "catalogue": {
                "datasets": [
                    {
                        "id": "ops_files.farmers",
                        "name": "farmers",
                        "kind": "duckdb",
                        "columns": [
                            {"name": "id", "type": "INTEGER", "is_primary_key": True},
                            {"name": "name", "type": "VARCHAR"},
                        ],
                        "read_via": {
                            "kind": "duckdb",
                            "target": "farmers",
                            "files": [str(csv_path)],
                        },
                    }
                ]
            },
        }
    }
    _sources.clear()
    _sources.update(sources)
    yield _sources
    _sources.clear()


def test_run_query_duckdb_end_to_end(duckdb_source):
    _require_duckdb()
    from catalogue import run_query
    from models import DatasetKind, RunQueryRequest

    req = RunQueryRequest(
        source_id="ops_files",
        dataset_id="ops_files.farmers",
        kind=DatasetKind.duckdb,
        query="SELECT * FROM farmers ORDER BY id",
        row_limit=10,
    )
    resp = asyncio.run(run_query(req))
    assert resp.error is None, f"unexpected error: {resp.error}"
    assert resp.total == 2
    assert resp.rows[0]["name"] == "Alice"


def test_run_query_duckdb_requires_dataset_id(duckdb_source):
    from catalogue import run_query
    from fastapi import HTTPException
    from models import DatasetKind, RunQueryRequest

    req = RunQueryRequest(
        source_id="ops_files",
        kind=DatasetKind.duckdb,
        query="SELECT * FROM farmers",
        row_limit=10,
    )
    with pytest.raises(HTTPException) as ei:
        asyncio.run(run_query(req))
    assert ei.value.status_code == 400
