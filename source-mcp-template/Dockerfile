# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

FROM python:3.11-slim

# Build context = monorepo root. requirements.txt has `-e ../citra-common/citra-service-utils`.

# System deps for SQL Server ODBC (uncomment if your dept uses SQL Server sources)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     unixodbc-dev curl gnupg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY citra-common/citra-service-utils /app/citra-common/citra-service-utils

COPY source-mcp-template/requirements.txt /app/source-mcp-template/requirements.txt
WORKDIR /app/source-mcp-template
RUN pip install --retries 10 --timeout 60 --no-cache-dir -r requirements.txt

COPY source-mcp-template /app/source-mcp-template

# Source definitions are pulled at runtime from Citra-Service's central
# `dept_sources` Mongo collection. No sources.yaml mount needed.
EXPOSE 8090

# §1 — one uvicorn worker = one event loop on one core. To use more cores, set
# WEB_CONCURRENCY (uvicorn reads it as the default for --workers) sized to the
# container's vCPUs in the compose/k8s `environment`. This is now SAFE under
# multiple workers: the registration leader lock (REGISTER_LEADER_LOCK, §2/P3)
# dedups the per-worker registration POSTs, and the plan/count/embedding/
# selection/introspection caches are shared via Redis. Also right-size the DB
# pool (DB_POOL_SIZE / DB_MAX_OVERFLOW) and the offload threads
# (THREAD_POOL_MAX_WORKERS) per worker — see config.py.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]
