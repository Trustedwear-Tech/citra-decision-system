# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

FROM python:3.11-slim

# Build context = monorepo root. requirements.txt has `-e ../citra-common/citra-service-utils`
# (shared PII/semantic-type classifier + utils) which only resolves if the
# sibling is at /app/citra-common/citra-service-utils relative to WORKDIR
# /app/data-discovery-service. Mirrors discovery-service/Dockerfile.

WORKDIR /app

COPY citra-common/citra-service-utils /app/citra-common/citra-service-utils

COPY data-discovery-service/requirements.txt /app/data-discovery-service/requirements.txt
WORKDIR /app/data-discovery-service
RUN pip install --no-cache-dir -r requirements.txt

COPY data-discovery-service /app/data-discovery-service

EXPOSE 8095

# Safe to scale now: the API is stateless over Mongo and the background crawler
# is leader-locked (only one replica/worker crawls per pass). Set WEB_CONCURRENCY
# (uvicorn CLI reads it as the --workers default) per box; size Mongo pool to
# workers × replicas.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8095"]
