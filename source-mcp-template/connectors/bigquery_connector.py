# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
BigQuery Connector
==================
Backs ``DatasetKind.bigquery`` for Google BigQuery datasets. Implements
the same surface as ``sql_connector`` so ``query_engine`` can dispatch
to it uniformly: schema extraction (ingest plane) + parameterised SELECT
execution (query plane).

Credential model
----------------
Credentials live ONLY in env vars referenced via ``connection.env_prefix``
(same pattern as sql_connector / odata_connector). The source YAML
defines:

    type:        bigquery
    env_prefix:  ACME_BQ
    project_id:  acme-analytics-prod        # optional override
    location:    US                          # optional, defaults to None
    default_dataset: sales                   # optional

Env vars consumed (per env_prefix):

    ACME_BQ_PROJECT          GCP project id (overrides connection.project_id)
    ACME_BQ_CREDENTIALS_JSON Service-account JSON (raw string) — optional
    ACME_BQ_CREDENTIALS_FILE Path to SA JSON on disk            — optional

If neither *_CREDENTIALS_* var is set the connector falls back to
Application Default Credentials (workload identity / gcloud login).

Hardening
---------
- SELECT-only enforcement via ``sqlglot`` (BigQuery dialect). Multi-
  statement requests rejected.
- ``row_limit`` applied as a wrap: ``SELECT * FROM (<sql>) LIMIT n``
  so the agent cannot bypass via UNION / CTEs.
- ``maximum_bytes_billed`` capped from ``connection.max_bytes_billed``
  (default 10 GB) to prevent a runaway scan from billing the customer.
- All queries run as ``QueryJobConfig(use_legacy_sql=False, dry_run=False)``
  with ``timeoutMs`` derived from the source's
  ``query_timeout_seconds``.

This connector is **optional**. ``google-cloud-bigquery`` is not in the
default ``requirements.txt`` — install ``google-cloud-bigquery>=3.21.0``
when you deploy a dept-MCP that exposes BigQuery datasets. The import
is lazy so the rest of the service starts cleanly even when BQ isn't
configured.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_DEFAULT_MAX_BYTES_BILLED = 10 * 1024 * 1024 * 1024  # 10 GB


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def _env(prefix: str, name: str, default: str = "") -> str:
    return os.getenv(f"{prefix}_{name}", default).strip()


def _resolve_connection(conn: Dict[str, Any]) -> Dict[str, Any]:
    prefix = (conn.get("env_prefix") or "").upper()
    if not prefix:
        raise ValueError("BigQuery connection: env_prefix is required")

    project_id = _env(prefix, "PROJECT") or str(conn.get("project_id") or "").strip()
    if not project_id:
        raise ValueError(f"BigQuery: {prefix}_PROJECT or connection.project_id required")

    return {
        "prefix": prefix,
        "project_id": project_id,
        "location": (conn.get("location") or "").strip() or None,
        "default_dataset": (conn.get("default_dataset") or "").strip() or None,
        "credentials_json": _env(prefix, "CREDENTIALS_JSON"),
        "credentials_file": _env(prefix, "CREDENTIALS_FILE"),
        "max_bytes_billed": int(conn.get("max_bytes_billed") or _DEFAULT_MAX_BYTES_BILLED),
    }


