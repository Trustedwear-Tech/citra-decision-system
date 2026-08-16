<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Redis — Deployment Guide

Redis is used for caching, session storage, and rate limiting across Citra services.

## Requirements

- Redis 7.0+
- No persistence required (cache only) — persistence is optional

## Option A: Cloud Managed

**AWS ElastiCache:**
1. Create a Redis cluster in your AWS console
2. Set in `.env`:
   ```env
   REDIS_HOST=your-cluster.xxxxx.cache.amazonaws.com
   REDIS_PORT=6379
   REDIS_PASSWORD=your-auth-token
   REDIS_SSL=true
   ```

**Upstash (serverless):**
1. Create a database at [upstash.com](https://upstash.com)
2. Set in `.env`:
   ```env
   REDIS_HOST=your-endpoint.upstash.io
   REDIS_PORT=6379
   REDIS_PASSWORD=your-password
   REDIS_SSL=true
   ```

**Azure Cache for Redis:**
```env
REDIS_HOST=your-cache.redis.cache.windows.net
REDIS_PORT=6380
REDIS_PASSWORD=your-access-key
REDIS_SSL=true
```

## Option B: Docker (Single Machine)

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.redis.yml \
  --env-file ../../.env up -d
```

Default config:
```env
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_SSL=false
```

> When running Citra services in the same Docker Compose network, use `redis` instead of `localhost` as the host.

## Option C: Self-Managed Server

```bash
# Ubuntu/Debian
sudo apt-get install -y redis-server
sudo systemctl enable redis-server && sudo systemctl start redis-server
```

Edit `/etc/redis/redis.conf` for remote access:
```
bind 0.0.0.0
requirepass your-strong-password
```

Set in `.env` on your Citra service machines:
```env
REDIS_HOST=<redis-server-ip>
REDIS_PORT=6379
REDIS_PASSWORD=your-strong-password
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_HOST` | Yes | `redis` | Redis hostname or IP |
| `REDIS_PORT` | No | `6379` | Redis port |
| `REDIS_PASSWORD` | No | (empty) | Auth password |
| `REDIS_SSL` | No | `false` | Enable TLS connection |

## Verification

```bash
# Local
redis-cli -h localhost ping
# PONG

# Remote with password
redis-cli -h <host> -p <port> -a <password> ping

# Setup validator
./scripts/setup.sh --check
```
