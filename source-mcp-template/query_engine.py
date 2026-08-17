# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Query Engine — shared structured execution core
================================================
Single entry point for executing a *generated* read query against a dataset,
regardless of whether the request arrived via:

  • POST /query         (NL → planner → SQL via rag.structured_engine; this
                         module owns Stage 4: execution + safety)
  • POST /run_query     (caller hands us the SQL/expression directly via
                         the catalogue contract used by smart-app runtime
                         and the data-discovery-service crawler)

Why one engine
--------------
Before this module existed, both endpoints dispatched to
`connectors.sql_connector.execute_sql` independently. That meant any future
backend-specific tweak (DuckDB-over-files, OData $batch, MongoDB find, …)
had to be plumbed through two code paths. Funnelling everything through
`query_engine.execute(...)` keeps the safety story (SELECT-only, row-cap)
and the per-kind dispatch in one place.

Backends
--------
  sql      — RDBMS (Postgres / MySQL / SQL Server / SQLite). Delegates to
             `connectors.sql_connector.execute_sql` which uses sqlglot to
             enforce SELECT-only and wrap the query with a hard LIMIT.
  duckdb   — DuckDB-over-files. Opens an in-memory DuckDB, registers each
             file in `read_via.files[]` as a view named `read_via.table`
             using `read_xlsx_auto` / `read_csv_auto` / `read_parquet`,
             enables `httpfs` for s3:// or http(s):// paths, then executes
             the SELECT. Same sqlglot SELECT-only guard applies (with the
             `duckdb` dialect).
  semantic — Adapter to `rag.semantic_engine.search_semantic`. Returns
             chunks shaped as rows so /run_query callers can treat vector
             retrieval like any other dataset.

Other kinds (odata / soql / rest / mongodb) are extension points — this
module raises `NotImplementedError`; dept-specific subclasses override.

Contract
--------
`execute(kind, source, query, row_limit, read_via=None)` returns:

    ExecutionResult(
        rows: List[Dict[str, Any]],   # always — empty on error
        error: Optional[str],         # human-readable failure reason
        elapsed_ms: int,              # wall-clock execution time
        sql_used: Optional[str],      # the (post-guard) SQL we ran
    )

It never raises for execution problems — callers branch on `result.error`.
It does raise `ValueError` for un-implemented kinds so the catalogue layer
can translate that into an HTTP 501.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    rows: List[Dict[str, Any]]
    error: Optional[str]
    elapsed_ms: int
    sql_used: Optional[str] = None


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


async def execute(
    kind: str,
    source: Dict[str, Any],
    query: str,
    row_limit: int = 50,
    read_via: Optional[Dict[str, Any]] = None,
) -> ExecutionResult:
    """Execute a read query against a dataset, dispatched by `kind`.

    `kind` is the string value of `models.DatasetKind`; using the string
    keeps this module importable without pulling in pydantic models in
    contexts where only the dispatcher is needed.
    """
    started = time.perf_counter()

    # sql / duckdb / bigquery / sap_rfc connectors are SYNCHRONOUS and
    # blocking. Running them directly on the event loop means one slow or
    # hung backend call freezes the whole MCP (every request, /health
    # included). Offload them to a worker thread so a stuck query only
    # ties up one thread. The odata/soql/rest/semantic branches are already
    # async (async connectors) and stay on the loop.
    if kind == "sql":
        return await _sync_guarded("sql", started, _run_sql, source, query, row_limit, started)
    if kind == "duckdb":
        return await _sync_guarded("duckdb", started, _run_duckdb, source, query, row_limit, read_via, started)
    if kind == "semantic":
        return await _run_semantic(source, query, row_limit, started)
    if kind == "odata":
        return await _run_odata(source, query, row_limit, read_via, started)
    if kind == "soql":
        return await _run_soql(source, query, row_limit, read_via, started)
    if kind == "rest":
        return await _run_rest(source, query, row_limit, read_via, started)
    if kind == "bigquery":
        return await _sync_guarded("bigquery", started, _run_bigquery, source, query, row_limit, started)
    if kind == "sap_rfc":
        return await _sync_guarded("sap_rfc", started, _run_sap_rfc, source, query, read_via, row_limit, started)
    raise ValueError(f"query_engine.execute: kind {kind!r} is not implemented here")


