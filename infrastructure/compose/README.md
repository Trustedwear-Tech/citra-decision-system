<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra platform compose files

Each file here defines a piece of **platform infrastructure** — Redis,
Mongo, Milvus, MinIO, Vault, proxy, observability — that is **not owned by
any one service**. Service composes (which live inside each service's own
project folder) consume these.

Postgres is **not** platform infrastructure anymore: it exists only inside
demo tenant stacks (`demo-data/tenants/*/mcp/docker-compose.yml`) and
bundled inside GlitchTip (`docker-compose.errortracker.yml`). The Superset
dashboard stack and the shared `citra-postgres` were decommissioned
2026-06-11.

## Files

| File | What it brings up | Consumed by |
|---|---|---|
| `docker-compose.base.yml` | Shared `citra-network` + base settings | every other compose |
| `docker-compose.redis.yml` | Redis 7 (`redis` cache + `queue-redis` durable job queue) | Citra-Service, action-chat-service, citra-workflow |
| `docker-compose.mongodb.yml` | Mongo 7 (replica set) | Citra-Service, action-chat-service |
| `docker-compose.milvus.yml` | Milvus 2.4 + etcd + MinIO | Citra-Service, action-chat-service |
| `docker-compose.minio.yml` | App-level S3-compat storage | Citra-Service, action-chat-service |
| `docker-compose.vault.yml` | Vault (dev mode) | all services at boot |
| `docker-compose.proxy.yml` | Traefik reverse proxy | ingress |
| `docker-compose.observability.yml` | Prometheus / Alertmanager / Loki / Tempo / Grafana / Promtail | central obs (app box) |
| `docker-compose.obs-agent.yml` | node-exporter + promtail agent | non-central boxes |
| `docker-compose.errortracker.yml` | GlitchTip (+ its own bundled Postgres/Redis) | exception tracking |
| `docker-compose.prebuilt.yml` | All app services from GHCR prebuilt images | local quickstart / canonical service inventory |

## Ownership rule

If a service needs a shared database / cache / object store, it does **not**
declare that store inside its own compose. It declares a *dependency*
on the platform compose, and provisions whatever it needs *inside* that
shared cluster (a database, a schema, a collection, a bucket prefix).

Service-specific build/runtime compose files live in the service's own
project folder (e.g. `Monitoring-Service/docker-compose.monitoring.yml`,
`Citra-UI/docker-compose.web.yml`), not here.

## Bring-up order

```bash
docker compose \
  -f infrastructure/compose/docker-compose.base.yml \
  -f infrastructure/compose/docker-compose.mongodb.yml \
  -f infrastructure/compose/docker-compose.redis.yml \
  -f infrastructure/compose/docker-compose.milvus.yml \
  -f infrastructure/compose/docker-compose.minio.yml \
  up -d
```

Then bring up service composes on top (e.g. `docker-compose.prebuilt.yml`
for the full app fleet).
