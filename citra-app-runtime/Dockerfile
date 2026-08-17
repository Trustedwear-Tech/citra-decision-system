# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Next.js' standalone output expects a `public/` directory to exist even if empty.
RUN mkdir -p public
# The embeddable decision UI (public/v1/citra.js). Built BEFORE `next build` so
# it is present when the runner stage copies public/ across — `public/` is not
# part of Next's standalone trace, which is why that COPY exists below.
RUN npm run build:embed
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3100
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
# Vault bootstrap wrapper: fetches secrets at boot, then requires server.js.
# Not part of Next's standalone trace, so it must be copied explicitly.
COPY --from=builder --chown=nextjs:nodejs /app/vault-bootstrap.js ./vault-bootstrap.js
USER nextjs
EXPOSE 3100
CMD ["node", "vault-bootstrap.js"]
