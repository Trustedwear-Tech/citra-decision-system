# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Outbox watcher — bridges the agent's `/workspace/.citra/outbox.ndjson`
to Citra-Service and emits SSE events back to the UI.

How it works:
  1. Before each agent turn, we record the current size of the outbox file
     (or zero if it doesn't exist) — this is our "read offset".
  2. The agent writes one JSON entry per artifact / html_block while it runs.
  3. While streaming AND after the openclaw subprocess exits, we tail any
     new lines in the outbox, translate each into a Citra SSE event, and
     yield it. Artifacts are uploaded to Citra-Service first; we receive
     a presigned URL and embed it in the event.

Per-turn caps: 25 MB / artifact, 10 artifacts total. Anything larger or
beyond the count is dropped with an `error` event so the UI surfaces it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from .config import SandboxConfig

logger = logging.getLogger("action-sandbox.outbox")

_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_MAX_ARTIFACTS_PER_TURN = 10


def _outbox_path(cfg: SandboxConfig) -> Path:
    return Path(cfg.workspace_dir) / ".citra" / "outbox.ndjson"


class OutboxWatcher:
    """One instance per turn. Owns the read offset for that turn."""

    def __init__(self, cfg: SandboxConfig):
        self._cfg = cfg
        self._path = _outbox_path(cfg)
        self._offset = self._current_size()
        self._uploaded = 0

    def _current_size(self) -> int:
        try:
            return self._path.stat().st_size
        except FileNotFoundError:
            return 0

    async def drain(self) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE event dicts for all new outbox lines since `__init__`.

        Caller is responsible for serialising these into the SSE byte stream.
        Safe to call multiple times in the same turn (e.g. tick during
        streaming, then once more after openclaw exits).
        """
        if not self._path.exists():
            return
        try:
            with self._path.open("rb") as f:
                f.seek(self._offset)
                buf = f.read()
                self._offset = f.tell()
        except OSError as e:
            logger.warning("outbox read failed: %s", e)
            return
        if not buf:
            return
        for raw in buf.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            kind = entry.get("type")
            if kind == "artifact":
                async for evt in self._handle_artifact(entry):
                    yield evt
            elif kind == "html_block":
                yield self._handle_html(entry)
            elif kind == "email":
                async for evt in self._handle_email(entry):
                    yield evt
            else:
                logger.info("outbox: ignoring unknown entry type=%r", kind)

    # ----------------------- artifact upload -----------------------------
    async def _handle_artifact(self, entry: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if self._uploaded >= _MAX_ARTIFACTS_PER_TURN:
            yield {
                "type": "error",
                "message": f"artifact dropped (limit {_MAX_ARTIFACTS_PER_TURN}/turn): "
                           f"{entry.get('filename', '?')}",
            }
            return
        rel = entry.get("rel_path") or ""
        kind = str(entry.get("kind") or "txt")
        title = str(entry.get("title") or "")
        summary = str(entry.get("summary") or "")
        slug = (entry.get("slug") or "").strip().lower() or None
        keep_versions = int(entry.get("keep_versions") or 2)
        try:
            uploaded = await self._upload_one(
                rel_path=rel, kind=kind, title=title, summary=summary,
            )
        except _UploadError as e:
            yield {"type": "error", "message": str(e)}
            return

        # Slug-based versioning: update the registry and prune old
        # S3 keys beyond the keep window. The agent did
        # publish.publish_versioned(); we (the adapter) do the
        # bookkeeping after upload because only we know the S3 key
        # the upload endpoint just minted.
        deleted_count = 0
        if slug:
            try:
                deleted_count = await self._supersede_slug(
                    slug=slug,
                    new_version={
                        "key": uploaded.get("key") or "",
                        "url": uploaded.get("url") or "",
                        "ts": time.time(),
                        "kind": uploaded.get("kind") or kind,
                        "title": uploaded.get("title") or "",
                        "filename": uploaded.get("filename") or "",
                        "size": uploaded.get("size") or 0,
                    },
                    keep=keep_versions,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("slug supersede failed (slug=%s): %s", slug, e)

        out_evt: dict[str, Any] = {
            "type": "artifact",
            "kind": uploaded["kind"],
            "title": uploaded["title"] or uploaded["filename"],
            "summary": uploaded["summary"],
            "filename": uploaded["filename"],
            "size": uploaded["size"],
            "url": uploaded["url"],
        }
        if uploaded.get("key"):
            out_evt["key"] = uploaded["key"]
        if slug:
            out_evt["slug"] = slug
            if deleted_count:
                out_evt["superseded_versions"] = deleted_count
        yield out_evt

    # ----------------------- slug versioning -----------------------------
    async def _supersede_slug(
        self,
        *,
        slug: str,
        new_version: dict[str, Any],
        keep: int,
    ) -> int:
        """Update the artifact registry for ``slug`` and delete S3 keys
        beyond the keep window. Returns the count of S3 deletions.

        Registry lives in scratch.workspace at
        ``namespace="artifacts", key=<slug>``. Versions list is
        chronological (oldest first); ``current`` mirrors the most
        recent entry.
        """
        if not self._cfg.proxy_base_url or not self._cfg.scoped_token:
            return 0
        base = str(self._cfg.proxy_base_url).rstrip("/")
        headers = {
            "Authorization": f"Bearer {self._cfg.scoped_token}",
            "Content-Type": "application/json",
        }

        # 1. Fetch existing registry
        existing: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.post(
                    f"{base}/scratch/workspace/get",
                    headers=headers,
                    json={"namespace": "artifacts", "key": slug},
                )
                if 200 <= r.status_code < 300:
                    body = r.json() or {}
                    doc = body.get("doc") if isinstance(body, dict) else None
                    if isinstance(doc, dict):
                        existing = doc
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("supersede: registry get failed (slug=%s): %s", slug, e)

        # 2. Build new registry
        prior_versions = existing.get("versions") or []
        if not isinstance(prior_versions, list):
            prior_versions = []
        all_versions = prior_versions + [new_version]
        # Keep only the last ``keep`` versions; older ones get deleted.
        deletions: list[dict[str, Any]] = []
        if len(all_versions) > keep:
            cut = len(all_versions) - keep
            deletions = all_versions[:cut]
            all_versions = all_versions[cut:]

        new_doc = {
            "slug": slug,
            "title": new_version.get("title") or existing.get("title") or "",
            "kind": new_version.get("kind") or existing.get("kind") or "",
            "current": new_version,
            "versions": all_versions,
            "updated_at": time.time(),
        }

        # 3. Write registry back
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                await http.post(
                    f"{base}/scratch/workspace/put",
                    headers=headers,
                    json={"namespace": "artifacts", "key": slug, "doc": new_doc},
                )
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("supersede: registry put failed (slug=%s): %s", slug, e)

        # 4. Delete S3 keys for the version(s) we dropped from the window
        deleted = 0
        for stale in deletions:
            stale_key = (stale or {}).get("key") or ""
            if not stale_key:
                continue
            try:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    r = await http.post(
                        f"{base}/files/delete",
                        headers=headers,
                        json={"key": stale_key},
                    )
                if 200 <= r.status_code < 300:
                    deleted += 1
                    logger.info(
                        "supersede: deleted stale artifact slug=%s key=%s",
                        slug, stale_key,
                    )
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(
                    "supersede: delete failed slug=%s key=%s: %s",
                    slug, stale_key, e,
                )
        return deleted

    async def _upload_one(
        self, *, rel_path: str, kind: str, title: str, summary: str,
    ) -> dict[str, Any]:
        """Upload one workspace file to Citra-Service; returns the uploaded
        descriptor. Raises ``_UploadError`` for any user-facing failure.
        """
        if not self._cfg.artifact_url:
            raise _UploadError("CITRA_AGENT_ARTIFACT_URL not set; cannot upload")
        abs_path = Path(self._cfg.workspace_dir) / rel_path
        try:
            abs_path = abs_path.resolve()
            if not str(abs_path).startswith(str(Path(self._cfg.workspace_dir).resolve())):
                raise ValueError("outside workspace")
            if not abs_path.is_file():
                raise FileNotFoundError(str(abs_path))
        except (OSError, ValueError) as e:
            raise _UploadError(f"artifact missing: {rel_path} ({e})") from e
        size = abs_path.stat().st_size
        if size > _MAX_ARTIFACT_BYTES:
            raise _UploadError(
                f"artifact too large ({size} > {_MAX_ARTIFACT_BYTES}): {rel_path}"
            )
        try:
            data = abs_path.read_bytes()
        except OSError as e:
            raise _UploadError(f"cannot read artifact {rel_path}: {e}") from e

        filename = abs_path.name
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.post(
                    self._cfg.artifact_url,
                    headers={"Authorization": f"Bearer {self._cfg.scoped_token}"},
                    data={
                        "session_id": self._cfg.session_id,
                        "kind": kind,
                        "title": title,
                        "summary": summary,
                        "filename": filename,
                    },
                    files={"file": (filename, data, _content_type_for(kind))},
                )
            if resp.status_code >= 400:
                raise _UploadError(
                    f"artifact upload {resp.status_code}: {resp.text[:200]}"
                )
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise _UploadError(f"artifact upload failed: {e}") from e

        self._uploaded += 1
        return {
            "kind": kind,
            "title": title,
            "summary": summary,
            "filename": filename,
            "size": size,
            "url": payload.get("download_url"),
            "key": payload.get("key"),  # S3 key for slug-based supersede
        }

    # ----------------------- email --------------------------------------
    async def _handle_email(self, entry: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if not self._cfg.email_url:
            yield {"type": "error", "message": "CITRA_AGENT_EMAIL_URL not set; cannot send email"}
            return
        subject = str(entry.get("subject") or "").strip() or "Citra Action Chat report"
        intro = str(entry.get("intro") or "").strip()
        rels = entry.get("attachments") or []
        if not isinstance(rels, list):
            yield {"type": "error", "message": "email: attachments must be a list"}
            return

        uploaded: list[dict[str, Any]] = []
        for rel in rels:
            try:
                u = await self._upload_one(
                    rel_path=str(rel),
                    kind=_kind_from_path(str(rel)),
                    title=Path(str(rel)).stem,
                    summary="",
                )
            except _UploadError as e:
                yield {"type": "error", "message": f"email attachment failed: {e}"}
                return
            uploaded.append(u)

        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.post(
                    self._cfg.email_url,
                    headers={"Authorization": f"Bearer {self._cfg.scoped_token}"},
                    json={
                        "session_id": self._cfg.session_id,
                        "subject": subject,
                        "intro": intro,
                        "attachments": [
                            {
                                "title": u["title"] or u["filename"],
                                "filename": u["filename"],
                                "kind": u["kind"],
                                "size": u["size"],
                                "url": u["url"],
                            }
                            for u in uploaded
                        ],
                    },
                )
            if resp.status_code >= 400:
                yield {
                    "type": "error",
                    "message": f"email send {resp.status_code}: {resp.text[:200]}",
                }
                return
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            yield {"type": "error", "message": f"email send failed: {e}"}
            return

        yield {
            "type": "email_sent",
            "to": payload.get("to"),
            "subject": subject,
            "attachments": len(uploaded),
        }

    # ----------------------- html block ----------------------------------
    @staticmethod
    def _handle_html(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "html_block",
            "html": str(entry.get("html") or ""),
            "title": str(entry.get("title") or ""),
        }


_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "csv": "text/csv",
    "png": "image/png",
    "txt": "text/plain",
    "html": "text/html",
    "json": "application/json",
}


def _content_type_for(kind: str) -> str:
    return _CONTENT_TYPES.get(kind.lower(), "application/octet-stream")


_KIND_BY_EXT = {
    ".pdf": "pdf", ".xlsx": "xlsx", ".pptx": "pptx", ".docx": "docx",
    ".csv": "csv", ".png": "png", ".jpg": "png", ".jpeg": "png",
    ".html": "html", ".json": "json", ".txt": "txt", ".md": "txt",
}


def _kind_from_path(path: str) -> str:
    return _KIND_BY_EXT.get(Path(path).suffix.lower(), "txt")


class _UploadError(Exception):
    """Raised when uploading a single artifact fails in a user-visible way."""
