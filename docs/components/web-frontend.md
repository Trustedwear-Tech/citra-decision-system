<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Web Frontend (Citra-UI) — Deployment Guide

Citra-UI is the web frontend built with React Native (Expo for Web). It compiles to a static site served by Nginx inside a Docker container.

## Local Development

No Docker needed — run directly:

```bash
cd Citra-UI
npm install
npx expo start --web
```

Config comes from `Citra-UI/.env` (local file, not committed). Copy `.env.example` and adjust:
```bash
cp .env.example .env
```

## Docker Deployment (Self-Hosted / Enterprise)

The Docker image builds with `EXPO_PUBLIC_ENVIRONMENT=self-hosted` by default. At container startup, the entrypoint injects runtime configuration from `CITRA_*` environment variables into the app — **no rebuild needed** when URLs change.

### Option A: Pull Pre-Built Image

```bash
docker run -d -p 80:80 \
  -e CITRA_API_URL=https://api.yourdomain.com \
  -e CITRA_USER_SERVICE_URL=https://api.yourdomain.com/user-service \
  ghcr.io/trustedwear-tech/citra-ui:latest
```

### Option B: Docker Compose (Modular)

The web compose file lives in the Citra-UI project folder:

```bash
docker compose -f Citra-UI/docker-compose.web.yml --env-file .env up -d
```

Or with pre-built images:
```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.prebuilt.yml \
  --env-file ../../.env up -d
```

### Option C: Build from Source

```bash
cd Citra-UI
docker build -f Dockerfile.web -t citra-ui .
docker run -d -p 80:80 \
  -e CITRA_API_URL=https://api.yourdomain.com \
  citra-ui
```

### Option D: Static Hosting (No Docker)

```bash
cd Citra-UI
npm install
EXPO_PUBLIC_ENVIRONMENT=self-hosted \
EXPO_PUBLIC_CITRA_API_URL=https://api.yourdomain.com \
npx expo export --platform web
```

The `dist/` folder contains static HTML/JS/CSS. Host on any static server:
AWS S3 + CloudFront, Azure Blob + CDN, Vercel, Netlify, Caddy, Apache, etc.

## Runtime Environment Variables

These are passed as Docker `-e` flags or in your compose `.env` file. The entrypoint (`docker-entrypoint.sh`) injects them into the app at container startup.

### Core URLs (Required)

| Docker Env Var | Purpose | Default (compose) |
|---|---|---|
| `CITRA_API_URL` | citra-service API | `http://citra-service:7001` |
| `CITRA_USER_SERVICE_URL` | Authentication & user management | `http://citra-user-service:7004` |
| `CITRA_AUTH_BASE_URL` | Auth API base path | `http://citra-user-service:7004/api/auth` |

### Optional

| Docker Env Var | Purpose |
|---|---|
| `CITRA_GOOGLE_CLIENT_ID` | Google OAuth Web Client ID (if using Google sign-in) |
| `CITRA_SENTRY_DSN` | Sentry error tracking DSN |
| `CITRA_MIXPANEL_TOKEN` | Mixpanel analytics token |
| `CITRA_GA_MEASUREMENT_ID` | Google Analytics 4 Measurement ID |

### Example: Enterprise with Reverse Proxy

When using Traefik (see [reverse-proxy.md](reverse-proxy.md)), all services are behind one domain:

```bash
docker run -d -p 80:80 \
  -e CITRA_API_URL=https://citra.acme.com \
  -e CITRA_USER_SERVICE_URL=https://citra.acme.com/user-service \
  -e CITRA_AUTH_BASE_URL=https://citra.acme.com/user-service/api/auth \
  -e CITRA_GOOGLE_CLIENT_ID=123456.apps.googleusercontent.com \
  ghcr.io/trustedwear-tech/citra-ui:latest
```

```
Browser → Traefik (/) → Citra-UI static files
                (/citra-ai/*) → Citra-Service:7001
                (/user-service/*) → User-Service:7004
```

## How Runtime Injection Works

1. `Dockerfile.web` builds the Expo app with `EXPO_PUBLIC_ENVIRONMENT=self-hosted`
2. At container startup, `docker-entrypoint.sh` reads `CITRA_*` env vars
3. It generates a `<script>window.__CITRA_ENV__={...}</script>` tag
4. This is injected into `index.html` before the app loads
5. `config/config.js` reads `window.__CITRA_ENV__` and uses it to override build-time defaults

For local development (`npx expo start`), `window.__CITRA_ENV__` does not exist — the app uses `EXPO_PUBLIC_*` from `.env` files as usual. Zero impact on the dev workflow.

## Health Check

The container exposes `GET /healthz` on port 80 which returns `200 OK`.