async def _sync_guarded(kind: str, started: float, fn, *args) -> ExecutionResult:
    """Run a synchronous connector branch in a worker thread with the SAME
    surfacing contract the async branches honor: a config/backend/import error
    is LOGGED and returned as ``result.error`` — it must never escape and crash
    the shared execution thread (RULE #1: fail loud, but as a clean error-status,
    not an unhandled exception on the event loop)."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as exc:
        logger.error("[query_engine] %s execution failed: %s", kind, exc)
        return ExecutionResult(
            rows=[], error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )


# ---------------------------------------------------------------------------
# Extension stubs — odata / soql / rest
# ---------------------------------------------------------------------------
#
# These branches let the dispatcher accept the four remaining catalogue
# `DatasetKind` values without raising. Each one delegates to a connector
# placeholder that raises ``NotImplementedError``; the branch catches that
# and returns a sentinel ``ExecutionResult`` with ``error="not_implemented"``
# so the orchestrator can surface a clean 501-style failure to the caller.
#
# Dept-specific subclasses replace the connector module (or monkey-patch
# the function) to enable the real backend.


def _not_implemented_result(kind: str, started: float) -> ExecutionResult:
    return ExecutionResult(
        rows=[],
        error="not_implemented",
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        sql_used=None,
    )


async def _run_odata(
    source: Dict[str, Any],
    query: Any,
    row_limit: int,
    read_via: Optional[Dict[str, Any]],
    started: float,
) -> ExecutionResult:
    try:
        from connectors import odata_connector  # type: ignore
        rows, error = await odata_connector.execute_odata(
            source.get("connection") or {}, query, read_via or {}, row_limit=row_limit
        )
        return ExecutionResult(
            rows=rows, error=error,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )
    except NotImplementedError:
        return _not_implemented_result("odata", started)
    except Exception as exc:
        # Contract: execution problems surface as result.error, never escape.
        logger.error("[query_engine] odata execution failed: %s", exc)
        return ExecutionResult(
            rows=[], error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )


async def _run_soql(
    source: Dict[str, Any],
    query: Any,
    row_limit: int,
    read_via: Optional[Dict[str, Any]],
    started: float,
) -> ExecutionResult:
    try:
        from connectors import soql_connector  # type: ignore
        rows, error = await soql_connector.execute_soql(
            source.get("connection") or {}, query, row_limit=row_limit
        )
        return ExecutionResult(
            rows=rows, error=error,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )
    except NotImplementedError:
        return _not_implemented_result("soql", started)
    except Exception as exc:
        # Contract: execution problems surface as result.error, never escape.
        logger.error("[query_engine] soql execution failed: %s", exc)
        return ExecutionResult(
            rows=[], error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )


async def _run_rest(
    source: Dict[str, Any],
    query: Any,
    row_limit: int,
    read_via: Optional[Dict[str, Any]],
    started: float,
) -> ExecutionResult:
    try:
        from connectors import rest_connector  # type: ignore
        rows, error = await rest_connector.execute_rest(
            source.get("connection") or {}, query, read_via or {}, row_limit=row_limit
        )
        return ExecutionResult(
            rows=rows, error=error,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )
    except NotImplementedError:
        return _not_implemented_result("rest", started)
    except Exception as exc:
        # Contract: execution problems surface as result.error, never escape.
        logger.error("[query_engine] rest execution failed: %s", exc)
        return ExecutionResult(
            rows=[], error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )


# ---------------------------------------------------------------------------
# SQL — delegate to the existing connector
# ---------------------------------------------------------------------------


def _run_sql(
    source: Dict[str, Any],
    query: str,
    row_limit: int,
    started: float,
) -> ExecutionResult:
    from connectors import sql_connector  # local import keeps cold-start cheap

    rows, error = sql_connector.execute_sql(
        source.get("connection") or {}, query, row_limit=row_limit
    )
    return ExecutionResult(
        rows=rows,
        error=error,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        sql_used=query,
    )


# ---------------------------------------------------------------------------
# DuckDB — query Excel / CSV / Parquet files in place
# ---------------------------------------------------------------------------


_DUCKDB_FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER",
    "CREATE", "ATTACH", "DETACH", "PRAGMA", "COPY", "EXPORT",
    "IMPORT", "INSTALL", "LOAD", "SET", "RESET", "CALL",
)


def _enforce_select_only_duckdb(sql: str, row_limit: int) -> Tuple[str, Optional[str]]:
    """SELECT-only + LIMIT wrap for DuckDB queries.

    Mirrors `sql_connector._enforce_readonly_and_limit` but pinned to the
    `duckdb` sqlglot dialect so DuckDB-native syntax (e.g. list comprehensions,
    `read_*` table functions in the FROM clause) parses cleanly. The wrap
    `SELECT * FROM (<query>) AS _capped LIMIT n` defends against any LIMIT
    bypass the user-controlled body might attempt.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "", "Empty SQL"
    if ";" in stripped:
        return "", "Multi-statement SQL is not allowed"

    try:
        import sqlglot  # type: ignore
        from sqlglot import expressions as sg_exp  # type: ignore

        try:
            parsed = sqlglot.parse(stripped, read="duckdb")
        except Exception as exc:
            return "", f"SQL parse error: {exc}"

        if len(parsed) != 1 or parsed[0] is None:
            return "", "Exactly one SELECT statement is required"

        tree = parsed[0]
        if not isinstance(tree, (sg_exp.Select, sg_exp.Union, sg_exp.Intersect, sg_exp.Except)):
            return "", f"Only SELECT statements are allowed (got {type(tree).__name__})"

        forbidden_types = tuple(
            cls for cls in (
                getattr(sg_exp, "Insert", None),
                getattr(sg_exp, "Update", None),
                getattr(sg_exp, "Delete", None),
                getattr(sg_exp, "Drop", None),
                getattr(sg_exp, "AlterTable", None),
                getattr(sg_exp, "Alter", None),
                getattr(sg_exp, "Create", None),
                getattr(sg_exp, "Merge", None),
                getattr(sg_exp, "Command", None),
                getattr(sg_exp, "TruncateTable", None),
            ) if cls is not None
        )
        for node in tree.walk():
            n = node[0] if isinstance(node, tuple) else node
            if isinstance(n, forbidden_types):
                return "", f"Forbidden statement type in query: {type(n).__name__}"

        return f"SELECT * FROM ({stripped}) AS _capped LIMIT {int(row_limit)}", None

    except ImportError:
        # Regex fallback when sqlglot isn't available.
        upper = stripped.upper()
        first_tok = upper.split(None, 1)[0] if upper.split() else ""
        if first_tok not in ("SELECT", "WITH"):
            return "", f"Only SELECT/WITH queries are allowed (got {first_tok!r})"
        import re as _re
        for kw in _DUCKDB_FORBIDDEN:
            if _re.search(rf"\b{kw}\b", upper):
                return "", f"Forbidden keyword in query: {kw}"
        if "LIMIT" not in upper:
            stripped = f"{stripped} LIMIT {int(row_limit)}"
        return stripped, None