@lru_cache(maxsize=16)
def _client_for(prefix: str, project_id: str, location: Optional[str],
                cred_path: str, cred_json_hash: str):
    """Cache one BigQuery client per (env_prefix, project, credentials).

    The cred_json_hash arg is just to participate in cache key — the real
    credential material is re-resolved inside via the same env vars.

    BIGQUERY_EMULATOR_HOST override: when set, the client targets the
    emulator over plain HTTP with AnonymousCredentials. The official
    SDK respects BIGQUERY_EMULATOR_HOST for routing on its own — we
    add the explicit ClientOptions + AnonymousCredentials so it doesn't
    fall back to ADC discovery (which fails on a box without GCP creds).
    """
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-bigquery is not installed; add 'google-cloud-bigquery>=3.21.0' "
            "to source-mcp-template/requirements.txt for this deployment"
        ) from exc

    emulator_host = os.getenv("BIGQUERY_EMULATOR_HOST", "").strip()
    if emulator_host:
        from google.auth.credentials import AnonymousCredentials  # type: ignore
        from google.api_core.client_options import ClientOptions  # type: ignore
        endpoint = emulator_host if emulator_host.startswith("http") else f"http://{emulator_host}"
        return bigquery.Client(
            project=project_id,
            credentials=AnonymousCredentials(),
            location=location,
            client_options=ClientOptions(api_endpoint=endpoint),
        )

    credentials = None
    if cred_path:
        from google.oauth2 import service_account  # type: ignore
        credentials = service_account.Credentials.from_service_account_file(cred_path)
    elif cred_json_hash:
        # We stored a stable hash in the cache key; pull the raw JSON now.
        raw = os.getenv(f"{prefix}_CREDENTIALS_JSON", "").strip()
        if raw:
            from google.oauth2 import service_account  # type: ignore
            credentials = service_account.Credentials.from_service_account_info(json.loads(raw))

    return bigquery.Client(project=project_id, credentials=credentials, location=location)


def _get_client(conn_config: Dict[str, Any]):
    resolved = _resolve_connection(conn_config)
    # Hash credentials_json so equal-content callers share the client without
    # putting the secret in the cache key.
    import hashlib
    cred_hash = (
        hashlib.sha256(resolved["credentials_json"].encode()).hexdigest()[:16]
        if resolved["credentials_json"] else ""
    )
    return _client_for(
        resolved["prefix"],
        resolved["project_id"],
        resolved["location"],
        resolved["credentials_file"],
        cred_hash,
    ), resolved


# ---------------------------------------------------------------------------
# Schema extraction (ingest plane)
# ---------------------------------------------------------------------------


def extract_table_names(conn_config: Dict[str, Any]) -> List[str]:
    """List ``dataset.table`` names visible to the SA across all datasets
    in the project (or only ``default_dataset`` when set)."""
    client, resolved = _get_client(conn_config)
    out: List[str] = []
    if resolved["default_dataset"]:
        datasets = [client.dataset(resolved["default_dataset"])]
    else:
        datasets = list(client.list_datasets(resolved["project_id"]))
    for ds_ref in datasets:
        try:
            for tbl in client.list_tables(ds_ref):
                out.append(f"{tbl.dataset_id}.{tbl.table_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️ [BQ] list_tables failed for {ds_ref!r}: {exc}")
    return sorted(out)


def extract_schema(conn_config: Dict[str, Any], table_name: str) -> Dict[str, Any]:
    """Return column metadata for ``dataset.table`` (or fully qualified
    ``project.dataset.table``)."""
    client, resolved = _get_client(conn_config)
    fq = _qualify(resolved["project_id"], resolved["default_dataset"], table_name)
    table = client.get_table(fq)

    columns: List[Dict[str, Any]] = []
    for field in table.schema:
        columns.append({
            "name": field.name,
            "type": str(field.field_type),
            "nullable": field.mode != "REQUIRED",
            "description": field.description or None,
        })

    return {
        "table_name": table_name,
        "columns": columns,
        "row_count": int(table.num_rows or 0),
    }


def extract_sample_rows(conn_config: Dict[str, Any], table_name: str,
                         n: int = 10) -> List[Dict[str, Any]]:
    """Return n representative rows for LLM SQL prompt construction.

    Uses ``client.list_rows(max_results=n)`` (free, doesn't trigger a scan)
    rather than a ``TABLESAMPLE`` query."""
    client, resolved = _get_client(conn_config)
    fq = _qualify(resolved["project_id"], resolved["default_dataset"], table_name)
    try:
        rows = client.list_rows(fq, max_results=n)
        return [dict(row.items()) for row in rows]
    except Exception as exc:
        logger.warning(f"⚠️ [BQ] sample rows failed for {table_name!r}: {exc}")
        return []


