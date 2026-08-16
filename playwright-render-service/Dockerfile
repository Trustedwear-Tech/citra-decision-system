# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# Build context = monorepo root. requirements.txt has `-e ../citra-common/citra-service-utils`.

WORKDIR /app

# Install system dependencies (CRITICAL)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY citra-common/citra-service-utils /app/citra-service-utils

COPY playwright-render-service/requirements.txt /app/playwright-render-service/requirements.txt
WORKDIR /app/playwright-render-service
RUN pip install --no-cache-dir -r requirements.txt

COPY playwright-render-service /app/playwright-render-service

# Create logs directory
RUN mkdir -p /logs && touch /logs/playwright-render-service.log

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3001/health || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 3001 --workers 1 --loop uvloop --http httptools 2>&1 | tee -a /logs/playwright-render-service.log"]
