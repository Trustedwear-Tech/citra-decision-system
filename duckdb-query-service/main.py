# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
DuckDB Query Service — Centralized analytical SQL engine.

All Citra-Service instances call this single service instead of running
their own ephemeral DuckDB pipelines. Tables are kept persistent in
memory with TTL-based eviction.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from vault_env_loader import load_environment_variables

# Load secrets before anything reads os.environ
load_environment_variables()

from config import config  # noqa: E402
from models import (  # noqa: E402
    AnalyticalQueryRequest,
    EvictRequest,
    HealthResponse,
    PerTableQueryRequest,
    PoolStatusResponse,
    QueryResponse,
    TableInfo,
)
from sql_engine import run_analytical_query, run_per_table_fetch  # noqa: E402
from table_pool import get_table_pool  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)
 
 
# Filter out /health from uvicorn access logs
class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # returns True to keep the record, False to drop it
        return record.getMessage().find("/health") == -1
 
 
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = get_table_pool()
    pool.start_cleanup_loop()
    # ⚠️ SINGLE-REPLICA SERVICE. The TablePool (per-user DuckDB connections +
    # loaded tables) is an in-process, in-memory singleton with no shared/sticky
    # backing. With >1 replica a user's tables exist only on whichever replica
    # loaded them, so a follow-up query routed elsewhere finds nothing (or
    # re-loads from Mongo), and MAX_TABLES/TTL caps are per-process. Run exactly
    # ONE replica, or front it with sticky-by-user routing, until the table state
    # is externalized. This is logged loudly so a mis-scaled deploy is visible.
    logger.warning(
        "DuckDB Query Service is SINGLE-REPLICA by design (in-memory TablePool, "
        "no shared/sticky backing) — do NOT run >1 replica without user-sticky "
        "routing, or per-user table state will be split-brain."
    )
    # Log connection targets for debugging
    mongo_host = config.MONGODB_URI.split("@")[-1].split("/")[0] if "@" in config.MONGODB_URI else config.MONGODB_URI.split("//")[-1].split("/")[0]
    logger.info("DuckDB Query Service started (port=%d, table_ttl=%ds, max_tables=%d)",
                config.PORT, config.TABLE_TTL, config.MAX_TABLES)
    logger.info("MongoDB: db=%s, host=%s | Redis: %s:%d",
                config.MONGODB_DATABASE, mongo_host, config.REDIS_HOST, config.REDIS_PORT)
    yield
    await pool.shutdown()


# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("duckdb-query-service")
except Exception as _exc:
    import logging as _l
    _l.getLogger(__name__).warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]

app = FastAPI(
    title="DuckDB Query Service",
    version="1.0.0",
    lifespan=lifespan,
)

# Prometheus /metrics endpoint
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except ImportError:
    import logging as _l
    _l.getLogger(__name__).warning("prometheus-fastapi-instrumentator not installed; /metrics disabled")

if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception:
        pass

# Distributed tracing — OTel spans → Tempo
try:
    from citra_service_utils import setup_tracing as _setup_tracing, request_id_middleware as _request_id_middleware
    app.middleware("http")(_request_id_middleware)
    _setup_tracing(app, service_name="duckdb-query-service")
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")


# ── Routes ──

@app.post("/api/analytical-query", response_model=QueryResponse)
async def analytical_query(req: AnalyticalQueryRequest):
    """Run an analytical SQL query (AGGREGATE / TREND / SPECIFIC)."""
    try:
        result = await run_analytical_query(
            query=req.query,
            user_id=req.user_id,
            document_ids=req.document_ids,
            vector_samples=req.vector_samples,
            chart_type=req.chart_type,
            schema_metadata_map=req.schema_metadata_map,
            force_aggregate=req.force_aggregate,
        )
        return QueryResponse(**result)
    except Exception as e:
        logger.error("analytical-query error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/per-table-query", response_model=QueryResponse)
async def per_table_query(req: PerTableQueryRequest):
    """Run per-table comparison query (COMPARE)."""
    try:
        result = await run_per_table_fetch(
            query=req.query,
            user_id=req.user_id,
            document_ids=req.document_ids,
            vector_samples=req.vector_samples,
            chart_type=req.chart_type,
            schema_metadata_map=req.schema_metadata_map,
        )
        return QueryResponse(**result)
    except Exception as e:
        logger.error("per-table-query error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tables/{user_id}", response_model=PoolStatusResponse)
async def get_user_tables(user_id: str):
    """List tables currently loaded for a user."""
    pool = get_table_pool()
    tables = pool.get_user_tables(user_id)
    return PoolStatusResponse(
        user_id=user_id,
        tables=[
            TableInfo(
                table_name=t["table_name"],
                document_id=t["document_id"],
                filename=t["filename"],
                row_count=t["row_count"],
                columns=t["columns"],
                loaded_at=t["loaded_at"],
                last_accessed=t["last_accessed"],
            )
            for t in tables
        ],
        total_tables=len(tables),
        total_rows=sum(t["row_count"] for t in tables),
    )


@app.post("/api/evict")
async def evict_tables(req: EvictRequest):
    """Evict tables from pool (specific docs or all for a user)."""
    pool = get_table_pool()
    await pool.evict(req.user_id, req.document_ids)
    return {"status": "ok"}


@app.get("/health", response_model=HealthResponse)
async def health():
    pool = get_table_pool()
    return HealthResponse(
        status="ok",
        pool_tables=pool.total_tables,
        pool_users=pool.total_users,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
