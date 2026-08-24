# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Action-Chat scratch + memory client.

Talks to the Citra-Service ``/action-chat/internal/scratch/*`` endpoints,
which are backed by a **dedicated** MongoDB database
(``action-chat-open-claw-temp``) — fully isolated from the main Citra DB.
The sandbox cannot reach Mongo directly; this is the only path.

Two surfaces:

- ``findings.*`` — short-lived (7 days) per-job notes, intermediate
  tables, captured quotes. Cleared automatically by Mongo TTL.
- ``memory.*`` — durable per-user analyst memory (terminology, named
  entities, user preferences). Survives container restarts and jobs.

Memory is **opt-in**: the agent must explicitly call ``memory.put()`` for
something to be remembered. Nothing is auto-persisted.

The First Law still applies (``SOUL.md``): memory is a shortcut, not a
citation. Re-verify before claiming a fact, even if memory says it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._proxy import proxy_url
from .client import http_client


# ============================================================== findings
@dataclass
class Finding:
    finding_id: str
    kind: str
    text: str
    tags: list[str]
    metadata: dict[str, Any]
    created_at: float


class _Findings:
    @staticmethod
    def save(text: str, *, kind: str = "note",
             tags: list[str] | None = None,
             metadata: dict[str, Any] | None = None,
             job_id: str | None = None) -> Finding:
        body = {
            "text": text,
            "kind": kind,
            "tags": tags or [],
            "metadata": metadata or {},
        }
        if job_id:
            body["job_id"] = job_id
        with http_client() as c:
            r = c.post(proxy_url("scratch/findings"), json=body)
            r.raise_for_status()
            d = r.json() or {}
        return Finding(
            finding_id=str(d.get("finding_id") or ""),
            kind=str(d.get("kind") or ""),
            text=str(d.get("text") or ""),
            tags=list(d.get("tags") or []),
            metadata=dict(d.get("metadata") or {}),
            created_at=float(d.get("created_at") or 0.0),
        )

    @staticmethod
    def list(*, job_id: str | None = None, kind: str | None = None,
             limit: int = 50) -> list[Finding]:
        params: dict[str, Any] = {"limit": int(limit)}
        if job_id:
            params["job_id"] = job_id
        if kind:
            params["kind"] = kind
        with http_client() as c:
            r = c.get(proxy_url("scratch/findings"), params=params)
            r.raise_for_status()
            payload = r.json() or {}
        out: list[Finding] = []
        for d in payload.get("findings") or []:
            out.append(Finding(
                finding_id=str(d.get("finding_id") or ""),
                kind=str(d.get("kind") or ""),
                text=str(d.get("text") or ""),
                tags=list(d.get("tags") or []),
                metadata=dict(d.get("metadata") or {}),
                created_at=float(d.get("created_at") or 0.0),
            ))
        return out

    @staticmethod
    def delete(finding_id: str) -> bool:
        with http_client() as c:
            r = c.delete(proxy_url(f"scratch/findings/{finding_id}"))
            r.raise_for_status()
            return bool((r.json() or {}).get("deleted"))


findings = _Findings()


# ============================================================== memory
@dataclass
class MemoryNote:
    slug: str
    title: str
    body: str
    tags: list[str]


def _from_memdoc(d: dict[str, Any]) -> MemoryNote:
    return MemoryNote(
        slug=str(d.get("slug") or ""),
        title=str(d.get("title") or d.get("slug") or ""),
        body=str(d.get("body") or ""),
        tags=list(d.get("tags") or []),
    )


class _Memory:
    @staticmethod
    def get(slug: str) -> MemoryNote | None:
        with http_client() as c:
            r = c.get(proxy_url(f"scratch/memory/{slug}"))
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return _from_memdoc(r.json() or {})

    @staticmethod
    def put(slug: str, body: str, *, title: str | None = None,
            tags: list[str] | None = None) -> MemoryNote:
        with http_client() as c:
            r = c.put(
                proxy_url(f"scratch/memory/{slug}"),
                json={"body": body, "title": title, "tags": tags or []},
            )
            r.raise_for_status()
            return _from_memdoc(r.json() or {})

    @staticmethod
    def delete(slug: str) -> bool:
        with http_client() as c:
            r = c.delete(proxy_url(f"scratch/memory/{slug}"))
            r.raise_for_status()
            return bool((r.json() or {}).get("deleted"))

    @staticmethod
    def clear() -> int:
        """Wipe every durable-memory note for this user in one call.

        Use when the user explicitly asks to wipe all memory — "forget
        everything", "delete all my notes", "start fresh". Returns
        the number of notes removed. After calling, confirm the count
        back to the user. For per-note deletion, use ``delete(slug)``.
        """
        with http_client() as c:
            r = c.delete(proxy_url("scratch/memory"))
            r.raise_for_status()
            return int((r.json() or {}).get("removed") or 0)

    @staticmethod
    def list(*, limit: int = 100) -> list[MemoryNote]:
        with http_client() as c:
            r = c.get(proxy_url("scratch/memory"), params={"limit": int(limit)})
            r.raise_for_status()
            return [_from_memdoc(d) for d in (r.json() or {}).get("items") or []]

    @staticmethod
    def search(query: str, *, limit: int = 20) -> list[MemoryNote]:
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/memory/search"),
                json={"query": query, "limit": int(limit)},
            )
            r.raise_for_status()
            return [_from_memdoc(d) for d in (r.json() or {}).get("items") or []]


memory = _Memory()


# ============================================================== jobs
def job_status(job_id: str) -> dict[str, Any] | None:
    """Read-only lookup of this job's status (queued/running/done/failed)."""
    with http_client() as c:
        r = c.get(proxy_url(f"scratch/jobs/{job_id}"))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json() or None


# ============================================================== workspace
# Durable, structured Mongo workspace for arbitrary-shape state.
# Backed by the ``actionchat_workspace_docs`` collection in
# ``action-chat-open-claw-<env>``. Per-user, namespaced. No TTL —
# entries persist until you drop them or admin-purge runs.
#
# Use this for **structured records** the agent will look up by field
# value or aggregate later. For prose insights → ``memory``. For vector
# retrieval → ``vector``. For SQL analytics over an uploaded file →
# ``sql.duckdb_over_file``.

class _WorkspaceDocs:
    @staticmethod
    def put(namespace: str, key: str, doc: dict[str, Any]) -> dict[str, Any]:
        """Upsert a doc under ``(namespace, key)``. Replaces any existing
        doc at that key. Use this when you want a stable lookup."""
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/workspace/put"),
                json={"namespace": namespace, "key": key, "doc": doc},
            )
            r.raise_for_status()
            return r.json() or {}

    @staticmethod
    def append(namespace: str, doc: dict[str, Any]) -> str:
        """Append a doc with a server-generated id. Use this for log-style
        records where there is no natural primary key (e.g. each scrape
        result). Returns the new doc_id."""
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/workspace/append"),
                json={"namespace": namespace, "doc": doc},
            )
            r.raise_for_status()
            return str((r.json() or {}).get("doc_id") or "")

    @staticmethod
    def get(namespace: str, key: str) -> dict[str, Any] | None:
        """Read the doc at ``(namespace, key)``. Returns None if missing."""
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/workspace/get"),
                json={"namespace": namespace, "key": key},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json() or None

    @staticmethod
    def find(namespace: str, *,
             filter: dict[str, Any] | None = None,
             limit: int = 50,
             sort: str = "updated_at",
             order: str = "desc") -> list[dict[str, Any]]:
        """Query docs by Mongo-style filter. Whitelisted operators only:
        ``$eq $ne $gt $gte $lt $lte $in $nin $exists $regex $options
        $all $elemMatch $size $and $or $nor $not``. Server rejects
        ``$where`` / ``$function`` / ``$mapReduce``."""
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/workspace/find"),
                json={
                    "namespace": namespace,
                    "filter": filter or {},
                    "limit": int(limit),
                    "sort": sort,
                    "order": order,
                },
            )
            r.raise_for_status()
            return list((r.json() or {}).get("items") or [])

    @staticmethod
    def list(namespace: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """List all docs in a namespace, newest first (by updated_at)."""
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/workspace/list"),
                json={"namespace": namespace, "limit": int(limit)},
            )
            r.raise_for_status()
            return list((r.json() or {}).get("items") or [])

    @staticmethod
    def drop(namespace: str) -> int:
        """Delete every doc in a namespace. Returns the count."""
        with http_client() as c:
            r = c.post(
                proxy_url("scratch/workspace/drop_namespace"),
                json={"namespace": namespace},
            )
            r.raise_for_status()
            return int((r.json() or {}).get("deleted") or 0)

    @staticmethod
    def list_namespaces() -> list[dict[str, Any]]:
        """Return ``[{name, count}]`` for every namespace this user has docs in.
        Useful before creating a new namespace ("did I already log this?")."""
        with http_client() as c:
            r = c.post(proxy_url("scratch/workspace/list_namespaces"), json={})
            r.raise_for_status()
            return list((r.json() or {}).get("namespaces") or [])


workspace = _WorkspaceDocs()
