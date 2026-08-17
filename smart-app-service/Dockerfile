# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

FROM python:3.11-slim

# Build context = monorepo root. requirements.txt has editable local deps:
# `-e ../citra-common/citra-service-utils`, `-e ../citra-common/citra-auth`, `-e ../citra-common/citra-cache`, and
# `-e ../citra-common/citra-queue` (the shared durable job queue) — each must be COPYed
# into the build context before the pip install below.

WORKDIR /app

COPY citra-common/citra-service-utils /app/citra-common/citra-service-utils
COPY citra-common/citra-auth /app/citra-common/citra-auth
COPY citra-common/citra-cache /app/citra-common/citra-cache
COPY citra-common/citra-queue /app/citra-common/citra-queue

COPY smart-app-service/requirements.txt /app/smart-app-service/requirements.txt
WORKDIR /app/smart-app-service
RUN pip install --no-cache-dir -r requirements.txt

COPY smart-app-service /app/smart-app-service

EXPOSE 9100

# Worker count via WEB_CONCURRENCY (uvicorn CLI reads it as the --workers
# default). Default 2 (not 1): a single Decision-App /run is 1-2 min of
# sequential LLM iterations, and with one worker that run starves every
# concurrent request (accept-backlog fills → new connections refused until it
# finishes). Safe to scale: per-replica singletons are leader-elected
# (scheduler/sweeps), the trigger-queue consumer joins a Redis Streams consumer
# group (load-balances across workers), and the LLM rate-limit + proxy budget
# live on the durable coordination Redis. Override per box; size Mongo pool to
# workers × replicas. Baked here so prod gets it even if the on-box compose drifts.
ENV WEB_CONCURRENCY=2
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9100"]
