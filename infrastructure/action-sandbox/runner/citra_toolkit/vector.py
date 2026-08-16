# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Durable per-user vector index for the Action Chat sandbox.

Backed by a dedicated Milvus collection (``actionchat_<env>_user_index``)
that is **isolated** from Citra-Service's vector store. Every row carries
the user_id from the verified scoped JWT; every query/delete is anchored
to that user_id server-side. Two users with the same logical
``collection`` name see different data.

Persistence: rows survive container restarts. The agent embeds once and
re-queries forever.

Typical usage::

    from citra_toolkit import vector

    # Embed + upsert; server fills in the vectors.
    vector.upsert(
        rows=[
            {"id": "doc-1", "text": "Section 80C deductions...", "meta": {"src": "report.pdf"}},
            {"id": "doc-2", "text": "Capital gains tax slabs...", "meta": {"src": "report.pdf"}},
        ],
        collection="tax-filing-2026",
    )

    hits = vector.query("80C limits", k=5, collection="tax-filing-2026")
    for h in hits:
        print(h.id, h.score, h.text[:80])

    # See what indexes you've already built for this user.
    for c in vector.list_collections():
        print(c["name"], c["rows"])

    # Drop a corpus you don't need any more.
    vector.drop_collection("tax-filing-2026")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ._proxy import proxy_url
from .client import http_client


@dataclass
class VectorHit:
    id: str
    score: float
    text: str
    metadata: dict[str, Any]
    collection_tag: str
    created_at: int


def _normalise_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        item: dict[str, Any] = {
            "id": str(r.get("id") or ""),
            "text": r.get("text") or "",
        }
        if "vector" in r and r["vector"] is not None:
            item["vector"] = list(r["vector"])
        meta = r.get("meta") or r.get("metadata")
        if meta is not None:
            item["meta"] = meta
        out.append(item)
    return out


def upsert(rows: Iterable[dict[str, Any]],
           *,
           collection: str = "default") -> dict[str, Any]:
    """Insert or update rows in the user's durable vector store.

    ``rows`` may carry pre-computed vectors; otherwise the server
    embeds the ``text`` field via the same model as ``embed.text``
    (cache-backed — re-using the same text is free).
    """
    body = {"rows": _normalise_rows(rows), "collection": collection}
    with http_client(timeout=120.0) as c:
        r = c.post(proxy_url("vector/upsert"), json=body)
        r.raise_for_status()
        return r.json() or {}


def query(query_text: str | None = None,
          *,
          vector: list[float] | None = None,
          k: int = 8,
          collection: str = "default",
          filter: str | None = None) -> list[VectorHit]:
    """Search the user's vector store.

    Provide ``query_text`` (server embeds it) OR ``vector`` (use a
    precomputed query vector). ``filter`` is an optional Milvus boolean
    expression — it is **AND-ed** with the user-id lock on the server,
    so it cannot leak cross-user.

    Returns up to ``k`` ``VectorHit`` entries, highest cosine score
    first.
    """
    if not query_text and not vector:
        raise ValueError("provide query_text or vector")
    body: dict[str, Any] = {"k": int(k), "collection": collection}
    if query_text:
        body["query"] = query_text
    if vector is not None:
        body["vector"] = list(vector)
    if filter:
        body["filter"] = filter
    with http_client(timeout=30.0) as c:
        r = c.post(proxy_url("vector/search"), json=body)
        r.raise_for_status()
        payload = r.json() or {}
    out: list[VectorHit] = []
    for h in payload.get("hits") or []:
        out.append(VectorHit(
            id=str(h.get("id") or ""),
            score=float(h.get("score", 0.0) or 0.0),
            text=str(h.get("text") or ""),
            metadata=dict(h.get("meta") or {}),
            collection_tag=str(h.get("collection_tag") or collection),
            created_at=int(h.get("created_at") or 0),
        ))
    return out


def delete(*,
           ids: list[str] | None = None,
           collection: str | None = None,
           filter: str | None = None) -> int:
    """Delete rows. Supply at least one of ``ids`` / ``collection`` /
    ``filter``. Operations are user-scoped server-side."""
    body: dict[str, Any] = {}
    if ids:
        body["ids"] = list(ids)
    if collection:
        body["collection"] = collection
    if filter:
        body["filter"] = filter
    if not body:
        raise ValueError("provide ids, collection, or filter")
    with http_client(timeout=30.0) as c:
        r = c.post(proxy_url("vector/delete"), json=body)
        r.raise_for_status()
        return int((r.json() or {}).get("deleted", 0))


def list_collections() -> list[dict[str, Any]]:
    """Return ``[{name, rows}]`` for every logical collection this user
    has rows in. Use this *before* re-embedding a corpus to check if you
    already indexed it last week."""
    with http_client(timeout=30.0) as c:
        r = c.post(proxy_url("vector/list_collections"), json={})
        r.raise_for_status()
        return list((r.json() or {}).get("collections") or [])


def drop_collection(collection: str) -> int:
    """Wipe every row this user has under ``collection``. Returns the
    number of rows deleted. Other users' data with the same logical name
    is untouched."""
    with http_client(timeout=30.0) as c:
        r = c.post(proxy_url("vector/drop_collection"),
                   json={"collection": collection})
        r.raise_for_status()
        return int((r.json() or {}).get("deleted", 0))


# Legacy alias — old skills called ``vector.hybrid_query``. We don't do
# BM25 hybrid in the durable store yet; route to ``query``.
def hybrid_query(query_text: str, *, k: int = 8, collection: str = "default",
                 **_ignored: Any) -> list[VectorHit]:
    return query(query_text, k=k, collection=collection)
