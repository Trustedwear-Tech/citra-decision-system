# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""SQL helpers for the Action Chat sandbox.

Postgres has been removed from the action-chat surface. The agent's
SQL surface is now DuckDB-only, with three callable shapes ordered by
preference:

1. ``sql.session()`` — the **primary** path. Returns a tuned, long-lived
   in-container ``duckdb.Connection`` (memory_limit, temp_directory,
   threads sized for SAP-scale analytics). The connection is a
   PROCESS-WIDE singleton so calls across turns share registered
   tables, views, and macros. Tables you ``CREATE`` survive every
   subsequent turn within the same session.
2. ``sql.duckdb_over_file(sql, key=...)`` — stateless server-side
   proxy over a single file in the user's bucket. Useful for the main
   agent's one-shot lookups (and for callers that explicitly do not
   want a persistent connection). Reloads bytes on every call.
3. ``sql.duckdb(sql, data=...)`` — same proxy with inline bytes
   (~64 MiB cap, results capped server-side).

Returns a ``pandas.DataFrame`` for ergonomic analysis from the proxy
calls. ``sql.session()`` returns a raw connection so the analyst can
mix SELECT / CREATE TABLE / register / EXPLAIN as needed.

For durable cross-turn structured state, use ``scratch.workspace.put``
(Mongo) or ``vector.upsert`` (Milvus). There is no per-user Postgres
in this image.
"""
from __future__ import annotations

import base64
import logging
import os
from threading import Lock
from typing import Any, Literal

from ._proxy import proxy_url
from .client import http_client


logger = logging.getLogger(__name__)


# ============================================================== session
# Process-wide singleton DuckDB connection. The Python kernel inside
# OpenClaw's code_execution survives across turns within a session, so a
# module-level cached connection is the right way to give the agent a
# stateful, tuned engine without it having to remember to plumb a `con`
# variable through every snippet.

_SESSION_CON: Any = None
_SESSION_LOCK = Lock()


def session(*, force_new: bool = False) -> Any:
    """Return the tuned, long-lived in-container DuckDB connection.

    Tuning applied on first open (idempotent across turns):

    - ``memory_limit`` from ``DUCKDB_MEMORY_LIMIT`` (default 4GB) —
      caps the heap so a huge GROUP BY doesn't OOM-kill the container.
    - ``threads`` from ``DUCKDB_THREADS`` (default 4) — parallelism
      across the container's vCPU quota.
    - ``temp_directory`` from ``DUCKDB_TEMP_DIRECTORY`` (default
      ``/workspace/.duckdb-temp``) — DuckDB spills hash tables / sorts
      here when the working set exceeds ``memory_limit``. Without this
      SAP-scale aggregations OOM. /tmp is too small (64M).
    - ``max_temp_directory_size`` from ``DUCKDB_MAX_TEMP_DIRECTORY_SIZE``
      (default 3GB) — guards against a runaway query exhausting the
      /workspace tmpfs.
    - ``INSTALL httpfs; LOAD httpfs;`` — so ``read_parquet('gs://...')``
      / ``read_parquet('s3://...')`` / ``read_parquet('https://...')``
      all work out of the box. GCS_HMAC_* / AWS_* env vars are passed
      through as SECRET if set.

    The session connection is cached for the life of the Python kernel.
    Tables, views, registrations, and prepared statements survive every
    subsequent ``sql.session()`` call within the same chat session.

    Use ``force_new=True`` if you genuinely want to throw away state and
    open a fresh connection (rare — usually a sign you should be
    namespacing your tables instead).
    """
    global _SESSION_CON
    with _SESSION_LOCK:
        if _SESSION_CON is not None and not force_new:
            return _SESSION_CON
        if _SESSION_CON is not None and force_new:
            try:
                _SESSION_CON.close()
            except Exception:  # noqa: BLE001
                pass
            _SESSION_CON = None

        try:
            import duckdb  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "duckdb is not installed inside the sandbox image — "
                "check infrastructure/action-sandbox/requirements.txt"
            ) from e

        con = duckdb.connect()
        _apply_tuning(con)
        _enable_httpfs(con)
        _SESSION_CON = con
        return con


def _apply_tuning(con: Any) -> None:
    """Apply DUCKDB_* env tuning to a fresh connection. Each SET is best-
    effort — older DuckDB builds may not know every key, in which case
    we log and continue rather than refusing the connection."""
    settings = [
        ("memory_limit", os.getenv("DUCKDB_MEMORY_LIMIT", "4GB")),
        ("threads", os.getenv("DUCKDB_THREADS", "4")),
        ("temp_directory", os.getenv("DUCKDB_TEMP_DIRECTORY", "/workspace/.duckdb-temp")),
        ("max_temp_directory_size", os.getenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "3GB")),
    ]
    for key, value in settings:
        if not value:
            continue
        try:
            # `?`-style parameter binding doesn't work for SET keys/values
            # in DuckDB — they're parsed as identifiers/literals at planning
            # time. Quote the value defensively against trivial injection.
            safe = str(value).replace("'", "''")
            con.execute(f"SET {key} = '{safe}'")
        except Exception as exc:  # noqa: BLE001
            logger.info("[sql.session] SET %s failed (%s) — skipping", key, exc)


def _enable_httpfs(con: Any) -> None:
    """Load httpfs and best-effort wire GCS / S3 credentials."""
    try:
        con.execute("INSTALL httpfs; LOAD httpfs;")
    except Exception as exc:  # noqa: BLE001
        logger.info("[sql.session] httpfs install/load failed — gs:// / s3:// disabled: %s", exc)
        return

    gcs_key = os.getenv("GCS_HMAC_ACCESS_KEY_ID")
    gcs_sec = os.getenv("GCS_HMAC_SECRET")
    if gcs_key and gcs_sec:
        try:
            safe_key = gcs_key.replace("'", "''")
            safe_sec = gcs_sec.replace("'", "''")
            con.execute(
                f"CREATE OR REPLACE SECRET gcs_secret (TYPE GCS, KEY_ID '{safe_key}', SECRET '{safe_sec}')"
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("[sql.session] GCS secret install failed: %s", exc)

    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_sec = os.getenv("AWS_SECRET_ACCESS_KEY")
    if aws_key and aws_sec:
        for env_key, duckdb_setting in (
            ("AWS_ACCESS_KEY_ID", "s3_access_key_id"),
            ("AWS_SECRET_ACCESS_KEY", "s3_secret_access_key"),
            ("AWS_SESSION_TOKEN", "s3_session_token"),
            ("AWS_REGION", "s3_region"),
        ):
            v = os.getenv(env_key)
            if not v:
                continue
            try:
                safe = v.replace("'", "''")
                con.execute(f"SET {duckdb_setting} = '{safe}'")
            except Exception:
                pass


def session_stats() -> dict[str, Any]:
    """Inspect the session connection's current memory / temp usage.

    Useful when the agent wants to decide whether to evict tables before
    loading another fact source. Returns a small dict; never raises.
    """
    con = session()
    out: dict[str, Any] = {}
    for key in ("memory_limit", "threads", "temp_directory", "max_temp_directory_size"):
        try:
            row = con.execute(f"SELECT current_setting('{key}')").fetchone()
            out[key] = row[0] if row else None
        except Exception:
            out[key] = None
    try:
        # database_size returns rows of (database_name, database_size, ...).
        rows = con.execute("PRAGMA database_size").fetchall()
        out["databases"] = [tuple(r) for r in rows]
    except Exception:
        pass
    return out


def duckdb(sql: str, *,
           data: bytes | None = None,
           data_b64: str | None = None,
           filename: str = "data.csv",
           format: Literal["csv", "json", "parquet"] = "csv",
           timeout_ms: int = 30000):
    """Run an inline DuckDB query over an in-memory blob the agent already has.

    Pass ``data`` (bytes) OR ``data_b64`` (string) — one of them. The blob is
    loaded as the table named ``data`` inside DuckDB. ``sql`` runs against
    that table. Results return as a ``pandas.DataFrame``.

    Server caps payload size and execution time. Writes (``CREATE``,
    ``INSERT`` against ``data``) are rejected — read-only.

    Useful for: PnL / cohort / aggregation against an Excel/CSV the user
    just uploaded. For larger or columnar data, prefer Parquet.
    """
    if data_b64 is None:
        if data is None:
            raise ValueError("provide data (bytes) or data_b64 (str)")
        data_b64 = base64.b64encode(data).decode("ascii")
    body = {
        "sql": sql,
        "format": format,
        "filename": filename,
        "content_b64": data_b64,
        "timeout_ms": int(timeout_ms),
    }
    with http_client(timeout=max(60.0, (timeout_ms / 1000.0) + 5.0)) as c:
        r = c.post(proxy_url("duckdb"), json=body)
        r.raise_for_status()
        payload = r.json() or {}

    columns = list(payload.get("columns") or [])
    rows = list(payload.get("rows") or [])
    import pandas as pd  # type: ignore
    df = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame(rows)

    try:
        from . import research as _research
        _research.auto_step(
            "sql.duckdb",
            params={"sql": sql[:120], "filename": filename, "rows": len(df)},
            summary=f"{len(df)} rows from {filename}",
        )
    except Exception:  # noqa: BLE001
        pass
    return df


def duckdb_raw(sql: str, *,
               data: bytes | None = None,
               data_b64: str | None = None,
               filename: str = "data.csv",
               format: Literal["csv", "json", "parquet"] = "csv",
               timeout_ms: int = 30000) -> dict[str, Any]:
    """Variant that returns the raw ``{columns, rows, ...}`` payload."""
    if data_b64 is None:
        if data is None:
            raise ValueError("provide data (bytes) or data_b64 (str)")
        data_b64 = base64.b64encode(data).decode("ascii")
    body = {
        "sql": sql,
        "format": format,
        "filename": filename,
        "content_b64": data_b64,
        "timeout_ms": int(timeout_ms),
    }
    with http_client(timeout=max(60.0, (timeout_ms / 1000.0) + 5.0)) as c:
        r = c.post(proxy_url("duckdb"), json=body)
        r.raise_for_status()
        return r.json() or {}


def duckdb_over_file(sql: str, *,
                     key: str,
                     filename: str | None = None,
                     format: Literal["csv", "json", "parquet"] = "csv",
                     timeout_ms: int = 60000):
    """Convenience wrapper: pull bytes from the user's bucket via
    ``files.get(key)`` and run DuckDB over them.

    For files larger than the inline cap (8 MiB), the agent should fetch
    them in chunks instead.
    """
    from . import files as _files
    blob = _files.get_bytes(key)
    return duckdb(
        sql,
        data=blob,
        filename=filename or (key.rsplit("/", 1)[-1] or "data.csv"),
        format=format,
        timeout_ms=timeout_ms,
    )


# Legacy aliases (don't break older skills that referenced ``sql.query``).
query = duckdb
query_raw = duckdb_raw
