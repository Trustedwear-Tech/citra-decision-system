# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# Dockerfile for Citra AI Service
FROM python:3.11-slim

# Build-time argument to mark environment (prod/test)
ARG APP_ENV=prod

# gRPC fork-safety: GRPC_ENABLE_FORK_SUPPORT ensures safe forking.
# GRPC_POLL_STRATEGY=epoll1 uses efficient Linux epoll instead of busy-polling.
# With -w 1 (single worker), no forking occurs so SIGSEGV risk is eliminated.
# C protobuf extension is safe with single worker (no fork = no segfault).
ENV PYTHONUNBUFFERED=1 \
    TZ=America/New_York \
    DEBIAN_FRONTEND=noninteractive \
  APP_ENV=${APP_ENV} \
    GRPC_ENABLE_FORK_SUPPORT=1 \
    GRPC_POLL_STRATEGY=epoll1

# Install minimal system deps in one layer, remove apt lists
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    tzdata \
    libsnappy-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first
COPY Citra-Service/requirements.txt /app/Citra-Service/requirements.txt

# Copy editable dependency packages (must match `-e ../citra-X` in requirements.txt:
#   citra-service-utils, citra-queue, citra-auth, citra-mongo, citra-cache)
COPY citra-common/citra-service-utils /app/citra-service-utils
COPY citra-common/citra-queue /app/citra-queue
COPY citra-common/citra-auth /app/citra-auth
COPY citra-common/citra-mongo /app/citra-mongo
COPY citra-common/citra-cache /app/citra-cache
COPY citra-common/citra-llm /app/citra-llm

# Switch into service dir
WORKDIR /app/Citra-Service

# Install deps
RUN pip install -r requirements.txt

# Copy service source LAST for caching
COPY Citra-Service /app/Citra-Service

RUN mkdir -p /app/logs

EXPOSE 7001

# Healthcheck - tolerant of gunicorn worker restarts (max-requests recycling)
# With 1 worker, restarts cause brief unavailability (~15-20s)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 CMD curl -fsS http://127.0.0.1:7001/health >/dev/null || exit 1

# Gunicorn + Uvicorn workers. Worker count is env-tunable via WEB_CONCURRENCY
# (default 2). NOTE: this service also runs as 8 shards, so default 2 means
# ~16 worker processes total — size the Mongo pool (MONGODB_MAX_POOL_SIZE) and
# watch the DB connection ceiling accordingly. `exec` so gunicorn is PID 1 and
# receives SIGTERM directly (clean graceful shutdown).
CMD ["sh", "-c", "exec gunicorn main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:7001 --timeout 1200 --graceful-timeout 30 --access-logfile - --error-logfile - --capture-output --log-level info"]
