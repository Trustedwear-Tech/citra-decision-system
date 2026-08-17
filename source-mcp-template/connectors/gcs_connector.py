# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
GCS Connector
=============
Helper for ``DatasetKind.duckdb`` datasets whose files live in Google
Cloud Storage (``gs://bucket/path/...``). The actual SELECT execution
runs through ``query_engine._run_duckdb`` — that branch already detects
the ``gs://`` scheme, installs the ``httpfs`` extension, and pulls HMAC
credentials from env (``GCS_HMAC_ACCESS_KEY_ID`` / ``GCS_HMAC_SECRET``).

What this module adds:

- ``list_objects(prefix, env_prefix)`` — directory-listing helper used
  during ingestion to enumerate parquet/csv drops without touching
  DuckDB. Auth via SA JSON (env var ``<PFX>_CREDENTIALS_FILE`` or
  ``<PFX>_CREDENTIALS_JSON``) OR HMAC pair.
- ``extract_schema(uri)`` — opens a thin DuckDB cursor against a single
  parquet/CSV file and returns ``{columns, row_count}`` so the dataset
  document can advertise the shape.
- ``presigned_url(bucket, key, expiry_seconds)`` — V4-signed URL the
  agent can hand to its in-container DuckDB without leaking creds.

This module is optional. ``google-cloud-storage`` is not in the default
``requirements.txt`` — install ``google-cloud-storage>=2.16.0`` when
you deploy a dept-MCP exposing GCS datasets. Schema extraction also
needs ``duckdb`` (already in template's requirements).

The agent's runtime path for **querying** GCS files is:

    SELECT ... FROM read_parquet('gs://bucket/path/*.parquet')

with credentials picked up automatically by the duckdb dispatcher. So
this module is mainly used by the ingestion side (catalogue build) and
by the agent when it wants a presigned URL.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def _env(prefix: str, name: str, default: str = "") -> str:
    return os.getenv(f"{prefix}_{name}", default).strip()


def _resolve(conn: Dict[str, Any]) -> Dict[str, Any]:
    prefix = (conn.get("env_prefix") or "").upper()
    if not prefix:
        raise ValueError("GCS connection: env_prefix is required")
    return {
        "prefix": prefix,
        "project_id": _env(prefix, "PROJECT") or str(conn.get("project_id") or "").strip() or None,
        "credentials_file": _env(prefix, "CREDENTIALS_FILE"),
        "credentials_json": _env(prefix, "CREDENTIALS_JSON"),
        "default_bucket": (conn.get("default_bucket") or "").strip() or None,
        "hmac_key_id": _env(prefix, "HMAC_ACCESS_KEY_ID") or os.getenv("GCS_HMAC_ACCESS_KEY_ID", ""),
        "hmac_secret": _env(prefix, "HMAC_SECRET") or os.getenv("GCS_HMAC_SECRET", ""),
    }


@lru_cache(maxsize=8)
def _client_for(prefix: str, project_id: Optional[str], cred_path: str, cred_hash: str):
    try:
        from google.cloud import storage  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-storage not installed; add 'google-cloud-storage>=2.16.0' "
            "to source-mcp-template/requirements.txt for this deployment"
        ) from exc
    credentials = None
    if cred_path:
        from google.oauth2 import service_account  # type: ignore
        credentials = service_account.Credentials.from_service_account_file(cred_path)
    elif cred_hash:
        raw = os.getenv(f"{prefix}_CREDENTIALS_JSON", "").strip()
        if raw:
            from google.oauth2 import service_account  # type: ignore
            credentials = service_account.Credentials.from_service_account_info(json.loads(raw))
    return storage.Client(project=project_id, credentials=credentials)


def _get_client(conn_config: Dict[str, Any]):
    resolved = _resolve(conn_config)
    import hashlib
    cred_hash = (
        hashlib.sha256(resolved["credentials_json"].encode()).hexdigest()[:16]
        if resolved["credentials_json"] else ""
    )
    return _client_for(
        resolved["prefix"], resolved["project_id"],
        resolved["credentials_file"], cred_hash,
    ), resolved


# ---------------------------------------------------------------------------
# Object listing
# ---------------------------------------------------------------------------


def list_objects(conn_config: Dict[str, Any], prefix: str = "", *,
                  bucket: Optional[str] = None,
                  max_results: int = 1000) -> List[Dict[str, Any]]:
    """List GCS objects under ``gs://<bucket>/<prefix>``.

    Returns ``[{name, size, updated, content_type, gs_url}]`` sorted by
    ``name``. Used at catalogue-build time to enumerate parquet drops.
    """
    client, resolved = _get_client(conn_config)
    target_bucket = bucket or resolved["default_bucket"]
    if not target_bucket:
        raise ValueError("list_objects: bucket is required (or set connection.default_bucket)")

    out: List[Dict[str, Any]] = []
    for blob in client.list_blobs(target_bucket, prefix=prefix, max_results=max_results):
        out.append({
            "name": blob.name,
            "size": int(blob.size or 0),
            "updated": blob.updated.isoformat() if blob.updated else None,
            "content_type": blob.content_type,
            "gs_url": f"gs://{target_bucket}/{blob.name}",
        })
    return sorted(out, key=lambda b: b["name"])


# ---------------------------------------------------------------------------
# Schema sniff via a one-shot DuckDB cursor over the file
# ---------------------------------------------------------------------------


def extract_schema(conn_config: Dict[str, Any], gs_uri: str) -> Dict[str, Any]:
    """Open ``gs_uri`` through DuckDB's httpfs+GCS and return
    ``{table_name, columns, row_count}`` for the catalogue.

    Honours the same ``GCS_HMAC_*`` env vars the runtime path uses, so
    schema sniff and SELECT execution share authentication.
    """
    try:
        import duckdb  # type: ignore
    except ImportError as exc:
        raise RuntimeError("duckdb is not installed on this MCP deployment") from exc

    if not gs_uri.startswith("gs://"):
        raise ValueError(f"extract_schema: expected gs:// URI, got {gs_uri!r}")

    resolved = _resolve(conn_config)
    con = duckdb.connect()
    try:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        if resolved["hmac_key_id"] and resolved["hmac_secret"]:
            try:
                con.execute(
                    "CREATE OR REPLACE SECRET gcs_secret (TYPE GCS, KEY_ID ?, SECRET ?)",
                    [resolved["hmac_key_id"], resolved["hmac_secret"]],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"⚠️ [GCS] secret install failed (relying on ADC): {exc}")

        fn = _table_function(gs_uri)
        cur = con.execute(f"SELECT * FROM {fn}('{gs_uri}') LIMIT 0")
        columns = [
            {"name": d[0], "type": str(d[1]), "nullable": True}
            for d in cur.description
        ] if cur.description else []

        try:
            row_count = con.execute(
                f"SELECT COUNT(*) FROM {fn}('{gs_uri}')"
            ).fetchone()[0]
        except Exception:
            row_count = -1

        return {
            "table_name": gs_uri.rsplit("/", 1)[-1],
            "columns": columns,
            "row_count": int(row_count or 0),
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# V4-signed URL for the agent (lets in-container DuckDB read via https://)
# ---------------------------------------------------------------------------


def presigned_url(conn_config: Dict[str, Any], bucket: str, key: str,
                   expiry_seconds: int = 3600) -> str:
    """Return a V4-signed HTTPS URL for ``gs://<bucket>/<key>``.

    The agent can SELECT from this URL directly via DuckDB's httpfs
    without needing GCS HMAC creds inside the sandbox — useful when
    the sandbox has no GCS env vars and the dept-MCP should not leak
    them.
    """
    client, _ = _get_client(conn_config)
    blob = client.bucket(bucket).blob(key)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=int(expiry_seconds)),
        method="GET",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_function(path: str) -> str:
    p = path.lower().split("?", 1)[0]
    if p.endswith((".xlsx", ".xls", ".xlsm")):
        return "read_xlsx"
    if p.endswith(".parquet"):
        return "read_parquet"
    return "read_csv_auto"
