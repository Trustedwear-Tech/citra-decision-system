# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

FROM python:3.11-slim

# Build context = monorepo root. requirements.txt has `-e ../citra-common/citra-service-utils`.

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    moreutils \
    dumb-init \
    && rm -rf /var/lib/apt/lists/*

COPY citra-common/citra-service-utils /app/citra-common/citra-service-utils

COPY duckdb-query-service/requirements.txt /app/duckdb-query-service/requirements.txt
WORKDIR /app/duckdb-query-service
RUN pip install --retries 10 --timeout 60 --no-cache-dir -r requirements.txt

COPY duckdb-query-service /app/duckdb-query-service

# ⚠️ SINGLE-REPLICA SERVICE — the in-memory TablePool (per-user DuckDB tables)
# is NOT shared across instances. Run exactly ONE replica, or front it with
# user-sticky routing, or per-user table state goes split-brain. `-w 1` below is
# intentional (one event loop per process); scaling is vertical, not horizontal,
# until the table state is externalized.
EXPOSE 7301

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7301/health || exit 1

CMD ["gunicorn", "main:app", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", \
    "--bind", "0.0.0.0:7301", "--timeout", "120", "--graceful-timeout", "10", \
    "--access-logfile", "-"]