def extract_primary_keys(conn_config: Dict[str, Any], table_name: str) -> List[str]:
    """BigQuery PKs are advisory (added in 2023). Return the declared
    PK columns if any; empty list otherwise."""
    client, resolved = _get_client(conn_config)
    fq = _qualify(resolved["project_id"], resolved["default_dataset"], table_name)
    try:
        table = client.get_table(fq)
        ts = getattr(table, "table_constraints", None)
        if ts and getattr(ts, "primary_key", None):
            return list(ts.primary_key.columns or [])
    except Exception as exc:
        logger.warning(f"⚠️ [BQ] PK detection failed for {table_name!r}: {exc}")
    return []


def extract_foreign_keys(conn_config: Dict[str, Any], table_name: str) -> List[Dict[str, Any]]:
    """BQ FK constraints are advisory and rarely populated. Return empty
    by default — relationships should be declared in the catalogue."""
    return []


# ---------------------------------------------------------------------------
# Query execution (query plane)
# ---------------------------------------------------------------------------


def execute_bigquery(
    conn_config: Dict[str, Any],
    sql: str,
    row_limit: int = 50,
    timeout_seconds: int = 60,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Run a SELECT, return (rows, error). Never raises.

    - SELECT-only via sqlglot (BigQuery dialect); wraps with hard LIMIT.
    - Caps ``maximum_bytes_billed`` (defends against runaway scans).
    """
    safe_sql, err = _enforce_readonly_and_limit(sql, row_limit)
    if err is not None:
        logger.warning(f"⚠️ [BQ] Rejected query: {err}")
        return [], err

    client, resolved = _get_client(conn_config)

    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        return [], "google-cloud-bigquery not installed on this deployment"

    job_config = bigquery.QueryJobConfig(
        use_legacy_sql=False,
        maximum_bytes_billed=resolved["max_bytes_billed"],
    )
    try:
        job = client.query(safe_sql, job_config=job_config)
        # Bound wall-clock by row iteration timeout rather than relying on
        # the server timeoutMs (which BQ treats as a hint).
        rows = job.result(timeout=float(timeout_seconds), max_results=row_limit)
        out = [dict(r.items()) for r in rows]
        return out, None
    except Exception as exc:  # noqa: BLE001
        snippet = (safe_sql[:300] + "…") if len(safe_sql) > 300 else safe_sql
        logger.error(f"❌ [BQ] Execution failed: {exc}\nSQL: {snippet}")
        return [], str(exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _qualify(project: str, default_dataset: Optional[str], table_name: str) -> str:
    parts = table_name.split(".")
    if len(parts) == 3:
        return table_name
    if len(parts) == 2:
        return f"{project}.{table_name}"
    if len(parts) == 1:
        if not default_dataset:
            raise ValueError(
                f"BigQuery table {table_name!r} is unqualified and connection has no default_dataset"
            )
        return f"{project}.{default_dataset}.{table_name}"
    raise ValueError(f"unrecognised BigQuery table name: {table_name!r}")


_FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER",
    "CREATE", "GRANT", "REVOKE", "MERGE", "CALL", "EXECUTE",
    "REPLACE", "RENAME", "EXPORT", "LOAD",
)


def _enforce_readonly_and_limit(sql: str, row_limit: int) -> Tuple[str, Optional[str]]:
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return "", "Empty SQL"
    if ";" in stripped:
        return "", "Multi-statement SQL is not allowed"

    try:
        import sqlglot  # type: ignore
        from sqlglot import expressions as sg_exp  # type: ignore

        try:
            parsed = sqlglot.parse(stripped, read="bigquery")
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

        return f"SELECT * FROM ({stripped}) LIMIT {int(row_limit)}", None

    except ImportError:
        upper = stripped.upper()
        first_tok = upper.split(None, 1)[0] if upper.split() else ""
        if first_tok not in ("SELECT", "WITH"):
            return "", f"Only SELECT/WITH queries are allowed (got {first_tok!r})"
        import re as _re
        for kw in _FORBIDDEN_KEYWORDS:
            if _re.search(rf"\b{kw}\b", upper):
                return "", f"Forbidden keyword in query: {kw}"
        if "LIMIT" not in upper:
            stripped = f"{stripped} LIMIT {int(row_limit)}"
        return stripped, None
