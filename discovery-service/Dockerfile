# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

FROM python:3.11-slim

# Build context = monorepo root. requirements.txt has `-e ../citra-common/citra-service-utils`
# which only resolves if the sibling is at /app/citra-common/citra-service-utils relative
# to WORKDIR /app/discovery-service.

WORKDIR /app

COPY citra-common/citra-service-utils /app/citra-common/citra-service-utils

COPY discovery-service/requirements.txt /app/discovery-service/requirements.txt
WORKDIR /app/discovery-service
RUN pip install --no-cache-dir -r requirements.txt

COPY discovery-service /app/discovery-service

EXPOSE 9000

# Stateless over shared Mongo → safe to scale. Set WEB_CONCURRENCY (the uvicorn
# CLI reads it as the default for --workers) to run N workers per box; size
# Mongo maxPoolSize to match workers × replicas.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
