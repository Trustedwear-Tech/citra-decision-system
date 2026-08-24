<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Reverse Proxy / Load Balancer — Deployment Guide

Traefik provides a single entry point, TLS termination, rate limiting, load balancing, and WebSocket support for all Citra services.

## When You Need This

- **Production**: Always — provides TLS, rate limiting, security headers, load balancing
- **Development**: Optional — access services directly by port

## Route Table

| Path | Backend | Protocol |
|------|---------|----------|
| `/` | Citra-UI | HTTP |
| `/citra-ai/*` | Citra-Service (port 7001) | HTTP |
| `/api/auth/*` | User-Service (port 7004) | HTTP |
| `/duckdb/*` | DuckDB Query (port 7301) | HTTP |

## Option A: Docker Compose

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.proxy.yml \
  --env-file ../../.env up -d
```

This starts Traefik v3.1 on ports 80 and 443. The configuration is split into:
- **Static config**: `infrastructure/traefik/traefik.yml` — entrypoints, providers, logging
- **Dynamic config**: `infrastructure/traefik/dynamic.yml` — routers, services, middlewares

### Customize routing

Edit `infrastructure/traefik/dynamic.yml` to change backend URLs. When services are on different machines, replace Docker service names with IPs:

```yaml
# Before (same Docker network)
servers:
  - url: "http://citra-service:7001"

# After (service on another machine)
servers:
  - url: "http://10.0.1.20:7001"
```

### Enable TLS

1. **Automatic (Let's Encrypt):** Uncomment the `certificatesResolvers` section in `infrastructure/traefik/traefik.yml` and set `DOMAIN` and `ACME_EMAIL` in `.env`.
2. **Manual certificates:** Place `fullchain.pem` and `privkey.pem` in `infrastructure/traefik/certs/` and reference them in the dynamic config.

## Option B: Self-Managed Traefik

Install Traefik on any server:

```bash
# Download Traefik binary
curl -sSL https://github.com/traefik/traefik/releases/latest/download/traefik_linux_amd64.tar.gz | tar xz
sudo mv traefik /usr/local/bin/
sudo cp infrastructure/traefik/traefik.yml /etc/traefik/traefik.yml
sudo cp infrastructure/traefik/dynamic.yml /etc/traefik/dynamic.yml
sudo traefik --configFile=/etc/traefik/traefik.yml
```

Edit `/etc/traefik/dynamic.yml` to point services to your backend hosts.

## Option C: Other Reverse Proxies

The Traefik config can be translated to:
- **Caddy** — automatic TLS with Let's Encrypt
- **HAProxy** — high-performance TCP/HTTP proxy
- **Cloud Load Balancers** — AWS ALB, GCP Load Balancer, Azure Application Gateway

Key requirements for any proxy:
- WebSocket support for `/ws/collab` path
- Max request body size of at least 100MB (file uploads)
- Proper `X-Forwarded-For`, `X-Forwarded-Proto` headers

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOMAIN` | For TLS | — | Domain name for certificate |
| `ACME_EMAIL` | For TLS | — | Email for Let's Encrypt |
| `FORCE_HTTPS` | Production | `false` | Set `true` behind TLS proxy |
| `CORS_ALLOWED_ORIGINS` | Yes | `http://localhost:8081` | Comma-separated CORS origins |

## Rate Limits (Default)

The provided Traefik config includes:
- **API**: 30 requests/second per IP (burst 50)
- **Auth**: 10 requests/second per IP (burst 20)
- Security headers: `X-Frame-Options` (deny), `X-Content-Type-Options` (nosniff), `X-XSS-Protection`
- gzip compression for text/JSON responses
