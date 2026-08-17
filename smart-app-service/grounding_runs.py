# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""Transient job state for the few-shot-from-history grounding refresh.

The refresh is async; its progress (status / phase / percent / result) is
short-lived UI state, not a durable record — so it lives in the platform's
shared Redis cache (``citra_cache``), the same one Citra-Service's
``redis_progress_manager`` uses: ``setex`` with a TTL + JSON payload, keyed per
task. ``citra_cache`` is intentionally Redis-only and **fails loud** when
``REDIS_HOST`` is unset (no silent local fallback) — so a misconfigured
deployment surfaces immediately instead of silently using per-replica memory
that the UI poll (possibly on another replica) would never see.

One run per app slug at a time (the refresh swaps the agent's grounding), so
the key is ``grounding:run:<slug>`` and holds the latest run for that app. The
durable grounding artefacts are the Milvus collection (vectors the runtime
reads) and the source dataset — nothing here needs to persist.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GroundingRunStore:
    """Grounding-refresh job state on the shared platform Redis (citra_cache)."""

    ACTIVE_TTL = 900    # 15 min — a refresh in flight
    DONE_TTL = 3600     # 1 h — so the BA can re-open and see the last result
    # Durable "last refresh" summary (time + counts) for the card/status endpoint.
    # The run doc above is TTL-bounded, so it can't answer "never refreshed" vs
    # "refreshed long ago". This survives ~6 months (effectively durable — Redis
    # is persisted); a missing key = never refreshed.
    LAST_TTL = 180 * 24 * 3600

    def __init__(self):
        # Raises (fail loud) if REDIS_HOST isn't configured — grounding refresh
        # needs shared Redis so the async UI poll works across replicas.
        from citra_cache import get_cache_manager
        self.cache = get_cache_manager()
        logger.info("[grounding] run store: shared Redis via citra_cache")

    @staticmethod
    def _key(slug: str) -> str:
        return f"grounding:run:{slug}"

    @staticmethod
    def _last_key(slug: str) -> str:
        return f"grounding:last:{slug}"

    def put(self, slug: str, data: Dict[str, Any], *, done: bool = False) -> None:
        ttl = self.DONE_TTL if done else self.ACTIVE_TTL
        self.cache.setex(self._key(slug), ttl, json.dumps(data))

    def get(self, slug: str) -> Optional[Dict[str, Any]]:
        v = self.cache.get(self._key(slug))
        return json.loads(v) if v else None

    def put_last(self, slug: str, summary: Dict[str, Any]) -> None:
        """Record the durable last-successful-refresh summary for the card."""
        self.cache.setex(self._last_key(slug), self.LAST_TTL, json.dumps(summary))

    def get_last(self, slug: str) -> Optional[Dict[str, Any]]:
        """Durable last-refresh summary, or None if the app was never refreshed."""
        v = self.cache.get(self._last_key(slug))
        return json.loads(v) if v else None


_store: Optional[GroundingRunStore] = None


def get_grounding_run_store() -> GroundingRunStore:
    global _store
    if _store is None:
        _store = GroundingRunStore()
    return _store
