# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Bucket access for the Action Chat sandbox.

Two-bucket model — enforced server-side, the agent has no S3
credentials and never picks the bucket:

* **READ** — the user's existing uploads bucket. Browse + download only.
  ``list``, ``get_bytes``, ``get_text``, ``get_url``, ``head``.
  No write or delete is possible against this bucket.

* **WRITE** — the action-chat-only outputs bucket. The agent's own
  generated artifacts (briefs, derived CSVs, intermediate parquet)
  go here via ``put``. Listed separately via ``list_outputs``.
  ``delete`` removes only your own outputs.

Typical usage::

    from citra_toolkit import files

    # See what the user has uploaded.
    catalog = files.list()                       # whole user prefix
    catalog = files.list(prefix="bihar/")        # narrow

    # Read a small file inline (≤ 8 MiB).
    pdf = files.get_bytes(catalog[0]["key"])

    # Larger file → presigned URL, fetch with httpx directly.
    url = files.get_url(catalog[0]["key"])

    # Compute on a structured file via DuckDB.
    from citra_toolkit import sql
    df = sql.duckdb_over_file(
        "SELECT region, SUM(sales) FROM data GROUP BY 1",
        key=catalog[0]["key"],
        format="csv",
    )

    # Write a derived report into your own output namespace.
    files.put("q2-summary.md",
              b"# Q2 summary\\n...",
              content_type="text/markdown",
              category="reports")

    # See what you've written previously (other namespace).
    mine = files.list_outputs()
