# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
SQL Connector
=============
Connects to Postgres / SQL Server / MySQL via SQLAlchemy.
Provides schema extraction (for ingestion) and SQL execution (for query time).

Credentials are NEVER stored in code or sources.yaml — they come exclusively
from environment variables using the env_prefix defined in sources.yaml.
e.g. env_prefix=MANDI → reads MANDI_HOST, MANDI_PORT, MANDI_DB, MANDI_USER, MANDI_PASS
"""

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Columns with this many or fewer distinct values get their values listed in the schema record.
# This lets the LLM write correct WHERE clauses without guessing categorical values.
CARDINALITY_THRESHOLD = 30

# M7: per-query statement timeout (ms) so a runaway scan can't hold a pool
# connection open indefinitely (the pool is only 2 + 4 overflow).
_STATEMENT_TIMEOUT_MS = int(os.getenv("SQL_STATEMENT_TIMEOUT_MS", "30000"))
# H1: skip per-column cardinality/range scans on tables larger than this — a
# schema describe must never trigger ~N full scans against a large source table.
_ENRICH_MAX_ROWS = int(os.getenv("SQL_ENRICH_MAX_ROWS", "500000"))


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _build_url(conn_config: Dict[str, Any]) -> str:
    db_type: str = conn_config["type"].lower()
    prefix: str = conn_config.get("env_prefix", "").upper()

    # host/db are REQUIRED for networked DBs — no silent localhost/empty
    # default that would point a prod source at a dev/local database.
    # (sqlite is file-based and resolves its path in its own branch below.)
    if db_type != "sqlite":
        from citra_service_utils import require_env
        host = require_env(f"{prefix}_HOST")
        db = require_env(f"{prefix}_DB")
    else:
        host = os.getenv(f"{prefix}_HOST", "")
        db = os.getenv(f"{prefix}_DB", "")
    port_str = os.getenv(f"{prefix}_PORT", "")
    user = os.getenv(f"{prefix}_USER", "")
    password = os.getenv(f"{prefix}_PASS", "")

    if db_type == "postgres":
        port = port_str or "5432"
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    elif db_type == "sqlserver":
        port = port_str or "1433"
        return (
            f"mssql+pyodbc://{user}:{password}@{host}:{port}/{db}"
            f"?driver=ODBC+Driver+17+for+SQL+Server"
        )
    elif db_type == "mysql":
        port = port_str or "3306"
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"
    elif db_type == "sqlite":
        path = os.getenv(f"{prefix}_PATH", db or ":memory:")
        return f"sqlite:///{path}"
    else:
        raise ValueError(f"Unsupported DB type: {db_type!r}")


# Module-level engine cache. Without this, every call to _get_engine() built a
# brand-new Engine (and thus a brand-new connection pool) that was never reused
# and never disposed — so the configured pool_size/max_overflow meant nothing and
# connections leaked. Memoize by the resolved URL + the timeout-bearing connect
# args so engines are reused across all functions for the same source config.
_ENGINE_CACHE: Dict[str, Any] = {}


def _get_engine(conn_config: Dict[str, Any]):
    """Create (or reuse) a SQLAlchemy engine for this connection config.

    Engines (and their pools) are memoized in a module-level dict keyed by the
    resolved SQLAlchemy URL + key options so the pool is actually reused across
    calls instead of leaking a fresh pool per call.
    """
    from sqlalchemy import create_engine
    url = _build_url(conn_config)
    # M7: a per-query statement_timeout so a runaway COUNT/scan can't pin a pool
    # connection (dialect-specific; Postgres via connect options).
    db_type = (conn_config.get("type") or "").lower()
    connect_args: Dict[str, Any] = {}
    if db_type == "postgres":
        connect_args = {"options": f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}"}

    cache_key = f"{url}||{db_type}||{_STATEMENT_TIMEOUT_MS}"
    cached = _ENGINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # pool_pre_ping ensures stale connections are recycled. sqlite uses
    # SingletonThreadPool, which does NOT accept pool_size/max_overflow
    # (QueuePool args) — pass them only for the server backends.
    #
    # §1 sizing: the pool is per (source, process). The old hard-coded 2+4=6
    # capacity meant the 7th concurrent query on a source queued for up to
    # pool_timeout. Size from settings (DB_POOL_SIZE / DB_MAX_OVERFLOW) so it
    # tracks the per-instance /query concurrency — and keep the to_thread
    # executor at least this large (see config.thread_pool_max_workers) so a
    # checked-out connection always has a worker thread to run its query on.
    engine_kwargs: Dict[str, Any] = {"pool_pre_ping": True, "connect_args": connect_args}
    if db_type != "sqlite":
        from config import get_settings
        cfg = get_settings()
        engine_kwargs["pool_size"] = max(1, int(cfg.db_pool_size))
        engine_kwargs["max_overflow"] = max(0, int(cfg.db_max_overflow))
        engine_kwargs["pool_timeout"] = max(1, int(cfg.db_pool_timeout))
        engine_kwargs["pool_recycle"] = int(cfg.db_pool_recycle)
    engine = create_engine(url, **engine_kwargs)
    _ENGINE_CACHE[cache_key] = engine
    return engine


# ---------------------------------------------------------------------------
# Schema extraction — used during ingestion
# ---------------------------------------------------------------------------

def extract_table_names(conn_config: Dict[str, Any]) -> List[str]:
    """Auto-discover all user tables if sources.yaml doesn't list them."""
    from sqlalchemy import inspect
    engine = _get_engine(conn_config)
    insp = inspect(engine)
    return insp.get_table_names()


