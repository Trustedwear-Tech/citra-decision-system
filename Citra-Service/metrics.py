# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

"""
Prometheus metrics for Citra-Service.

Exposes `/metrics` and records:
  • mcp_call_duration_seconds{tool_id, source_type, status}
  • mcp_call_total{tool_id, source_type, status}
  • mcp_circuit_state{tool_id}            — 0=closed, 1=half_open, 2=open
  • discovery_stale_seconds               — age of the last successful registry fetch

Instrumentation is opt-in: import `observe_mcp_call` around MCP HTTP calls
and call `note_discovery_refresh()` after a successful registry pull.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from fastapi import FastAPI
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

logger = logging.getLogger(__name__)

REGISTRY = CollectorRegistry()
_ORG = os.getenv("ORG_ID", "unknown")

_COMMON = {"org": _ORG}

mcp_call_duration = Histogram(
    "mcp_call_duration_seconds",
    "Duration of MCP /query calls made by Citra-Service.",
    labelnames=("org", "tool_id", "source_type", "status"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
    registry=REGISTRY,
)
mcp_call_total = Counter(
    "mcp_call_total",
    "Total MCP /query calls made by Citra-Service.",
    labelnames=("org", "tool_id", "source_type", "status"),
    registry=REGISTRY,
)
mcp_circuit_state = Gauge(
    "mcp_circuit_state",
    "Per-tool circuit breaker state: 0=closed, 1=half_open, 2=open.",
    labelnames=("org", "tool_id"),
    registry=REGISTRY,
)

discovery_stale_seconds = Gauge(
    "discovery_stale_seconds",
    "Seconds since the last successful discovery-service registry fetch.",
    labelnames=("org",),
    registry=REGISTRY,
)
discovery_stale_seconds.labels(**_COMMON).set(0)

# ---------------------------------------------------------------------------
# Grounding / anti-hallucination metrics
# ---------------------------------------------------------------------------

rag_context_empty_total = Counter(
    "rag_context_empty_total",
    "Number of RAG calls where retrieval returned no usable context.",
    labelnames=("org", "feature"),
    registry=REGISTRY,
)
rag_chunks_used = Histogram(
    "rag_chunks_used",
    "Number of context chunks passed to the LLM after retrieval/rerank/floor.",
    labelnames=("org", "feature"),
    buckets=(0, 1, 2, 4, 6, 8, 12, 16, 24, 32, 64),
    registry=REGISTRY,
)
rag_rerank_dropped_total = Counter(
    "rag_rerank_dropped_total",
    "Number of chunks dropped by min-score floors (post-rerank or absolute).",
    labelnames=("org", "stage"),
    registry=REGISTRY,
)
critic_unsupported_claims = Histogram(
    "critic_unsupported_claims",
    "Count of unsupported claims flagged by the critic per response.",
    labelnames=("org", "feature"),
    buckets=(0, 1, 2, 3, 5, 8, 13),
    registry=REGISTRY,
)
critic_runs_total = Counter(
    "critic_runs_total",
    "Total critic verification runs.",
    labelnames=("org", "feature", "verdict"),
    registry=REGISTRY,
)
llm_temperature_observed = Histogram(
    "llm_temperature_observed",
    "Effective LLM temperature used per feature call.",
    labelnames=("org", "feature"),
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    registry=REGISTRY,
)
internet_grounding_total = Counter(
    "internet_grounding_total",
    "Internet-grounding decisions taken by the retrieval layer.",
    labelnames=("org", "feature", "reason"),
    registry=REGISTRY,
)


def record_grounding(
    feature: str,
    *,
    chunks_used: int,
    context_empty: bool,
    rerank_dropped: int = 0,
    absolute_dropped: int = 0,
    temperature: Optional[float] = None,
    internet_used: bool = False,
    internet_reason: str = "",
) -> None:
    """Best-effort emit of grounding metrics. Never raises."""
    try:
        if context_empty:
            rag_context_empty_total.labels(org=_ORG, feature=feature).inc()
        rag_chunks_used.labels(org=_ORG, feature=feature).observe(max(0, int(chunks_used)))
        if rerank_dropped:
            rag_rerank_dropped_total.labels(org=_ORG, stage="rerank").inc(rerank_dropped)
        if absolute_dropped:
            rag_rerank_dropped_total.labels(org=_ORG, stage="absolute").inc(absolute_dropped)
        if temperature is not None:
            llm_temperature_observed.labels(org=_ORG, feature=feature).observe(float(temperature))
        if internet_used:
            internet_grounding_total.labels(
                org=_ORG, feature=feature, reason=internet_reason or "enabled"
            ).inc()
    except Exception as _e:
        logger.debug("record_grounding failed: %s", _e)


def record_critic(feature: str, *, unsupported: int, severity: str = "none") -> None:
    """Best-effort emit of critic metrics. Never raises."""
    try:
        critic_unsupported_claims.labels(org=_ORG, feature=feature).observe(max(0, int(unsupported)))
        verdict = "problems" if unsupported > 0 else "clean"
        critic_runs_total.labels(org=_ORG, feature=feature, verdict=verdict).inc()
    except Exception as _e:
        logger.debug("record_critic failed: %s", _e)


_last_discovery_ts: float = 0.0


_STATE_TO_INT = {"closed": 0, "half_open": 1, "open": 2}


@contextmanager
def observe_mcp_call(tool_id: str, source_type: str) -> Iterator[dict]:
    state = {"status": "ok"}
    start = time.monotonic()
    try:
        yield state
    except Exception:
        state["status"] = "error"
        raise
    finally:
        labels = {**_COMMON, "tool_id": tool_id, "source_type": source_type, "status": state["status"]}
        mcp_call_duration.labels(**labels).observe(time.monotonic() - start)
        mcp_call_total.labels(**labels).inc()


def note_mcp_circuit_open(tool_id: str) -> None:
    """Record that a call was skipped because the breaker was open."""
    labels = {**_COMMON, "tool_id": tool_id, "source_type": "n/a", "status": "circuit_open"}
    mcp_call_total.labels(**labels).inc()


def note_discovery_refresh() -> None:
    global _last_discovery_ts
    _last_discovery_ts = time.monotonic()
    discovery_stale_seconds.labels(**_COMMON).set(0)


async def _staleness_sampler(interval: float = 10.0) -> None:
    while True:
        try:
            if _last_discovery_ts > 0:
                age = time.monotonic() - _last_discovery_ts
                discovery_stale_seconds.labels(**_COMMON).set(age)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"discovery staleness sampler: {exc}")
        await asyncio.sleep(interval)


async def _cb_state_sampler(interval: float = 5.0) -> None:
    """Sample the circuit breaker registry into the mcp_circuit_state Gauge."""
    try:
        from circuit_breaker import get_breaker
    except Exception as exc:
        logger.warning(f"cb sampler disabled: {exc}")
        return
    while True:
        try:
            snap = get_breaker().snapshot()
            for tool_id, state in snap.items():
                mcp_circuit_state.labels(**_COMMON, tool_id=tool_id).set(_STATE_TO_INT.get(state, 0))
        except Exception as exc:  # pragma: no cover
            logger.warning(f"cb sampler: {exc}")
        await asyncio.sleep(interval)


_sampler_tasks: list[asyncio.Task] = []


def start_samplers() -> None:
    if _sampler_tasks:
        return
    _sampler_tasks.append(asyncio.create_task(_staleness_sampler(), name="metrics-discovery-stale"))
    _sampler_tasks.append(asyncio.create_task(_cb_state_sampler(), name="metrics-cb-state"))


def stop_samplers() -> None:
    for t in _sampler_tasks:
        if not t.done():
            t.cancel()
    _sampler_tasks.clear()


def mount_metrics(app: FastAPI) -> None:
    async def _metrics() -> Response:
        data = generate_latest(REGISTRY)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    # NOTE: /metrics bypasses JWTAuthMiddleware via that middleware's public-path
    # allowlist (see auth_middleware.py). Restrict scrape access at the network
    # or ingress layer if needed.
    app.add_api_route("/metrics", _metrics, methods=["GET"], include_in_schema=False)