"""
from __future__ import annotations

import base64
from typing import Any, Iterable

from ._proxy import proxy_url
from .client import http_client


def _normalise_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows or []:
        out.append({
            "key": r.get("key"),
            "size": int(r.get("size") or 0),
            "last_modified": r.get("last_modified") or "",
            "etag": r.get("etag") or "",
        })
    return out


# ----------------------------------------------------------------- list
def list(prefix: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
    """List the user's uploaded files. READ-ONLY against the user-uploads bucket.

    ``prefix`` is relative to the user's root prefix and may be empty.
    Returns ``[{key, size, last_modified, etag}, ...]``.
    """
    body = {"prefix": prefix or "", "limit": int(limit)}
    with http_client(timeout=20.0) as c:
        r = c.post(proxy_url("files/list"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    return _normalise_rows(payload.get("items") or [])


def list_outputs(prefix: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
    """List artifacts you wrote via ``files.put``. Reads from the
    action-chat OUTPUTS bucket (different bucket from user uploads)."""
    body = {"prefix": prefix or "", "limit": int(limit)}
    with http_client(timeout=20.0) as c:
        r = c.post(proxy_url("files/list_outputs"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    return _normalise_rows(payload.get("items") or [])


# ------------------------------------------------------------------ get
def get_bytes(key: str) -> bytes:
    """Return the file bytes inline. Caps at 8 MiB; over that, use
    ``get_url`` and stream with ``httpx`` directly."""
    with http_client(timeout=60.0) as c:
        r = c.post(proxy_url("files/get"), json={"key": key, "inline": True})
        r.raise_for_status()
        payload = r.json() or {}
    b64 = payload.get("content_b64") or ""
    return base64.b64decode(b64) if b64 else b""


def get_text(key: str, encoding: str = "utf-8") -> str:
    """Convenience wrapper: read bytes and decode."""
    return get_bytes(key).decode(encoding, errors="replace")


def get_url(key: str, expiry_seconds: int = 1800) -> str:
    """Return a presigned download URL (good for large files)."""
    with http_client(timeout=20.0) as c:
        r = c.post(
            proxy_url("files/get"),
            json={"key": key, "inline": False, "expiry_seconds": int(expiry_seconds)},
        )
        r.raise_for_status()
        return (r.json() or {}).get("presigned_url") or ""


# ------------------------------------------------------------------ head
def head(key: str) -> dict[str, Any]:
    """Metadata only — size, content type, etag, custom metadata."""
    with http_client(timeout=15.0) as c:
        r = c.post(proxy_url("files/head"), json={"key": key})
        r.raise_for_status()
        return r.json() or {}


# --------------------------------------------------------- search_metadata
def search_metadata(query: str, *, top_k: int = 10,
                    kind: str | None = None) -> list[dict[str, Any]]:
    """Rank user files by metadata relevance (filename / summary /
    schema_hint).

    Use this to discover **whether a file exists** for a sub-question,
    and to decide which path to take next:
      * ``kind="structured"``  → for compute / aggregate questions
        (the agent will then pull bytes and use ``sql.duckdb_over_file``
        or pandas — never embed structured data).
      * ``kind="unstructured"`` → useful when you need a specific
        file by name; for general prose / knowledge questions prefer
        ``vault.search(query)`` which spans all unstructured files
        at once.
      * ``kind=None`` → both kinds returned, ranked together.

    Returns up to ``top_k`` items: ``{key, filename, kind, extension,
    size_bytes, folder_id, summary, schema_hint, score, document_id}``.
    """
    if not query or not query.strip():
        return []
    body: dict[str, Any] = {"query": query, "top_k": int(top_k)}
    if kind:
        body["kind"] = kind
    with http_client(timeout=20.0) as c:
        r = c.post(proxy_url("files/search_metadata"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    return list(payload.get("items") or [])


# -------------------------------------------------------------- metadata
def metadata(keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Pull Citra-Service's per-file metadata for the given S3 keys.

    For every key that Citra-Service indexed at upload time you get
    a rich metadata dict — filename, file_type, size, total_chunks,
    total_pages, an extracted-text **summary**, and (for structured
    files) a **schema_hint** (header / first row). Keys that Citra
    didn't index — agent outputs, files uploaded outside the vault
    pipeline — are simply absent from the response.

    **Use this BEFORE peeking files yourself.** If a key is in the
    response, you already have a usable summary + schema_hint with
    no S3 download. Only fall back to ``sql.duckdb_over_file`` /
    ``files.get_text`` peeks for keys not returned here.

    Returns ``{<s3_key>: {filename, kind, size_bytes, total_chunks,
    total_pages, summary, schema_hint, folder_id, created_at, ...}}``.
    Limit: ≤ 50 keys per call.
    """
    keys_list = [k for k in keys if isinstance(k, str) and k]
    if not keys_list:
        return {}
    body = {"keys": keys_list[:50]}
    with http_client(timeout=20.0) as c:
        r = c.post(proxy_url("files/metadata"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    return dict(payload.get("items") or {})


# ------------------------------------------------------------------- put
def put(filename: str, data: bytes, *,
        content_type: str = "application/octet-stream",
        category: str = "outputs") -> dict[str, Any]:
    """Write an agent-generated artifact to the OUTPUTS bucket.

    Server picks the path. The file lands at
    ``<env>/users/<uid>/actionchat/outputs/<category>/<filename>``,
    tagged ``citra-purpose=actionchat-output``. Cannot be pointed at
    the user-uploads bucket.

    ``filename`` must be a basename (no slashes, no leading dot).
    ``category`` defaults to ``"outputs"``; use ``"reports"`` /
    ``"intermediate"`` etc. to organise.
    """
    body = {
        "filename": filename,
        "content_b64": base64.b64encode(data).decode("ascii"),
        "content_type": content_type,
        "category": category,
    }
    with http_client(timeout=60.0) as c:
        r = c.post(proxy_url("files/put"), json=body)
        r.raise_for_status()
        return r.json() or {}


# ---------------------------------------------------------------- delete
def delete(key: str) -> bool:
    """Delete an artifact you generated.

    Scoped to the agent-output bucket only — keys outside that namespace
    are rejected by the server (HTTP 403). **Vault uploads are NOT
    deletable here.** They live in a separate user-uploads bucket plus
    Milvus chunks and Mongo metadata, all managed by Citra-Service.
    The folder-management UI is the only legitimate channel for vault
    deletions; if the user asks you to remove an uploaded file, redirect
    them there.
    """
    with http_client(timeout=15.0) as c:
        r = c.post(proxy_url("files/delete"), json={"key": key})
        r.raise_for_status()
        return bool((r.json() or {}).get("deleted"))
