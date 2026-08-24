# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Reranker Service — Cross-encoder reranking microservice.

Selects exactly ONE cross-encoder model for the runtime based on hardware:
  - GPU present  -> heavy model (RERANKER_MODEL_GPU, e.g. BAAI/bge-reranker-v2-m3)
  - CPU only     -> lightweight model (RERANKER_MODEL_CPU, e.g. ms-marco-MiniLM-L-6-v2)
An explicit RERANKER_MODEL override (env/Vault) wins if set, but should NOT be
set in prod — it bypasses hardware-aware selection and can force the heavy GPU
model onto a CPU host (high RAM + ~100x slower inference).

The model is loaded ONCE at import time (module level) so that under
gunicorn `--preload` it lives in the master process and is shared copy-on-write
across forked workers — instead of each worker loading its own copy.

Exposes a POST /rerank endpoint. Called by Citra-Service to re-score Milvus
chunks against the user query for better relevance ranking.
"""
# Vault bootstrap MUST be first — populates SENTRY_DSN / OTEL_* before
# observability.init_sentry() and tracing wiring read them. Fails fast in
# prod if Vault is unreachable. In local dev (VAULT_ADDR unset) it's a no-op.
import os
if os.getenv("VAULT_ADDR"):
    from citra_service_utils import load_from_vault
    load_from_vault()

# Disable HF tokenizers' internal parallelism BEFORE the model is loaded. The
# model is loaded pre-fork (under gunicorn --preload); leaving tokenizers
# parallelism on across a fork triggers deadlock warnings / hangs.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import logging
import time
from contextlib import asynccontextmanager

import numpy as np
import requests
import torch
from fastapi import FastAPI, HTTPException
from sentence_transformers import CrossEncoder

from config import (
    PORT,
    RERANK_API_KEY,
    RERANK_API_MODEL,
    RERANK_API_TIMEOUT,
    RERANK_API_URL,
    RERANKER_MODEL,
    RERANKER_MODEL_CPU,
    RERANKER_MODEL_GPU,
    RERANKER_PROVIDER,
)
from models import (
    ChunkInput,
    ChunkOutput,
    HealthResponse,
    RerankRequest,
    RerankResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


# Filter out /health endpoint logs to keep production logs clean
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find("/health") == -1


# Apply filter to both uvicorn and gunicorn access loggers
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
logging.getLogger("gunicorn.access").addFilter(HealthCheckFilter())

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_cross_encoder: CrossEncoder | None = None
_device: str = "cpu"
_active_model: str = ""


def _detect_device() -> str:
    """Try GPU first, fall back to CPU."""
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        logger.info(f"🟢 CUDA GPU detected: {gpu_name}")
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        logger.info("🟢 Apple MPS GPU detected")
        return "mps"
    logger.info("🟡 No GPU detected — using CPU")
    return "cpu"


def _select_model(device: str) -> str:
    """Pick the right model for the device. Explicit env override wins."""
    if RERANKER_MODEL:
        logger.info(f"🔧 Using explicit RERANKER_MODEL override: {RERANKER_MODEL}")
        return RERANKER_MODEL
    if device in ("cuda", "mps"):
        logger.info(f"🚀 GPU detected → using heavy model: {RERANKER_MODEL_GPU}")
        return RERANKER_MODEL_GPU
    logger.info(f"💡 CPU detected → using lightweight model: {RERANKER_MODEL_CPU}")
    return RERANKER_MODEL_CPU


def _load_model() -> None:
    """Load the single device-appropriate cross-encoder model.

    Idempotent: if a model is already loaded (e.g. preloaded at import time in
    the gunicorn master), this is a no-op so workers don't reload after fork.
    """
    global _cross_encoder, _device, _active_model

    if _cross_encoder is not None:
        return

    _device = _detect_device()
    _active_model = _select_model(_device)
    t0 = time.time()
    logger.info(f"🔄 Loading reranker model: {_active_model} on {_device} ...")

    try:
        _cross_encoder = CrossEncoder(_active_model, device=_device)
        logger.info(
            f"✅ Reranker model loaded on {_device} in {time.time() - t0:.1f}s"
        )
    except Exception as gpu_err:
        if _device != "cpu":
            logger.warning(
                f"⚠️ Failed to load model on {_device}: {gpu_err} — falling back to CPU"
            )
            _device = "cpu"
            _active_model = _select_model(_device)
            _cross_encoder = CrossEncoder(_active_model, device="cpu")
            logger.info(
                f"✅ Reranker model loaded on CPU (fallback) in {time.time() - t0:.1f}s"
            )
        else:
            raise


# ---------------------------------------------------------------------------
# Pre-fork model load. This runs at IMPORT time — i.e. in the gunicorn master
# process under `--preload`, BEFORE workers are forked. The loaded weights are
# then shared copy-on-write across workers instead of duplicated per worker.
# Idempotent (see _load_model), so the lifespan call below is a safe no-op in
# preloaded deployments and the real load in non-preloaded dev (uvicorn reload).
# Skipped entirely for the remote provider — no local weights needed.
# ---------------------------------------------------------------------------
if RERANKER_PROVIDER == "local":
    _load_model()


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    if RERANKER_PROVIDER == "local":
        _load_model()  # no-op if already preloaded in the master; loads in dev
        logger.info(f"✅ Reranker service ready — model={_active_model}, device={_device}, port={PORT}")
    else:
        logger.info(
            f"✅ Reranker service ready — provider=remote, model={RERANK_API_MODEL}, "
            f"url={RERANK_API_URL}, port={PORT}"
        )
    yield
    logger.info("Reranker service shutting down")


# Error tracker (GlitchTip / Sentry) — no-op unless SENTRY_DSN is set
try:
    from observability import init_sentry, install_trace_id_middleware
    init_sentry("reranker-service")
except Exception as _exc:
    logger.warning(f"[sentry] init skipped: {_exc}")
    install_trace_id_middleware = None  # type: ignore[assignment]

app = FastAPI(
    title="Reranker Service",
    version="1.0.0",
    lifespan=lifespan,
)

# Prometheus /metrics endpoint (request rate, latency, errors per route).
# Scraped by Prometheus job 'reranker-service'.
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not installed; /metrics disabled")

if install_trace_id_middleware is not None:
    try:
        install_trace_id_middleware(app)
    except Exception as _exc:
        logger.warning(f"[sentry] trace_id middleware skipped: {_exc}")

# Distributed tracing — OTel spans → Tempo (X-Request-ID propagation on all outbound calls)
try:
    from citra_service_utils import setup_tracing as _setup_tracing, request_id_middleware as _request_id_middleware
    app.middleware("http")(_request_id_middleware)
    _setup_tracing(app, service_name="reranker-service")
except ImportError:
    logger.warning("citra-service-utils not installed; distributed tracing disabled")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health():
    if RERANKER_PROVIDER == "remote":
        return HealthResponse(
            status="ok",
            model=RERANK_API_MODEL,
            device="remote",
            model_loaded=True,  # remote provider — nothing to load locally
        )
    return HealthResponse(
        status="ok",
        model=_active_model,
        device=_device,
        model_loaded=_cross_encoder is not None,
    )


def _rerank_remote(req: RerankRequest) -> RerankResponse:
    """Score chunks via an external rerank API (Cohere-style shape — served
    identically by Fireworks and OpenRouter).

    Context limits apply per query+document pair and the provider truncates
    oversized pairs itself, so chunks are sent whole. Any provider error fails
    loud as a 502 — there is deliberately NO fallback to the local model (a
    silent model swap would change ranking behaviour unnoticed).
    """
    t0 = time.time()
    try:
        resp = requests.post(
            RERANK_API_URL,
            headers={"Authorization": f"Bearer {RERANK_API_KEY}"},
            json={
                "model": RERANK_API_MODEL,
                "query": req.query,
                "documents": [c.text for c in req.chunks],
                "top_n": req.top_k,
            },
            timeout=RERANK_API_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error(f"❌ Remote rerank transport error: {exc}")
        raise HTTPException(status_code=502, detail=f"Remote rerank unreachable: {exc}")

    if resp.status_code != 200:
        logger.error(
            f"❌ Remote rerank HTTP {resp.status_code}: {resp.text[:300]}"
        )
        raise HTTPException(
            status_code=502,
            detail=f"Remote rerank failed: HTTP {resp.status_code}: {resp.text[:300]}",
        )

    results = resp.json().get("results")
    if not isinstance(results, list):
        raise HTTPException(
            status_code=502,
            detail=f"Remote rerank returned no results field: {resp.text[:300]}",
        )

    scored: list[ChunkOutput] = []
    for r in results:
        chunk = req.chunks[r["index"]]
        score = float(r["relevance_score"])
        scored.append(
            ChunkOutput(
                id=chunk.id,
                text=chunk.text,
                score=score,
                original_score=chunk.score,
                reranker_raw_score=score,
                metadata=chunk.metadata,
            )
        )
    # OpenRouter returns results sorted by relevance and already top_n-capped;
    # sort + slice again so the contract never depends on provider behaviour.
    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[: req.top_k]

    elapsed_ms = (time.time() - t0) * 1000
    logger.info(
        f"✅ Reranked {len(req.chunks)} → {len(top)} chunks in {elapsed_ms:.1f}ms "
        f"(provider=remote, model={RERANK_API_MODEL}, "
        f"top={top[0].score:.4f}, bottom={top[-1].score:.4f})"
    )
    return RerankResponse(
        reranked_chunks=top,
        model=RERANK_API_MODEL,
        device="remote",
        elapsed_ms=elapsed_ms,
    )


# Sync (non-async) on purpose: cross-encoder inference is CPU/GPU-bound and
# would block the event loop in an async handler, freezing /health and every
# concurrent request on this worker. As a sync route FastAPI runs it in the
# threadpool, so the loop stays responsive; scale throughput via
# GUNICORN_WORKERS (torch inference is thread-safe for forward passes).
@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    if not req.chunks:
        return RerankResponse(
            reranked_chunks=[],
            model=RERANK_API_MODEL if RERANKER_PROVIDER == "remote" else _active_model,
            device="remote" if RERANKER_PROVIDER == "remote" else _device,
            elapsed_ms=0.0,
        )

    if RERANKER_PROVIDER == "remote":
        return _rerank_remote(req)

    if _cross_encoder is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    t0 = time.time()

    # Build (query, passage) pairs
    pairs = [(req.query, chunk.text) for chunk in req.chunks]

    # Score with cross-encoder
    raw_scores = _cross_encoder.predict(pairs)

    # Normalise via sigmoid to [0, 1]
    norm_scores = 1.0 / (1.0 + np.exp(-raw_scores))

    # Build output list with scores attached
    scored: list[ChunkOutput] = []
    for chunk, ns, rs in zip(req.chunks, norm_scores, raw_scores):
        scored.append(
            ChunkOutput(
                id=chunk.id,
                text=chunk.text,
                score=float(ns),
                original_score=chunk.score,
                reranker_raw_score=float(rs),
                metadata=chunk.metadata,
            )
        )

    # Sort descending by reranker score, take top_k
    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[: req.top_k]

    elapsed_ms = (time.time() - t0) * 1000
    logger.info(
        f"✅ Reranked {len(req.chunks)} → {len(top)} chunks in {elapsed_ms:.1f}ms "
        f"(device={_device}, top={top[0].score:.4f}, bottom={top[-1].score:.4f})"
    )

    return RerankResponse(
        reranked_chunks=top,
        model=_active_model,
        device=_device,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info(f"🚀 Starting Reranker Service on 0.0.0.0:{PORT}")
    logger.info(f"📦 Model: {RERANKER_MODEL}")
    logger.info(f"🔗 Health check: http://localhost:{PORT}/health")
    logger.info(f"🔗 Rerank endpoint: http://localhost:{PORT}/rerank")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        log_level="info",
    )