def _file_table_function(path: str) -> str:
    """Pick the right DuckDB table function based on the file extension.

    Bare paths default to CSV — DuckDB's `read_csv_auto` is forgiving and
    will tell us at execution time if the file is the wrong shape.
    """
    p = path.lower().split("?", 1)[0]  # strip query string for s3-presigned URLs
    if p.endswith((".xlsx", ".xls", ".xlsm")):
        # `read_xlsx` accepts named arguments; default sheet = first.
        return "read_xlsx"
    if p.endswith(".parquet"):
        return "read_parquet"
    return "read_csv_auto"


def _needs_httpfs(files: List[str]) -> bool:
    return any(
        f.startswith(("s3://", "http://", "https://", "gs://", "azure://"))
        for f in files
    )


def _apply_duckdb_tuning(con: Any) -> None:
    """Apply DUCKDB_* env tuning to a fresh in-process connection.

    Same knobs the sandbox's `citra_toolkit.sql.session()` reads, so the
    dept-MCP query plane behaves the same way as the agent's interactive
    DuckDB. Required for SAP-scale aggregations — without temp_directory
    DuckDB cannot spill and OOMs on any working set > RAM.

    Defaults intentionally conservative: the dept-MCP runs on shared
    infrastructure (one process per dept) and a runaway query should
    not destabilise the host. Override per-deployment via env.
    """
    settings = [
        ("memory_limit", os.getenv("DUCKDB_MEMORY_LIMIT", "2GB")),
        ("threads", os.getenv("DUCKDB_THREADS", "4")),
        # /tmp is a real disk in this service (not tmpfs) so we use it
        # by default. Set DUCKDB_TEMP_DIRECTORY to override when the
        # operator wants spill on a faster mount.
        ("temp_directory", os.getenv("DUCKDB_TEMP_DIRECTORY", "/tmp/duckdb")),
        ("max_temp_directory_size", os.getenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "10GB")),
    ]
    for key, value in settings:
        if not value:
            continue
        try:
            if key == "temp_directory":
                os.makedirs(value, exist_ok=True)
            safe = str(value).replace("'", "''")
            con.execute(f"SET {key} = '{safe}'")
        except Exception as exc:  # noqa: BLE001
            logger.info("[duckdb] SET %s failed (%s) — skipping", key, exc)