def _enrich_column(
    conn,
    table_name: str,
    col: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enrich a column dict with cardinality hints (best-effort — skipped on error).

    - String-like columns with <= CARDINALITY_THRESHOLD distinct values get
      a "distinct_values" list so the LLM knows exact filter values.
    - Numeric and date columns get a "range" dict {min, max} so the LLM
      can write sensible range predicates.

    Takes an ALREADY-OPEN connection (`conn`) so the caller can enrich all
    columns of a table over ONE shared connection instead of opening (and
    leaking back to the pool) one connection per column — the old N+1 pattern.
    Enrichment is best-effort: a single column's failure is logged at WARNING
    and skipped, but it must not abort the whole describe.
    """
    col_name = col["name"]
    col_type_str = col.get("type", "").upper()

    is_string = any(t in col_type_str for t in ("CHAR", "TEXT", "ENUM", "CLOB"))
    is_numeric = any(t in col_type_str for t in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL", "MONEY"))
    is_date = any(t in col_type_str for t in ("DATE", "TIME", "TIMESTAMP"))

    try:
        from sqlalchemy import text as sqla_text
        if is_string:
            count_sql = f'SELECT COUNT(DISTINCT "{col_name}") FROM "{table_name}"'
            count = conn.execute(sqla_text(count_sql)).scalar() or 0
            if 0 < count <= CARDINALITY_THRESHOLD:
                vals_sql = (
                    f'SELECT DISTINCT "{col_name}" FROM "{table_name}" '
                    f'WHERE "{col_name}" IS NOT NULL LIMIT 50'
                )
                rows = conn.execute(sqla_text(vals_sql)).fetchall()
                col["distinct_values"] = [str(r[0]) for r in rows if r[0] is not None]
        elif is_numeric or is_date:
            range_sql = f'SELECT MIN("{col_name}"), MAX("{col_name}") FROM "{table_name}"'
            row = conn.execute(sqla_text(range_sql)).fetchone()
            if row and row[0] is not None:
                col["range"] = {"min": str(row[0]), "max": str(row[1])}
    except Exception as exc:
        logger.warning(f"⚠️ [SQL] Column enrichment skipped for {table_name}.{col_name!r}: {exc}")

    return col


def _apply_session_timeout(conn, db_type: str) -> None:
    """Best-effort per-session statement timeout for backends that don't take
    it via connect_args.

    Postgres is handled at engine-build time (connect_args statement_timeout).
    MySQL/MariaDB support `SET SESSION max_execution_time` (milliseconds);
    SQL Server has no per-session statement timeout (LOCK_TIMEOUT only covers
    lock waits, and SET QUERY_GOVERNOR_COST_LIMIT is estimate-based), so it is
    intentionally left unset here. Failures are logged and ignored — a missing
    session-level cap must not break the actual query.
    """
    from sqlalchemy import text as sqla_text
    try:
        if db_type in ("mysql", "mariadb"):
            conn.execute(sqla_text(f"SET SESSION max_execution_time={int(_STATEMENT_TIMEOUT_MS)}"))
        # sqlserver: no clean per-session statement timeout — see docstring.
    except Exception as exc:
        logger.warning("⚠️ [SQL] Could not set session statement timeout (%s): %s", db_type, exc)


def _estimate_row_count(engine, db_type: str, table_name: str) -> int:
    """H1: cheap row-count ESTIMATE from the engine's catalogue stats (no scan).
    Falls back to COUNT(*) ONLY when no estimate is available (e.g. sqlite, or a
    never-analyzed Postgres table). Returns -1 if even that fails."""
    from sqlalchemy import text as sqla_text
    try:
        with engine.connect() as conn:
            if db_type == "postgres":
                r = conn.execute(
                    sqla_text("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t)"),
                    {"t": f'"{table_name}"'},
                ).scalar()
                if isinstance(r, int) and r > 0:
                    return int(r)
            elif db_type == "mysql":
                r = conn.execute(
                    sqla_text("SELECT TABLE_ROWS FROM information_schema.tables WHERE table_name = :t"),
                    {"t": table_name},
                ).scalar()
                if isinstance(r, int) and r >= 0:
                    return int(r)
            elif db_type == "sqlserver":
                r = conn.execute(
                    sqla_text("SELECT SUM(row_count) FROM sys.dm_db_partition_stats "
                              "WHERE object_id = OBJECT_ID(:t) AND index_id IN (0,1)"),
                    {"t": table_name},
                ).scalar()
                if isinstance(r, int) and r >= 0:
                    return int(r)
            # No estimate path (sqlite / unanalyzed / 0) → exact COUNT(*) fallback.
            return conn.execute(sqla_text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0
    except Exception as exc:
        logger.warning("[SQL] row-count estimate failed for %s: %s", table_name, exc)
        return -1


def extract_schema(conn_config: Dict[str, Any], table_name: str) -> Dict[str, Any]:
    """
    Return column metadata for a table.

    Returns:
        {
          "table_name": str,
          "columns": [{"name": str, "type": str, "nullable": bool}],
          "row_count": int (approximate)
        }
    """
    from sqlalchemy import inspect, text
    engine = _get_engine(conn_config)
    insp = inspect(engine)

    raw_cols = insp.get_columns(table_name)
    columns = [
        {"name": col["name"], "type": str(col["type"]), "nullable": col.get("nullable", True)}
        for col in raw_cols
    ]

    # H1: cheap row-count ESTIMATE from engine stats — never a full COUNT(*)
    # scan on a large prod table just to render a schema panel.
    db_type = (conn_config.get("type") or "").lower()
    row_count = _estimate_row_count(engine, db_type, table_name)

    # H1: gate per-column cardinality/range enrichment by size. On a large table
    # this would fire ~N full COUNT(DISTINCT)/MIN-MAX scans — skip it there.
    if 0 <= row_count <= _ENRICH_MAX_ROWS:
        # N+1 fix: open ONE connection and reuse it across all columns instead
        # of one connection (1-2 queries) per column. Enrichment stays
        # best-effort per column inside _enrich_column.
        with engine.connect() as conn:
            _apply_session_timeout(conn, db_type)
            columns = [_enrich_column(conn, table_name, col) for col in columns]
    else:
        logger.info(
            "[SQL] %s ~%s rows (> enrich cap %s, or unknown) — skipping per-column "
            "cardinality/range scans to spare the source.",
            table_name, row_count, _ENRICH_MAX_ROWS,
        )

    return {"table_name": table_name, "columns": columns, "row_count": row_count}


def extract_sample_rows(
    conn_config: Dict[str, Any],
    table_name: str,
    n: int = 10,
) -> List[Dict[str, Any]]:
    """
    Return n representative rows from the table for LLM SQL prompt construction.
    Uses ORDER BY RANDOM() (Postgres) / NEWID() (SQL Server) for variety.
    """
    from sqlalchemy import text
    engine = _get_engine(conn_config)
    db_type = conn_config["type"].lower()

    if db_type == "sqlserver":
        order_clause = "ORDER BY NEWID()"
    elif db_type == "mysql":
        order_clause = "ORDER BY RAND()"
    else:
        order_clause = "ORDER BY RANDOM()"

    sql = f'SELECT * FROM "{table_name}" {order_clause} LIMIT {n}'

    # RULE #1 fail-loud: a failure here used to return [] — indistinguishable
    # from a legitimately empty table. Log and re-raise so the caller knows the
    # sample could not be taken.
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            keys = list(result.keys())
            return [dict(zip(keys, row)) for row in result.fetchall()]
    except Exception as exc:
        logger.error(f"❌ [SQL] Could not fetch sample rows from {table_name!r}: {exc}")
        raise


def extract_foreign_keys(
    conn_config: Dict[str, Any],
    table_name: str,
) -> List[Dict[str, Any]]:
    """
    Return FK relationships defined on a table.

    Returns:
        List of dicts: [{from_table, from_column, to_table, to_column}]

    Note: Only returns FKs where the DB has explicit FK constraints defined.
    If the schema uses logical (un-constrained) FKs, add them manually to
    sources.yaml under a `relationships` key (future enhancement).
    """
    from sqlalchemy import inspect
    engine = _get_engine(conn_config)
    insp = inspect(engine)

    # RULE #1 fail-loud: returning [] on error is indistinguishable from "this
    # table genuinely has no FK constraints". Log and re-raise instead.
    fks = []
    try:
        raw_fks = insp.get_foreign_keys(table_name)
    except Exception as exc:
        logger.error(f"❌ [SQL] FK extraction failed for {table_name!r}: {exc}")
        raise
    for fk in raw_fks:
        referred_table = fk.get("referred_table", "")
        constrained_cols = fk.get("constrained_columns", [])
        referred_cols = fk.get("referred_columns", [])
        for from_col, to_col in zip(constrained_cols, referred_cols):
            fks.append({
                "from_table": table_name,
                "from_column": from_col,
                "to_table": referred_table,
                "to_column": to_col,
            })

    return fks


def extract_primary_keys(conn_config: Dict[str, Any], table_name: str) -> List[str]:
    """
    Return the list of primary key column names for a table.
    Empty list if the table has no PK constraint (or detection fails).
    """
    from sqlalchemy import inspect
    engine = _get_engine(conn_config)
    insp = inspect(engine)
    # RULE #1 fail-loud: returning [] on error is indistinguishable from "no PK
    # constraint" (which legitimately returns []). Log and re-raise on failure.
    try:
        pk_info = insp.get_pk_constraint(table_name)
    except Exception as exc:
        logger.error(f"❌ [SQL] PK detection failed for {table_name!r}: {exc}")
        raise
    return pk_info.get("constrained_columns", [])


def stable_row_id(source_id: str, table_name: str, row: Dict[str, Any], pk_cols: List[str]) -> str:
    """
    Compute a stable, deterministic Milvus ID for a data row.

    - PK available: key = source_id + table_name + sorted PK values  (fast, immune to column reordering)
    - No PK:        key = source_id + table_name + full JSON of row   (content-addressed fallback)

    Same row → same ID → Milvus upsert is a no-op. Changed row → same ID → vector updated.
    Deleted row → ID disappears from current_ids set → _delete_orphan_rows removes it.
    """
    if pk_cols:
        pk_vals = {col: str(row.get(col, "")) for col in sorted(pk_cols)}
        key = f"{source_id}__{table_name}__{json.dumps(pk_vals, sort_keys=True)}"
    else:
        key = f"{source_id}__{table_name}__{json.dumps(row, sort_keys=True, default=str)}"
    return hashlib.sha256(key.encode()).hexdigest()[:50]


def fetch_all_pk_values(
    source_id: str,
    conn_config: Dict[str, Any],
    table_name: str,
    pk_cols: List[str],
) -> Set[str]:
    """
    Return the set of stable row IDs for ALL current rows in a table.
    Used by _delete_orphan_rows to find rows deleted from the source DB.

    - If pk_cols non-empty: SELECT only PK columns (lightweight scan, no data transfer).
    - If pk_cols empty: returns empty set — orphan deletion is skipped for this table.
      Dept IT should declare pk_columns in sources.yaml for tables without DB-level PK constraints.
    """
    if not pk_cols:
        logger.warning(
            f"⚠️ [SQL] No PK cols known for {table_name!r} — orphan row deletion skipped. "
            f"Declare 'pk_columns' in sources.yaml to enable it."
        )
        return set()

    from sqlalchemy import text
    engine = _get_engine(conn_config)
    quoted_cols = ", ".join(f'"{c}"' for c in pk_cols)
    sql = f'SELECT {quoted_cols} FROM "{table_name}"'

    # RULE #1 fail-loud: this is the DANGEROUS one. A returned set() on error is
    # indistinguishable from "the table is now empty" and drives
    # _delete_orphan_rows to wipe EVERY ingested row for this table. Never
    # swallow — log and re-raise so orphan deletion aborts on a scan failure.
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            keys = list(result.keys())
            ids: Set[str] = set()
            for row_tuple in result:
                row_dict = dict(zip(keys, row_tuple))
                ids.add(stable_row_id(source_id, table_name, row_dict, pk_cols))
        logger.info(f"📊 [SQL] PK scan complete for {table_name!r}: {len(ids):,} row(s)")
        return ids
    except Exception as exc:
        logger.error(f"❌ [SQL] PK scan failed for {table_name!r}: {exc}")
        raise


# ---------------------------------------------------------------------------
# SQL execution — used at query time by structured_engine.py
# ---------------------------------------------------------------------------

def execute_sql(
    conn_config: Dict[str, Any],
    sql: str,
    row_limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Execute a SQL query and return (rows, error).

    Hardening:
      1. SELECT-only enforcement (parses with sqlglot when available;
         falls back to a strict regex check). Rejects multi-statement,
         DML/DDL, and Postgres CTE-with-DML variants.
      2. Row-limit enforcement via sqlglot AST when available
         (wraps original SQL in `SELECT * FROM (<sql>) AS _capped LIMIT n`
         when no compatible LIMIT/TOP/FETCH is detected). Falls back to
         a substring check + fetchmany cap.
    Returns ([], error_message) on failure — never raises.
    """
    from sqlalchemy import text

    safe_sql, err = _enforce_readonly_and_limit(sql, row_limit, conn_config.get("type", "").lower())
    if err is not None:
        logger.warning(f"⚠️ [SQL] Rejected query: {err}")
        return [], err

    try:
        engine = _get_engine(conn_config)
        with engine.connect() as conn:
            result = conn.execute(text(safe_sql))
            keys = list(result.keys())
            rows = [dict(zip(keys, row)) for row in result.fetchmany(row_limit)]
        return rows, None
    except Exception as exc:
        # Truncate SQL in logs to avoid spilling potentially sensitive payloads.
        snippet = (safe_sql[:300] + "…") if len(safe_sql) > 300 else safe_sql
        logger.error(f"❌ [SQL] Execution failed: {exc}\nSQL: {snippet}")
        return [], str(exc)


# ---------------------------------------------------------------------------
# Read-only / limit enforcement helpers
# ---------------------------------------------------------------------------

# Stages that must NEVER appear as the top-level statement type, even inside
# a CTE. Postgres allows `WITH x AS (DELETE …) SELECT …`.
_FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER",
    "CREATE", "GRANT", "REVOKE", "MERGE", "CALL", "EXEC", "EXECUTE",
    "REPLACE", "RENAME", "ATTACH", "DETACH", "VACUUM", "ANALYZE",
    "COPY", "LOAD", "COMMENT", "SET", "RESET", "DO", "LOCK",
)


