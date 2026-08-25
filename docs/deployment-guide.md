<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Deployment Guide

## Deployment Options

| Approach | Best For | Guide |
|----------|----------|-------|
| **Docker Compose** (modular) | Single machine, multi-machine, or hybrid | This page |
| **Build from source** | The supported path. `make wizard` builds every image locally | [README quickstart](../README.md#quickstart) |
| **Build from source** (no Docker) | Development, custom modifications | [Component guides](components/citra-services.md#option-b-build-from-source-no-docker) |

> **Kubernetes retired (2026-06-11):** the K8s manifests and Helm charts were removed from this repository (recoverable from the `archive/pre-infra-cleanup-2026-06-11` branch). The supported deployment path is Docker Compose via the `Makefile` targets, building from source.

---

## Docker Compose

### Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 16 GB | 32 GB |
| Disk | 50 GB | 200 GB (data) |
| OS | Linux (Docker 24+) | Ubuntu 22.04+ |

### Step 1: Configure

```bash
git clone https://github.com/Trustedwear-Tech/citra-decision-system.git
cd citra-decision-system

# Generate .env with random secrets:
./scripts/setup.sh --generate-env

# Or start from the reference template:
cp .env.example .env
```

Edit `.env` with your database URLs, AI provider keys, and domain settings.

### Step 2: Start Services

Each component has its own compose file in `infrastructure/compose/`. Deploy them independently — they can run on the same machine or across separate servers:

```bash
cd infrastructure/compose

# Create shared network (once)
docker network create citra-network

# Start databases
docker compose -f docker-compose.base.yml -f docker-compose.mongodb.yml --env-file ../../.env up -d
docker compose -f docker-compose.base.yml -f docker-compose.redis.yml --env-file ../../.env up -d
docker compose -f docker-compose.base.yml -f docker-compose.milvus.yml --env-file ../../.env up -d
docker compose -f docker-compose.base.yml -f docker-compose.minio.yml --env-file ../../.env up -d

# Start application services (pre-built images — see below)
docker compose -f docker-compose.base.yml -f docker-compose.prebuilt.yml --env-file ../../.env up -d
```

To run an application service from source instead, use the dev compose file at the repo root: `docker compose -f docker-compose.dev.yml up <service>`.

#### Pre-built images (no build step)

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.prebuilt.yml \
  --env-file ../../.env up -d
```

Override the image registry or tag:
```bash
CITRA_IMAGE_PREFIX=your-registry.com/citra-ai \
CITRA_IMAGE_TAG=v1.2.0 \
docker compose -f docker-compose.base.yml -f docker-compose.prebuilt.yml \
  --env-file ../../.env up -d
```

### Step 3: Initialize (First Time)

```bash
# Initialize MongoDB replica set
docker exec citra-mongodb mongosh --eval \
  "rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27017'}]})"

# Create MinIO bucket
docker run --rm --network host --entrypoint sh minio/mc -c \
  "mc alias set citra http://localhost:9000 minioadmin minioadmin; \
   mc mb --ignore-existing citra/citra-documents"
```

### Step 4: Verify

```bash
# Test APIs
curl http://localhost:7001/health    # Citra-Service
curl http://localhost:7004/health    # User-Service

# Validate .env connectivity
./scripts/setup.sh --check
```

### Service Ports

| Service | Port | Purpose |
|---------|------|---------|
| Citra-Service | 7001 | Core AI backend |
| User-Service | 7004 | Auth & users |
| Collaboration | 1234 | Real-time editing (WebSocket) |
| DuckDB Query | 7301 | Analytical SQL engine |
| Reranker | 7302 | ML re-ranking |
| Playwright | 3001 | Web rendering |
| MongoDB | 27017 | Document store |
| Redis | 6379 | Cache |
| Milvus | 19530 | Vector database |
| MinIO | 9000 / 9001 | Object storage / console |

---

## Kubernetes

The Kubernetes manifests were retired on 2026-06-11 (recoverable from the `archive/pre-infra-cleanup-2026-06-11` branch). The supported deployment path is Docker Compose via the `Makefile` targets, building from source.

---

## Configuration & Secrets Management

### Option A: .env File (Simple)

```bash
cp .env.example .env
# Or use a scenario template:
```

Edit `.env` with your values. Services read from it automatically.

### Option B: HashiCorp Vault (Production)

Store secrets in Vault instead of flat files:

1. **Don't create** a `.env` file (or delete it)
2. Set Vault connection variables:
   ```env
   VAULT_ADDR=http://vault:8200
   VAULT_SECRET_PATH=prod/citra-ai

   # Auth — choose ONE:
   VAULT_TOKEN=hvs.your-root-token
   # OR (recommended):
   VAULT_ROLE_ID=your-role-id
   VAULT_SECRET_ID=your-secret-id
   ```

3. Store all secrets in Vault:
   ```bash
   vault kv put prod/citra-ai \
     MONGODB_CONN_STRING="mongodb://..." \
     JWT_SECRET="$(openssl rand -hex 32)" \
     LLM_API_KEY="your-api-key" \
     BUCKET_ACCESS_KEY="..." \
     BUCKET_SECRET_KEY="..."
   ```

4. Services detect `VAULT_ADDR`, authenticate, and load secrets at startup.

**Priority chain**: `.env` loads first → Vault overrides `.env` values.

**Production fail-fast**: When Vault path starts with `prod`, services refuse to start if Vault is unreachable. Development paths (`dev`, `test`) fall back to `.env` gracefully.

### Vault Support by Service

| Service | Vault | Loader |
|---------|:-----:|--------|
| Citra-Service | Yes | `vault_env_loader.py` |
| User-Service | Yes | `vault_env_loader.js` |
| Collaboration | Yes | `vault_env_loader.js` |
| DuckDB Query | Yes | `vault_env_loader.py` |
| Reranker | No | `.env` only |
| Playwright | No | `.env` only |

### Deploy Vault (Docker)

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.vault.yml --env-file ../../.env up -d

# Initialize (first time — save the unseal keys!)
docker exec citra-vault vault operator init -key-shares=1 -key-threshold=1
docker exec citra-vault vault operator unseal <unseal-key>
docker exec citra-vault vault secrets enable -path=prod kv-v2
```

---

## Internet Search

Citra has two independent internet search systems (both optional):

### 1. LLM-Grounded Search (Chat)

The LLM performs live web searches when asked about current events.

Set the search endpoint in `.env`:
```env
SEARCH_BASE_URL=https://api.x.ai/v1     # any OpenAI-compatible endpoint
SEARCH_API_KEY=xai-your-key-here
SEARCH_MODEL=grok-4-1-fast-non-reasoning
```

### 2. Web Search API (Reader & RAG)

Traditional web search for document research and internet context.

| Provider | Env Var | Free Tier |
|----------|---------|-----------|
| Serper.dev | `SERPER_API_KEY` | 2,500 queries |
| DuckDuckGo | None | Unlimited (limited) |

---

## Inference Engine

Citra consumes LLMs through third-party OpenAI-compatible APIs. Set `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`. Any OpenAI-compatible endpoint works (OpenRouter, OpenAI, DeepSeek, Together, Groq, or a vLLM server you operate yourself).

> The bundled on-prem GPU inference service was retired on 2026-06-11 (recoverable from the `archive/pre-infra-cleanup-2026-06-11` branch).

---

## Updating

### Docker Compose

```bash
git pull
# Rebuild and restart a service from its own project folder, e.g.:
cd Citra-Service
docker compose up -d --build
```

### Pre-built Images

```bash
CITRA_IMAGE_TAG=v1.2.0 docker compose -f infrastructure/compose/docker-compose.prebuilt.yml up -d
```

---

## Backup & Restore

### MongoDB

```bash
# Docker
docker exec citra-mongodb mongodump --out /data/backup --gzip
docker cp citra-mongodb:/data/backup ./mongodb-backup

# Restore
docker cp ./mongodb-backup citra-mongodb:/data/backup
docker exec citra-mongodb mongorestore /data/backup --gzip
```

### MinIO

```bash
mc mirror citra/citra-documents ./minio-backup
mc mirror ./minio-backup citra/citra-documents  # Restore
```

### Milvus

Use the [Milvus Backup Tool](https://github.com/zilliztech/milvus-backup).

---

## Self-Hosted Mode

Every cloud service has a self-hosted alternative:

| Cloud Service | Self-Hosted Alternative |
|---------------|------------------------|
| MongoDB Atlas | Local MongoDB 7.0 (replica set) |
| Zilliz Cloud | Local Milvus 2.4 |
| AWS S3 / Azure Blob | MinIO |
| Redis Cloud | Local Redis 7 |
| AWS SES | SMTP or log transport |
| Cloud Vault | Local HashiCorp Vault |

AI models are the exception: LLMs are consumed via third-party OpenAI-compatible APIs (or a vLLM server you operate outside this repository).

---

## Component Guides

Detailed per-component deployment instructions:

| Component | Guide |
|-----------|-------|
| MongoDB | [components/mongodb.md](components/mongodb.md) |
| Redis | [components/redis.md](components/redis.md) |
| Milvus | [components/milvus.md](components/milvus.md) |
| Object Storage | [components/object-storage.md](components/object-storage.md) |
| Application Services | [components/citra-services.md](components/citra-services.md) |
| Reverse Proxy | [components/reverse-proxy.md](components/reverse-proxy.md) |
| Monitoring | [components/monitoring.md](components/monitoring.md) |
| Web Frontend | [components/web-frontend.md](components/web-frontend.md) |