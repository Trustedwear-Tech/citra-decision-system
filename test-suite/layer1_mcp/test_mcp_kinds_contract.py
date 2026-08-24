# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Layer 1 — MCP all-kinds contract.

Two tiers, both driving the REAL shared execution path (query_engine.execute for
8 kinds; catalogue._run_mongo for mongodb):

  Tier 1 — surfacing sweep: every DatasetKind, given a bad/absent backend, must
  return a CLEAN error (rows empty, error set) and NEVER raise — a misconfigured
  source must not crash the shared MCP execution thread (RULE #1). This is the
  contract that made query_engine wrap its sync branches uniformly.

  Tier 2 — real embedded backends: sql (sqlite) and duckdb (csv) run genuine
  queries against tempfile fixtures and return real rows (happy + empty).

Emits (kind, op, state) cells via _emit; conftest folds them into mcp.json.
The REST run_query states are covered in detail by test_mcp_contract_matrix.py.
"""
from __future__ import annotations

import asyncio
import csv
import sqlite3
import sys
import types
from pathlib import Path

import pytest

MCP = Path(__file__).resolve().parents[2] / "source-mcp-template"
sys.path.insert(0, str(MCP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _emit import emit_mcp_cell  # noqa: E402

# Stub the lazy rag.api_engine ssrf import used by the rest branch. Use
# setdefault so we never clobber the matrix test's stub if it imported first
# (its ssrf_refused case needs the refusing behaviour). The stub matches the real
# (url, allow_private=False) signature and refuses link-local/localhost — so
# either module's stub satisfies both, order-independent.
from urllib.parse import urlparse  # noqa: E402


def _ssrf(url, allow_private=False):
    host = urlparse(url).hostname or ""
    if not allow_private and (host.startswith("169.254.") or host in ("localhost", "127.0.0.1")):
        return f"refused {host}"
    return None


if "rag.api_engine" not in sys.modules:
    _rag = types.ModuleType("rag"); _rag.__path__ = []
    sys.modules.setdefault("rag", _rag)
    _api = types.ModuleType("rag.api_engine"); _api._ssrf_check = _ssrf
    sys.modules["rag.api_engine"] = _api

import query_engine as qe  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Tier 1 — surfacing sweep (kind, query, read_via) with a bad/absent backend ─
SURFACE_CASES = {
    "sql":      ({"connection": {"type": "sqlite", "path": "/no/such/x.db"}}, "SELECT 1 FROM nope", None),
    "duckdb":   ({"connection": {}}, "SELECT 1", {"table": "t", "files": ["/no/such.csv"]}),
    "odata":    ({"connection": {"base_url": "http://127.0.0.1:59999"}}, {"entity": "X"}, {}),
    "soql":     ({"connection": {"instance_url": "http://127.0.0.1:59999", "access_token": "x"}}, "SELECT Id FROM Account", {}),
    "rest":     ({"connection": {"base_url": "http://127.0.0.1:59999"}}, {}, {"request": {"path": "/x"}, "response": {}}),
    "bigquery": ({"connection": {"project_id": "x"}}, "SELECT 1", None),
    "sap_rfc":  ({"connection": {}}, {}, {}),
    "semantic": ({"connection": {}}, "hello", None),
}


@pytest.mark.parametrize("kind", list(SURFACE_CASES))
def test_kind_surfaces_cleanly(kind):
    src, q, rv = SURFACE_CASES[kind]
    res = _run(qe.execute(kind=kind, source=src, query=q, row_limit=10, read_via=rv))
    assert res.rows == [], f"{kind}: expected no rows on a bad backend"
    assert res.error, f"{kind}: a bad backend MUST surface an error (RULE #1), got none"
    emit_mcp_cell(kind, "run_query", "upstream_error")


# NOTE: mongodb's query path is catalogue._run_mongo, which requires the full
# service context (fastapi + motor + the router module) — it can't run as an
# isolated unit here. Its surfacing is exercised by the service-level tests, so
# this file deliberately does NOT claim the mongodb cell (no silent gap).


# ── Tier 2 — real embedded backends (genuine rows) ───────────────────────────
# sql_connector resolves a sqlite path from env var {PREFIX}_PATH (never a dev
# default), so the fixture sets it under a unique prefix.
_SQL_PREFIX = "MCPTESTSQL"


@pytest.fixture(scope="module")
def sqlite_db(tmp_path_factory):
    import os
    p = tmp_path_factory.mktemp("mcp") / "t.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE ins(id TEXT, status TEXT, amount INT)")
    c.executemany("INSERT INTO ins VALUES(?,?,?)",
                  [("INS-1", "open", 100), ("INS-2", "repair", 200)])
    c.commit(); c.close()
    os.environ[f"{_SQL_PREFIX}_PATH"] = str(p)
    return str(p)


def _sql(db, query):
    return _run(qe.execute(kind="sql",
                           source={"connection": {"type": "sqlite", "env_prefix": _SQL_PREFIX}},
                           query=query, row_limit=10))


def test_sql_happy(sqlite_db):
    res = _sql(sqlite_db, "SELECT * FROM ins")
    assert res.error is None and len(res.rows) == 2
    emit_mcp_cell("sql", "run_query", "happy")


def test_sql_empty(sqlite_db):
    res = _sql(sqlite_db, "SELECT * FROM ins WHERE 1=0")
    assert res.error is None and res.rows == []
    emit_mcp_cell("sql", "run_query", "empty")


@pytest.fixture(scope="module")
def csv_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("mcp") / "ins.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "status", "amount"])
        w.writerow(["INS-1", "open", 100])
        w.writerow(["INS-2", "repair", 200])
    return str(p)


def _duck(csv_path, query):
    return _run(qe.execute(kind="duckdb", source={"connection": {}}, query=query,
                           row_limit=10, read_via={"table": "ins", "files": [csv_path]}))


def test_duckdb_happy(csv_file):
    res = _duck(csv_file, "SELECT * FROM ins")
    assert res.error is None and len(res.rows) == 2
    emit_mcp_cell("duckdb", "run_query", "happy")


def test_duckdb_empty(csv_file):
    res = _duck(csv_file, "SELECT * FROM ins WHERE 1=0")
    assert res.error is None and res.rows == []
    emit_mcp_cell("duckdb", "run_query", "empty")
