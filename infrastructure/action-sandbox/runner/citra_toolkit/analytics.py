# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""DuckDB analytics over Excel / CSV / Parquet files in the sandbox.

The sandbox ships pandas + openpyxl, but DuckDB SQL is dramatically
faster + clearer for joins, aggregations, and window queries — and it is
the standard analyst's lingua franca. This module sends the file bytes
to ``/action-chat/internal/duckdb`` (server-side guarded SELECT-only),
and returns a ``pandas.DataFrame`` of the result.

This is the "Excel analytics software" capability — without installing
any GUI tool inside the sandbox.

Usage::

    from citra_toolkit import analytics
    df = analytics.query_excel("/workspace/uploads/sales.xlsx",
        "SELECT region, sum(revenue) FROM data GROUP BY region")
    df = analytics.query_csv("/workspace/uploads/orders.csv",
        "SELECT * FROM data WHERE status = 'open'")
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from ._proxy import proxy_url
from .client import http_client


def _query(*, content: bytes, fmt: str, sql: str,
           filename: str = "", sheet: str | None = None,
           max_rows: int = 1000, timeout_ms: int = 15000):
    body = {
        "sql": sql,
        "format": fmt,
        "filename": filename or f"data.{fmt}",
        "content_b64": base64.b64encode(content).decode("ascii"),
        "max_rows": int(max_rows),
        "timeout_ms": int(timeout_ms),
    }
    if sheet:
        body["sheet"] = sheet
    with http_client(timeout=max(60.0, (timeout_ms / 1000.0) + 10.0)) as c:
        r = c.post(proxy_url("duckdb"), json=body)
        r.raise_for_status()
        payload = r.json() or {}

    import pandas as pd  # type: ignore
    columns = list(payload.get("columns") or [])
    rows = list(payload.get("rows") or [])
    df = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame(rows)

    try:
        from . import research as _research
        _research.auto_step(
            "analytics.duckdb",
            params={"format": fmt, "sql": sql[:120], "rows": len(df)},
            summary=f"{len(df)} rows in {payload.get('elapsed_ms')} ms",
        )
    except Exception:  # noqa: BLE001
        pass
    return df


def _read_path_or_bytes(src: bytes | str | os.PathLike) -> tuple[bytes, str]:
    if isinstance(src, (bytes, bytearray)):
        return bytes(src), ""
    p = Path(src)
    return p.read_bytes(), p.name


def query_excel(src: bytes | str | os.PathLike, sql: str, *,
                sheet: str | None = None,
                max_rows: int = 1000, timeout_ms: int = 15000):
    data, filename = _read_path_or_bytes(src)
    return _query(content=data, fmt="xlsx", sql=sql, filename=filename,
                  sheet=sheet, max_rows=max_rows, timeout_ms=timeout_ms)


def query_csv(src: bytes | str | os.PathLike, sql: str, *,
              max_rows: int = 1000, timeout_ms: int = 15000):
    data, filename = _read_path_or_bytes(src)
    return _query(content=data, fmt="csv", sql=sql, filename=filename,
                  max_rows=max_rows, timeout_ms=timeout_ms)


def query_parquet(src: bytes | str | os.PathLike, sql: str, *,
                  max_rows: int = 1000, timeout_ms: int = 15000):
    data, filename = _read_path_or_bytes(src)
    return _query(content=data, fmt="parquet", sql=sql, filename=filename,
                  max_rows=max_rows, timeout_ms=timeout_ms)


def query_json(src: bytes | str | os.PathLike, sql: str, *,
               max_rows: int = 1000, timeout_ms: int = 15000):
    data, filename = _read_path_or_bytes(src)
    return _query(content=data, fmt="json", sql=sql, filename=filename,
                  max_rows=max_rows, timeout_ms=timeout_ms)