def _enforce_readonly_and_limit(
    sql: str,
    row_limit: int,
    db_type: str,
) -> Tuple[str, Optional[str]]:
    """Returns (safe_sql, error). On error returns ("", message)."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "", "Empty SQL"

    # Hard reject if the raw text contains a semicolon followed by more SQL.
    # (We already rstripped trailing ones; any remaining means multi-statement.)
    if ";" in stripped:
        return "", "Multi-statement SQL is not allowed"

    # ---- AST-based path (preferred) ---------------------------------------
    try:
        import sqlglot  # type: ignore
        from sqlglot import expressions as sg_exp  # type: ignore

        dialect = _sqlglot_dialect(db_type)
        try:
            parsed = sqlglot.parse(stripped, read=dialect)
        except Exception as exc:
            return "", f"SQL parse error: {exc}"

        if len(parsed) != 1 or parsed[0] is None:
            return "", "Exactly one SELECT statement is required"

        tree = parsed[0]

        # Top-level must be a SELECT (or UNION/INTERSECT/EXCEPT of SELECTs).
        if not isinstance(tree, (sg_exp.Select, sg_exp.Union, sg_exp.Intersect, sg_exp.Except)):
            return "", f"Only SELECT statements are allowed (got {type(tree).__name__})"

        # Walk the AST and reject any DML/DDL nodes anywhere (incl. CTEs).
        # sqlglot has renamed several statement nodes across versions
        # (e.g. AlterTable -> Alter in 25.x). Resolve the node classes
        # tolerantly via getattr so a minor sqlglot bump can never break
        # the SELECT-only guard with an AttributeError.
        _forbidden_names = (
            "Insert", "Update", "Delete", "Drop", "Alter", "AlterTable",
            "Create", "Merge", "Command", "TruncateTable", "Truncate",
        )
        forbidden_types = tuple(
            t for t in (getattr(sg_exp, _n, None) for _n in _forbidden_names)
            if t is not None
        )
        for node in tree.walk():
            n = node[0] if isinstance(node, tuple) else node
            if isinstance(n, forbidden_types):
                return "", f"Forbidden statement type in query: {type(n).__name__}"

        # Enforce LIMIT at the outer level — wrap if needed. Wrapping is the
        # safest cross-dialect approach and overrides any bypass via UNION/CTE.
        wrapped = f"SELECT * FROM ({stripped}) AS _capped LIMIT {int(row_limit)}"
        # SQL Server doesn't support LIMIT — use TOP via a subquery shape it accepts.
        if dialect == "tsql":
            wrapped = f"SELECT TOP {int(row_limit)} * FROM ({stripped}) AS _capped"
        return wrapped, None

    except ImportError:
        # ---- Fallback: regex-only guard -----------------------------------
        upper = stripped.upper()
        # First non-whitespace token must be SELECT or WITH
        first_tok = upper.split(None, 1)[0] if upper.split() else ""
        if first_tok not in ("SELECT", "WITH"):
            return "", f"Only SELECT/WITH queries are allowed (got {first_tok!r})"
        # Reject any forbidden keyword as a whole word.
        import re as _re
        for kw in _FORBIDDEN_KEYWORDS:
            if _re.search(rf"\b{kw}\b", upper):
                return "", f"Forbidden keyword in query: {kw}"
        if "LIMIT" not in upper and "TOP " not in upper and "FETCH " not in upper:
            stripped = f"{stripped} LIMIT {int(row_limit)}"
        return stripped, None


def _sqlglot_dialect(db_type: str) -> Optional[str]:
    if db_type in ("postgres", "postgresql"):
        return "postgres"
    if db_type in ("mysql", "mariadb"):
        return "mysql"
    if db_type in ("mssql", "sqlserver"):
        return "tsql"
    if db_type == "sqlite":
        return "sqlite"
    if db_type == "duckdb":
        return "duckdb"
    return None

