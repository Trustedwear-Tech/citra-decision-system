# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Host-side disk cache for sandbox input files.

Avoids re-downloading the same S3 object on every sandbox invocation.

Cache key:    sha256 of the env-prefixed S3 key.
Validity:     ETag from ``head_object`` — if it changed (file re-uploaded)
              we redownload; otherwise the cached file is reused.
Eviction:     Simple LRU by atime when total bytes exceed
              ``SANDBOX_FILE_CACHE_MAX_BYTES`` (default 2 GiB).

This module is intentionally tiny: zero external deps beyond the boto3
client passed in, and no in-memory state (so it survives reloads).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_DIR = os.getenv("SANDBOX_FILE_CACHE_DIR", "/tmp/citra_sandbox_cache")
MAX_CACHE_BYTES = int(os.getenv("SANDBOX_FILE_CACHE_MAX_BYTES", str(2 * 1024 * 1024 * 1024)))

try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except OSError as e:
    logger.warning(f"sandbox_file_cache: cannot create {CACHE_DIR}: {e}")


def _key_paths(full_s3_key: str) -> Tuple[str, str]:
    digest = hashlib.sha256(full_s3_key.encode("utf-8")).hexdigest()
    base = os.path.join(CACHE_DIR, digest)
    return base + ".bin", base + ".json"


def _read_meta(meta_path: str) -> Optional[dict]:
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_meta(meta_path: str, meta: dict) -> None:
    tmp = meta_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.replace(tmp, meta_path)
    except OSError as e:
        logger.warning(f"sandbox_file_cache: cannot write meta {meta_path}: {e}")


async def get_or_download(s3_client, bucket: str, full_key: str) -> Tuple[str, int, bool]:
    """
    Resolve ``full_key`` to a local file path, downloading only if needed.

    Returns
    -------
    (local_path, size_bytes, cache_hit)
    """
    bin_path, meta_path = _key_paths(full_key)
    cached_meta = _read_meta(meta_path) or {}
    cached_etag = cached_meta.get("etag")
    cached_size = cached_meta.get("size", 0)

    etag: Optional[str] = None
    head_size = 0
    try:
        head = await asyncio.to_thread(
            s3_client.head_object, Bucket=bucket, Key=full_key,
        )
        etag = (head.get("ETag") or "").strip('"')
        head_size = int(head.get("ContentLength", 0))
    except Exception as e:
        logger.debug(f"sandbox_file_cache: head_object failed for {full_key}: {e}")

    if (
        etag and cached_etag == etag
        and os.path.exists(bin_path)
        and os.path.getsize(bin_path) == cached_size
    ):
        try:
            os.utime(bin_path, None)  # touch atime for LRU
        except OSError:
            pass
        return bin_path, cached_size, True

    # Cache miss — download fresh.
    response = await asyncio.to_thread(
        s3_client.get_object, Bucket=bucket, Key=full_key,
    )
    content = await asyncio.to_thread(response["Body"].read)
    size = len(content)
    tmp = bin_path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, bin_path)
        _write_meta(meta_path, {
            "etag": etag,
            "size": size,
            "ts": time.time(),
            "key": full_key,
        })
    except OSError as e:
        logger.warning(f"sandbox_file_cache: cannot write cache file {bin_path}: {e}")
        # Best-effort: fall back to a temp file so the caller still gets bytes
        fallback = bin_path + f".fallback.{os.getpid()}"
        with open(fallback, "wb") as f:
            f.write(content)
        return fallback, size, False

    # Best-effort eviction; never block the request.
    try:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _evict_if_oversize)
    except Exception:
        pass

    return bin_path, size, False


def _evict_if_oversize() -> None:
    try:
        if not os.path.isdir(CACHE_DIR):
            return
        entries = []
        for name in os.listdir(CACHE_DIR):
            if not name.endswith(".bin"):
                continue
            p = os.path.join(CACHE_DIR, name)
            try:
                st = os.stat(p)
                entries.append((st.st_atime, st.st_size, p))
            except OSError:
                continue
        total = sum(e[1] for e in entries)
        if total <= MAX_CACHE_BYTES:
            return
        entries.sort()  # oldest atime first
        for _, sz, p in entries:
            if total <= MAX_CACHE_BYTES:
                break
            try:
                os.remove(p)
                meta_p = p[:-4] + ".json"
                if os.path.exists(meta_p):
                    os.remove(meta_p)
                total -= sz
            except OSError:
                pass
    except Exception:
        logger.exception("sandbox_file_cache: eviction failed")
