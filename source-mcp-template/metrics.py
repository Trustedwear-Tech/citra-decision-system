"""
Prometheus metrics for the dept-mcp-server (query plane only).

Ingestion-side metrics (ingest duration, chunks written, embed queue depth,
DLQ counters, header detection, DuckDB execution) moved to the Citra
Workflow Engine which now owns Dept Data Flow runs.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

logger = logging.getLogger(__name__)

REGISTRY = CollectorRegistry()
_DEPT_ID = os.getenv("DEPT_ID", "unknown")
_COMMON = {"dept": _DEPT_ID}

query_duration = Histogram(
    "mcp_query_duration_seconds",
    "Duration of /query dispatch, per source.",
    labelnames=("dept", "source_id", "source_type", "status"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
    registry=REGISTRY,
)
query_total = Counter(
    "mcp_query_total",
    "Total number of /query calls dispatched.",
    labelnames=("dept", "source_id", "source_type", "status"),
    registry=REGISTRY,
)


count_cache_total = Counter(
    "mcp_count_cache_total",
    "Count-probe cache outcomes (hit / miss / store).",
    labelnames=("dept", "result"),
    registry=REGISTRY,
)


def inc_count_cache(result: str) -> None:
    """Increment the count-probe cache counter. Best-effort — never raises."""
    try:
        count_cache_total.labels(dept=_DEPT_ID, result=result).inc()
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def observe_query(source_id: str, source_type: str) -> Iterator[dict]:
    """Records duration + status for a /query dispatch."""
    state = {"status": "ok"}
    start = time.monotonic()
    try:
        yield state
    except Exception:
        state["status"] = "error"
        raise
    finally:
        elapsed = time.monotonic() - start
        labels = {**_COMMON, "source_id": source_id, "source_type": source_type, "status": state["status"]}
        query_duration.labels(**labels).observe(elapsed)
        query_total.labels(**labels).inc()


def mount_metrics(app: FastAPI) -> None:
    async def _metrics() -> Response:
        data = generate_latest(REGISTRY)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    app.add_api_route("/metrics", _metrics, methods=["GET"], include_in_schema=False)