def _run_duckdb(
    source: Dict[str, Any],
    query: str,
    row_limit: int,
    read_via: Optional[Dict[str, Any]],
    started: float,
) -> ExecutionResult:
    """In-memory DuckDB query against file-backed datasets.

    `read_via` shape (from the catalogue document):

        {
          "kind": "duckdb",
          "table": "farmer_loans",         # logical name agent will SELECT from
          "files": [
            "s3://citra-files/ops/farmer_loans_2024.xlsx",
            "/data/ops/farmer_loans_2025.csv"
          ],
          "extra": { "sheet": "Sheet1" }   # optional, passed to read_xlsx
        }
    """
    safe_sql, err = _enforce_select_only_duckdb(query, row_limit)
    if err is not None:
        logger.warning(f"⚠️ [DUCKDB] Rejected query: {err}")
        return ExecutionResult(
            rows=[], error=err,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )

    rv = read_via or {}
    files = rv.get("files") or []
    table = rv.get("table") or "data"
    extra = rv.get("extra") or {}

    if not files:
        return ExecutionResult(
            rows=[], error="kind=duckdb requires read_via.files[] in the catalogue",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )

    try:
        import duckdb  # type: ignore
    except ImportError:
        return ExecutionResult(
            rows=[], error="duckdb not installed on this MCP deployment",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=None,
        )

    try:
        con = duckdb.connect(database=":memory:")
        _apply_duckdb_tuning(con)
        if _needs_httpfs(files):
            try:
                con.execute("INSTALL httpfs; LOAD httpfs;")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"⚠️ [DUCKDB] httpfs load failed: {exc}")
            # Best-effort credential pass-through from process env.
            # S3 / S3-compatible
            for env_key, duckdb_setting in (
                ("AWS_ACCESS_KEY_ID", "s3_access_key_id"),
                ("AWS_SECRET_ACCESS_KEY", "s3_secret_access_key"),
                ("AWS_SESSION_TOKEN", "s3_session_token"),
                ("AWS_REGION", "s3_region"),
            ):
                v = os.getenv(env_key)
                if v:
                    try:
                        con.execute(f"SET {duckdb_setting} = ?", [v])
                    except Exception:
                        pass
            # GCS via HMAC keys (DuckDB httpfs treats gs:// like s3:// with
            # endpoint storage.googleapis.com). The dept's GCS HMAC creds
            # live in env as GCS_HMAC_ACCESS_KEY_ID + GCS_HMAC_SECRET so
            # the SAME duckdb connection can hit s3://, gs://, and https://.
            #
            # Demo override: DUCKDB_GCS_ENDPOINT points DuckDB at a fake-gcs-
            # server / minio / any GCS-compatible emulator instead of the
            # real storage.googleapis.com. When set we also enable URL_STYLE
            # path + USE_SSL false so the request shape matches what
            # fake-gcs-server expects on plain HTTP.
            if any(f.startswith("gs://") for f in files):
                gcs_key = os.getenv("GCS_HMAC_ACCESS_KEY_ID")
                gcs_sec = os.getenv("GCS_HMAC_SECRET")
                gcs_endpoint = os.getenv("DUCKDB_GCS_ENDPOINT", "").strip()
                if gcs_key and gcs_sec:
                    try:
                        # Build the CREATE SECRET DDL. Parameter binding
                        # doesn't work for SECRET keys/values — they're
                        # parsed at planning time. Quote-escape defensively.
                        kv = [
                            ("KEY_ID", gcs_key),
                            ("SECRET", gcs_sec),
                        ]
                        if gcs_endpoint:
                            kv.append(("ENDPOINT", gcs_endpoint))
                            kv.append(("URL_STYLE", "path"))
                            kv.append(("USE_SSL", "false"))
                        body = ", ".join(
                            f"{k} '{str(v).replace(chr(39), chr(39)*2)}'" if k != "USE_SSL"
                            else f"{k} {v}"
                            for k, v in kv
                        )
                        con.execute(
                            f"CREATE OR REPLACE SECRET gcs_secret (TYPE GCS, {body})"
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"⚠️ [DUCKDB] GCS secret install failed (falling back to ADC): {exc}"
                        )
                else:
                    logger.info(
                        "[DUCKDB] gs:// path without GCS_HMAC_* env — relying on "
                        "DuckDB's anonymous / public-bucket access"
                    )

        # Register the files as a single view named `table`. UNION ALL across
        # files lets a dataset span multiple monthly/yearly drops.
        fn = _file_table_function(files[0])
        if fn == "read_xlsx":
            try:
                con.execute("INSTALL excel; LOAD excel;")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"⚠️ [DUCKDB] excel extension load failed: {exc}")
            sheet = extra.get("sheet")
            sheet_arg = f", sheet = '{sheet}'" if sheet else ""
            unions = " UNION ALL ".join(
                f"SELECT * FROM read_xlsx('{f}'{sheet_arg})" for f in files
            )
        else:
            unions = " UNION ALL ".join(
                f"SELECT * FROM {fn}('{f}')" for f in files
            )

        # Sanitise the table identifier — only word chars allowed.
        import re as _re
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            return ExecutionResult(
                rows=[], error=f"Invalid table identifier: {table!r}",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                sql_used=None,
            )
        con.execute(f"CREATE VIEW {table} AS {unions}")

        cur = con.execute(safe_sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(row_limit)]
        return ExecutionResult(
            rows=rows, error=None,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=safe_sql,
        )
    except Exception as exc:
        snippet = (safe_sql[:300] + "…") if len(safe_sql) > 300 else safe_sql
        logger.error(f"❌ [DUCKDB] Execution failed: {exc}\nSQL: {snippet}")
        return ExecutionResult(
            rows=[], error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            sql_used=safe_sql,
        )


