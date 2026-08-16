# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

FROM python:3.11-slim

WORKDIR /app

# Create non-root user and docker group (GID adjusted at runtime by entrypoint.sh)
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    groupadd -g 999 docker 2>/dev/null || true && \
    usermod -aG docker appuser

# Install build dependencies + gosu (used by entrypoint.sh to drop privileges)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better layer caching)
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy application files with correct ownership
COPY --chown=appuser:appuser . .

# Copy entrypoint (must be owned by root and executable)
RUN chmod +x /app/entrypoint.sh

# Create log directory and assign ownership
RUN mkdir -p /monitoring-service/logs \
    && chown -R appuser:appuser /monitoring-service

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    CPU_THRESHOLD_PERCENT=85 \
    CPU_DURATION_SECONDS=60 \
    MEM_THRESHOLD_PERCENT=85 \
    MEM_DURATION_SECONDS=60 \
    RESOURCE_CHECK_INTERVAL_SECONDS=30 \
    DOCKER_CHECK_INTERVAL_SECONDS=60 \
    DOCKER_STARTUP_GRACE_PERIOD_SECONDS=300 \
    ALERT_COOLDOWN_SECONDS=600 \
    ALERT_TRANSPORT=log \
    MONITOR_LOG_DIR=/monitoring-service/logs \
    MONITOR_LOG_LEVEL=INFO

# Entrypoint runs as root to fix docker socket GID, then drops to appuser via gosu
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "app.main"]
