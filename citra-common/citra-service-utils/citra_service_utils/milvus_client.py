# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
milvus_client — shared singleton MilvusClient + lifecycle helpers.

Every Citra service that talks to Milvus needs the same plumbing:

  • One MilvusClient per process (re-using a single gRPC channel).
  • PID-aware so a Gunicorn fork doesn't inherit a stale channel.
  • Channel-liveness aware so transient gRPC failures recreate cleanly.
  • A graceful close() for app shutdown.

Citra-Service already had this in ``config/milvus_config.py``. Moving it
into the shared utils package means every consumer (source-mcp-template,
data-discovery-service, future workers) gets the same battle-tested
pattern without copy-pasting it.

``pymilvus`` is a soft dependency — services that don't actually call
Milvus shouldn't be forced to pull it in. Import the helpers from this
module lazily inside the function bodies.

Usage::

    from citra_service_utils.milvus_client import (
        MilvusSettings, get_milvus_client, close_milvus_client,
    )

    settings = MilvusSettings(uri=os.getenv("MILVUS_URI"),
                              token=os.getenv("MILVUS_TOKEN"))
    client = get_milvus_client(settings)
    client.search(collection_name="demo_manufacturing_quality_test_results",
                  data=[qvec], anns_field="dense_vector", limit=20)

    # On FastAPI shutdown:
    close_milvus_client()
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MilvusSettings:
    """Connection settings for a MilvusClient.

    ``uri`` is required. ``token`` is optional for self-hosted Milvus
    instances without auth; Zilliz Cloud always needs one. ``timeout``
    is in seconds and applies to RPCs (search/insert) — not to the
    initial connection handshake.
    """
    uri: str
    token: Optional[str] = None
    timeout: int = 30


_milvus_client: Optional[Any] = None
_milvus_client_pid: Optional[int] = None
_milvus_client_settings: Optional[MilvusSettings] = None
_milvus_client_lock = threading.Lock()


def _is_channel_alive(client: Any) -> bool:
    """Best-effort check that the MilvusClient's gRPC channel is usable.

    Returns False if pymilvus's connection registry no longer holds the
    alias, if the handler's ``_channel`` was nulled out, or if gRPC reports
    SHUTDOWN / TRANSIENT_FAILURE. Returns True on any introspection error
    (better to use a possibly-dead client and let the next RPC fail than
    to thrash on recreate).
    """
    try:
        from pymilvus import connections as _conns  # type: ignore
        alias = getattr(client, "_using", None)
        if alias is None:
            return False
        if not _conns.has_connection(alias):
            return False
        handler = _conns._fetch_handler(alias)
        if getattr(handler, "_channel", None) is None:
            return False
        try:
            import grpc  # type: ignore
            channel = getattr(handler, "_channel", None)
            if channel is not None:
                state = channel.get_state(try_to_connect=False)
                if state in (
                    grpc.ChannelConnectivity.SHUTDOWN,
                    grpc.ChannelConnectivity.TRANSIENT_FAILURE,
                ):
                    logger.info("gRPC channel in %s state, will recreate", state.name)
                    return False
        except Exception:
            pass
        return True
    except Exception:
        return False


def _build_client(settings: MilvusSettings) -> Any:
    """Construct a fresh MilvusClient from settings."""
    from pymilvus import MilvusClient  # type: ignore
    kwargs = {"uri": settings.uri, "timeout": settings.timeout}
    if settings.token:
        kwargs["token"] = settings.token
    return MilvusClient(**kwargs)


def get_milvus_client(settings: MilvusSettings) -> Any:
    """Return the module-level singleton MilvusClient, creating on first call.

    Tracks PID + channel liveness + the settings instance so the singleton
    rebuilds itself after:
      • A Gunicorn pre-fork master → worker handoff (PID changed; gRPC
        channels are NOT fork-safe).
      • A transient gRPC failure (channel went SHUTDOWN).
      • An admin call swapping the connection settings under us (e.g. a
        URL rotation in dev).

    Callers must pass the SAME ``settings`` object (or an equivalent one)
    on every call. If you need to switch to a different cluster, call
    ``close_milvus_client()`` first then ``get_milvus_client(new_settings)``.
    """
    global _milvus_client, _milvus_client_pid, _milvus_client_settings
    current_pid = os.getpid()

    need_recreate = (
        _milvus_client is None
        or _milvus_client_pid != current_pid
        or _milvus_client_settings != settings
        or not _is_channel_alive(_milvus_client)
    )
    if not need_recreate:
        return _milvus_client

    with _milvus_client_lock:
        need_recreate = (
            _milvus_client is None
            or _milvus_client_pid != current_pid
            or _milvus_client_settings != settings
            or not _is_channel_alive(_milvus_client)
        )
        if not need_recreate:
            return _milvus_client

        if _milvus_client is not None:
            try:
                _milvus_client.close()
            except Exception:
                pass
            _milvus_client = None

        _milvus_client = _build_client(settings)
        _milvus_client_pid = current_pid
        _milvus_client_settings = settings
        logger.info("Singleton MilvusClient created (pid=%d, uri=%s)", current_pid, settings.uri)
    return _milvus_client


def close_milvus_client() -> None:
    """Close the singleton MilvusClient. Idempotent — safe to call from
    a FastAPI shutdown hook or signal handler even if no client was ever
    created in this process."""
    global _milvus_client, _milvus_client_pid, _milvus_client_settings
    with _milvus_client_lock:
        if _milvus_client is None:
            return
        try:
            _milvus_client.close()
            logger.info("Singleton MilvusClient closed")
        except Exception as e:
            logger.warning("MilvusClient close error: %s", e)
        finally:
            _milvus_client = None
            _milvus_client_pid = None
            _milvus_client_settings = None


def recreate_milvus_client(settings: MilvusSettings) -> Any:
    """Force-recreate the singleton MilvusClient.

    Use after a confirmed gRPC failure when the liveness probe didn't
    catch it. Most callers should NOT need this — ``get_milvus_client``
    already recreates lazily on a dead channel.
    """
    global _milvus_client, _milvus_client_pid, _milvus_client_settings
    with _milvus_client_lock:
        if _milvus_client is not None:
            try:
                _milvus_client.close()
            except Exception:
                pass
            _milvus_client = None
        _milvus_client = _build_client(settings)
        _milvus_client_pid = os.getpid()
        _milvus_client_settings = settings
        logger.info("Singleton MilvusClient recreated (pid=%d)", _milvus_client_pid)
    return _milvus_client


def create_throwaway_milvus_client(settings: MilvusSettings) -> Any:
    """Create a brand-new, short-lived MilvusClient (NOT the singleton).

    Use for one-off operations that should not be affected by singleton
    state — e.g., a long-running scan from a worker that holds its own
    channel and closes it when done. The caller is responsible for
    calling ``client.close()`` when finished.
    """
    return _build_client(settings)