# ---------------------------------------------------------------------------
# Semantic — adapter for /run_query callers who want vector hits as rows
# ---------------------------------------------------------------------------


async def _run_semantic(
    source: Dict[str, Any],
    query: str,
    row_limit: int,
    started: float,
) -> ExecutionResult:
    # RAG short-circuit (pure disconnect): the MCP serves NO semantic — it is
    # answered by the Citra-Service platform reader. Semantic sources are dropped
    # at load, so this executor branch is unreachable in practice; if a stale
    # binding reaches it, fail loud (surfaced as result.error) rather than run RAG.
    logger.error(
        "[query_engine] semantic execution requested for source=%s — the MCP no "
        "longer serves RAG; route to Citra-Service /semantic/search",
        source.get("source_id"))
    return ExecutionResult(
        rows=[], error="semantic_not_served_by_mcp: RAG is answered by the Citra "
                       "platform reader, not the dept-MCP",
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        sql_used=None,
    )


# ---------------------------------------------------------------------------
# BigQuery — Google Cloud BigQuery via google-cloud-bigquery
# ---------------------------------------------------------------------------


def _run_bigquery(
    source: Dict[str, Any],
    query: str,
    row_limit: int,
    started: float,
) -> ExecutionResult:
    """Dispatch a SELECT to a BigQuery dataset.

    The connector enforces SELECT-only + LIMIT wrap, caps
    ``maximum_bytes_billed`` from ``source.connection.max_bytes_billed``,
    and times out after ``source.options.query_timeout_seconds`` (default 60).
    """
    try:
        from connectors import bigquery_connector  # type: ignore
    except ImportError:
        return _not_implemented_result("bigquery", started)

    timeout = int((source.get("options") or {}).get("query_timeout_seconds") or 60)
    rows, error = bigquery_connector.execute_bigquery(
        source.get("connection") or {},
        query,
        row_limit=row_limit,
        timeout_seconds=timeout,
    )
    return ExecutionResult(
        rows=rows, error=error,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        sql_used=query,
    )


# ---------------------------------------------------------------------------
# SAP RFC — SAP NetWeaver RFC / BAPI via pyrfc (optional)
# ---------------------------------------------------------------------------


def _run_sap_rfc(
    source: Dict[str, Any],
    query: Any,
    read_via: Optional[Dict[str, Any]],
    row_limit: int,
    started: float,
) -> ExecutionResult:
    """Dispatch an RFC call to a SAP NetWeaver system.

    ``query`` is interpreted as a dict ``{function, parameters}`` (an RFC
    function module name + the IMPORT/CHANGING parameters). ``read_via``
    may carry ``{table}`` when the agent wants to pull a transparent
    table via RFC_READ_TABLE; the connector exposes that as a special-
    cased path.

    Requires the SAP NW RFC SDK on the host AND ``pyrfc`` Python bindings
    — neither ships with this template. The connector raises an actionable
    error if either is missing.
    """
    try:
        from connectors import sap_rfc_connector  # type: ignore
    except ImportError:
        return _not_implemented_result("sap_rfc", started)

    rows, error = sap_rfc_connector.execute_sap_rfc(
        source.get("connection") or {},
        query,
        read_via or {},
        row_limit=row_limit,
    )
    return ExecutionResult(
        rows=rows, error=error,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        sql_used=None,
    )
