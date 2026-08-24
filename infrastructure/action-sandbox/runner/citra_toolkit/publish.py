# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Surface a generated artifact to the Action Chat UI.

The agent does not upload to S3. It writes one JSON line per artifact into
``/workspace/.citra/outbox.ndjson``; the adapter (running in the same
container) tails this file during the turn, uploads each file to
Citra-Service (which persists to S3/MinIO), and emits an SSE ``artifact``
event with a presigned download URL.

This indirection keeps S3 credentials out of the sandbox.

The ``report.make_*`` helpers auto-publish by default, so most callers
shouldn't need to invoke ``publish()`` directly. Calling ``publish()`` on
a path that has already been published in the current container is a
no-op (idempotent), so combining auto-publish with an explicit
``publish()`` is safe.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Literal

ArtifactKind = Literal["pdf", "xlsx", "pptx", "docx", "csv", "png", "txt", "html", "json"]

# Module-level dedup set: absolute paths already queued in this container.
# Survives across turns within the same container; cleared on respawn
# (which is correct — a fresh container has a fresh tmpfs).
_PUBLISHED: set[str] = set()


def _outbox_path() -> Path:
    workspace = Path(os.getenv("CITRA_WORKSPACE_DIR", "/workspace"))
    d = workspace / ".citra"
    d.mkdir(parents=True, exist_ok=True)
    return d / "outbox.ndjson"


_KIND_BY_EXT = {
    ".pdf": "pdf", ".xlsx": "xlsx", ".pptx": "pptx", ".docx": "docx",
    ".csv": "csv", ".png": "png", ".jpg": "png", ".jpeg": "png",
    ".txt": "txt", ".md": "txt", ".html": "html", ".json": "json",
}


def _infer_kind(path: Path) -> str:
    return _KIND_BY_EXT.get(path.suffix.lower(), "txt")


# scratch.workspace's key validator is ``^[A-Za-z0-9_\-:./]{1,200}$`` —
# spaces and most punctuation are rejected. The agent often passes a
# human phrase ("Q3 Sales by Region") as the slug; we slugify so the
# registry write doesn't 400 out.
_SLUG_KILL_RE = re.compile(r"[^a-z0-9_\-:./]+")


def _slugify(s: str) -> str:
    """kebab-case sanitiser matching the workspace key regex."""
    s = (s or "").strip().lower()
    s = _SLUG_KILL_RE.sub("-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:200] or "artifact"


def publish(
    path: str | os.PathLike[str],
    *,
    kind: ArtifactKind | None = None,
    title: str | None = None,
    summary: str | None = None,
    slug: str | None = None,
    keep_versions: int | None = None,
) -> dict[str, object]:
    """Queue an on-disk file for delivery to the chat UI.

    Returns the outbox entry that was written, mostly for logging.

    Optional ``slug`` + ``keep_versions``: when provided, the adapter
    treats this artifact as a versioned deliverable. After upload it
    updates the ``scratch.workspace`` registry under
    ``namespace="artifacts", key=<slug>`` and deletes the oldest S3
    keys for that slug if the version count exceeds ``keep_versions``.
    Use the ``publish_versioned()`` convenience wrapper instead of
    passing these directly — see its docstring for the full semantics.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"publish(): not a file: {p}")
    workspace = Path(os.getenv("CITRA_WORKSPACE_DIR", "/workspace"))
    try:
        rel = p.resolve().relative_to(workspace.resolve())
    except ValueError as e:
        raise ValueError(f"publish(): file must live under {workspace}: {p}") from e

    normalised_slug = _slugify(str(slug)) if slug else None

    # Idempotency: skip if this exact (path, slug) tuple was already
    # published in this container. Including the slug in the dedup key
    # means re-publishing the same path under a NEW slug works (rare
    # but valid — e.g., the agent renamed the artifact mid-session).
    # Without slug, behaviour is unchanged: same path = deduped.
    abs_key = str(p.resolve())
    dedup_key = f"{abs_key}::{normalised_slug or ''}"
    if dedup_key in _PUBLISHED:
        return {"type": "artifact", "rel_path": str(rel), "deduped": True}

    entry: dict[str, object] = {
        "type": "artifact",
        "kind": kind or _infer_kind(p),
        "filename": p.name,
        "rel_path": str(rel),
        "size": p.stat().st_size,
        "title": title or p.stem,
        "summary": summary or "",
        "ts": time.time(),
    }
    if normalised_slug:
        entry["slug"] = normalised_slug
        entry["keep_versions"] = int(keep_versions or 2)
    out = _outbox_path()
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _PUBLISHED.add(dedup_key)
    return entry


def publish_versioned(
    path: str | os.PathLike[str],
    *,
    slug: str,
    kind: ArtifactKind | None = None,
    title: str | None = None,
    summary: str | None = None,
    keep: int = 2,
) -> dict[str, object]:
    """Publish an artifact under a stable ``slug`` with auto-cleanup.

    Refinement workflow:
      * The user asks for ``X``; you publish it under ``slug="x"``.
      * The user says "refine"; you publish a new version under the
        same slug — the adapter records v2 in the registry and
        (if keep=2) leaves both v1+v2 in S3.
      * Third refinement → v3 published, registry tracks v2+v3, **v1
        is auto-deleted from S3** (oldest beyond ``keep`` is purged).

    The registry lives in ``scratch.workspace`` under
    ``namespace="artifacts", key=<slug>`` with shape::

        {
          "slug": "q3-sales-by-region",
          "title": "Q3 Sales by Region",
          "kind": "xlsx",
          "current": {"key": "...", "url": "...", "ts": ...,
                      "filename": "...", "size": ...},
          "versions": [
            {"key": "...", "ts": ..., "filename": "...", "size": ...},
            ...
          ]
        }

    On a "refine" turn, master reads the registry to find
    ``current.key`` and passes it to the specialist. ``versions`` lets
    the master surface "earlier version available" when the user asks
    to revert.

    Args:
        path: file under ``/workspace/`` to publish.
        slug: stable kebab-case identifier (master assigns it; reuse
            it on every refinement of the same logical deliverable).
        kind/title/summary: same as ``publish()``.
        keep: how many versions to retain (default 2 = latest + one
            prior; older S3 keys auto-deleted on each new publish).

    Cleanup is end-to-end: agent calls this once, the adapter does
    the registry write + S3 deletion after upload.
    """
    if not slug or not slug.strip():
        raise ValueError("publish_versioned(): slug is required")
    return publish(
        path,
        kind=kind, title=title, summary=summary,
        slug=slug, keep_versions=int(keep),
    )
