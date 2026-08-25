# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

# syntax=docker/dockerfile:1.6
#
# Citra Action Sandbox Host (scheduler)
# =====================================
# A thin FastAPI service that runs on each sandbox-host VM. It owns the
# local Docker daemon and provides a small HTTP API (spawn / stop / upload
# / capacity) consumed by the stateless Citra-Service fleet.
#
# Why a separate image from the sandbox itself?
# - Different lifecycle (long-running host daemon vs per-user ephemeral).
# - Different privileges (this image MUST mount the Docker socket; the
#   sandbox image MUST NOT).
#
# Deployment per host:
#   docker compose up -d
# with `/var/run/docker.sock` mounted into this container (see
# docker-compose.yml). Expose port 7090 on the private network Citra-Service
# uses — do NOT expose it to the internet.

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --retries 10 --timeout 60 --no-cache-dir -r /app/requirements.txt \
 && pip install --retries 10 --timeout 60 --no-cache-dir psutil==6.1.0

COPY config.py           /app/config.py
COPY models.py           /app/models.py
COPY scheduler.py        /app/scheduler.py
COPY tiers.py            /app/tiers.py
COPY vault_bootstrap.py  /app/vault_bootstrap.py
COPY main.py             /app/main.py

# Non-root. Docker socket access is granted by joining the host's `docker`
# group at runtime (see docker-compose.yml: group_add).
RUN useradd --system --uid 1600 --home-dir /app --shell /bin/sh sandboxhost \
 && chown -R sandboxhost:sandboxhost /app
USER sandboxhost

EXPOSE 7090

ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "main"]
