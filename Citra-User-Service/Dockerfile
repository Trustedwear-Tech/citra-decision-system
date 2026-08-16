# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# syntax=docker/dockerfile:1
FROM node:22-bullseye-slim

# Build-time arg to mark environment (prod/test)
ARG APP_ENV=prod
ENV APP_ENV=${APP_ENV}

RUN apt-get update \
 && apt-get install -y --no-install-recommends dumb-init curl moreutils \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
#RUN useradd -m -u 1000 -s /bin/bash nodeuser

WORKDIR /app

COPY package*.json ./
# package-lock.json is currently out of sync with package.json (e.g. missing
# entries for gcp-metadata, gaxios). `npm ci` would fail. Fall back to
# `npm install` so the build succeeds; regenerate the lock file with
# `npm install` locally and commit to restore reproducible CI builds.
RUN npm ci || npm install

COPY --chown=node:node . .

RUN mkdir -p /app/logs && chown -R node:node /app

USER node

ENV PORT=7004
ENV NODE_ENV=production

EXPOSE 7004

# Healthcheck
HEALTHCHECK --interval=1m --timeout=5s --start-period=20s --retries=5 CMD curl -fsS http://localhost:7004/health || curl -fsS http://localhost:7004/ || exit 1

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["sh", "-c", "npm run start 2>&1 | tee -a /app/logs/${APP_ENV}_1.log"]
