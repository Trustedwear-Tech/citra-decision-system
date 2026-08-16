<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra Application Services — Deployment Guide

The Citra Decision System runs 12 application services. Each service is an independent container that can run on the same machine or different machines.

## Service Overview

Ports below are the host ports published by `docker-compose.dev.yml`.

| Service | Port | Tech | Description |
|---------|------|------|-------------|
| **citra-service** | 8085 | Python / FastAPI | Core backend — MCP-backed chat, vault, documents, search |
| **citra-ui** | 8081 | React Native (Expo Web) | The operator shell — case working, dashboards, admin |
| **citra-user-service** | 7004 | Node.js / Express | Authentication, user profiles, team management |
| **smart-app-service** | 9100 | Python / FastAPI | Decision App builder and runtime — specs, agent loop, approvals |
| **citra-app-runtime** | 3100 | Node.js / Next.js | Renders a published Decision App inside the UI shell |
| **citra-mcp-service** | 9090 | Python / FastAPI | Platform MCP tools — discovery, embed, rerank, SQL, media |
| **discovery-service** | 9010 | Python / FastAPI | Registry of live source MCPs; answers `/tools/available` |
| **data-discovery-service** | 8095 | Python / FastAPI | Crawls registered MCPs into the `data_catalogue` |
| **action-sandbox-host** | 7090 | Python / FastAPI | Runs generated code in an isolated sandbox |
| **duckdb-query-service** | 7301 | Python / FastAPI | Analytical SQL engine for data queries |
| **reranker-service** | 7302 | Python / FastAPI | ML re-ranking for search results |
| **playwright-render-service** | 3001 | Node.js / Playwright | Headless page rendering for content extraction |

A department's own source MCP is **not** in this list. It is built from
`source-mcp-template` and deployed per department with its own `sources.json`;
see [../write-actions.md](../write-actions.md) and
[../change-the-demo.md](../change-the-demo.md).

## Dependencies

```
citra-service          ──→ MongoDB, Redis, Milvus, MinIO, LLM provider
                           citra-user-service, duckdb-query-service,
                           reranker-service, playwright-render-service
citra-user-service     ──→ MongoDB, Redis
smart-app-service      ──→ MongoDB, discovery-service, data-discovery-service,
                           action-sandbox-host, the department MCPs, LLM provider
citra-app-runtime      ──→ smart-app-service
citra-mcp-service      ──→ Milvus, MinIO, reranker-service
discovery-service      ──→ MongoDB
data-discovery-service ──→ MongoDB, Milvus, discovery-service, the department MCPs
action-sandbox-host    ──→ (isolated — no inbound network from the sandbox)
duckdb-query-service   ──→ MongoDB, Redis, LLM provider
reranker-service       ──→ (standalone — downloads model on first start)
Playwright    ──→ (standalone — Chromium bundled in Docker image)
```

Set up databases first (see individual component guides), then deploy services.

## Option A: Docker Compose (Single or Multi-Machine)

### All services on one machine

Services run from the compose files at the repo root -- `docker-compose.dev.yml` to build from source, or `docker-compose.release.yml` for the pre-built images below.

### Pre-built images (no local build)

```bash
cd infrastructure/compose
docker compose -f docker-compose.base.yml -f docker-compose.prebuilt.yml \
  --env-file ../../.env up -d
```

Or pull individual images:
```bash
docker pull ghcr.io/trustedwear-tech/citra-service:latest
docker pull ghcr.io/trustedwear-tech/user-service:latest
docker pull ghcr.io/trustedwear-tech/duckdb-query-service:latest
docker pull ghcr.io/trustedwear-tech/reranker-service:latest
docker pull ghcr.io/trustedwear-tech/playwright-render-service:latest
```

### Individual service on a separate machine

Run a single service with Docker:
```bash
docker run -d --name citra-service \
  -p 7001:7001 \
  --env-file .env \
  ghcr.io/trustedwear-tech/citra-service:latest
```

Or build from source on that machine:
```bash
git clone https://github.com/Trustedwear-Tech/citra-decision-system.git
cd Citra-AI/Citra-Service
docker build -t citra-service .
docker run -d --name citra-service -p 7001:7001 --env-file .env citra-service
```

## Option B: Build from Source (No Docker)

### Citra-Service (Python)

```bash
cd Citra-Service
python -m venv myenv
source myenv/bin/activate  # Linux/macOS
# myenv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 7001
```

### User-Service (Node.js)

```bash
cd Citra-User-Service
npm install
node index.js
# Listens on port 7004
```

### DuckDB Query (Python)

```bash
cd duckdb-query-service
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
# Listens on port 7301
```

### Reranker (Python)

```bash
cd reranker-service
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
# Listens on port 7302 — downloads model on first start
```

### Playwright Render (Python)

```bash
cd playwright-render-service
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py
# Listens on port 3001
```

## Environment Variables (Key)

All services read from `.env`. When services are on different machines, each machine needs a `.env` with the relevant connection strings pointing to the correct hosts.

### Citra-Service

| Variable | Required | Description |
|----------|----------|-------------|
| `MONGODB_CONN_STRING` | Yes | MongoDB connection string |
| `MILVUS_URI` | Yes | Milvus endpoint |
| `REDIS_HOST` | Yes | Redis host |
| `BUCKET_ENDPOINT_URL` | For MinIO | Object storage endpoint (leave empty for AWS S3) |
| `LLM_BASE_URL` | Yes | OpenAI-compatible LLM endpoint |
| `LLM_API_KEY` | Yes | LLM API key |
| `LLM_MODEL` | Yes | LLM model name |
| `JWT_SECRET` | Yes | Shared JWT secret (must match User-Service) |
| `RERANKER_URL` | No | Reranker endpoint (default: `http://localhost:7302`) |
| `DUCKDB_SERVICE_URL` | No | DuckDB endpoint (default: `http://localhost:7301`) |
| `PLAYWRIGHT_RENDER_URL` | No | Playwright endpoint (default: `http://localhost:3001`) |

### User-Service

| Variable | Required | Description |
|----------|----------|-------------|
| `MONGODB_CONN_STRING` | Yes | Same MongoDB as Citra-Service |
| `JWT_SECRET` | Yes | Must match Citra-Service |
| `REDIS_HOST` | Yes | Redis host |

### Cross-Machine Configuration

When services run on different machines, update the service URLs in the `.env` on each machine:

```env
# Machine A: databases (MongoDB on 10.0.1.10, Redis on 10.0.1.11)
MONGODB_CONN_STRING=mongodb://root:password@10.0.1.10:27017/?authSource=admin&replicaSet=rs0
REDIS_HOST=10.0.1.11

# Machine B: Citra-Service + User-Service
# (point to databases on Machine A, other services on Machine C)
MONGODB_CONN_STRING=mongodb://root:password@10.0.1.10:27017/?authSource=admin&replicaSet=rs0
REDIS_HOST=10.0.1.11
RERANKER_URL=http://10.0.1.30:7302
DUCKDB_SERVICE_URL=http://10.0.1.30:7301
PLAYWRIGHT_RENDER_URL=http://10.0.1.30:3001

# Machine C: supporting services
# (just need their own config, no cross-references needed)
```

## Health Checks

All services expose a `GET /health` endpoint:

```bash
curl http://localhost:7001/health   # Citra-Service
curl http://localhost:7004/health   # User-Service
curl http://localhost:7301/health   # DuckDB Query
curl http://localhost:7302/health   # Reranker
curl http://localhost:3001/health   # Playwright
```

## Vault Integration

Citra-Service and User-Service support HashiCorp Vault for secrets management. Delete `.env` and set `VAULT_ADDR` + authentication to load secrets from Vault at startup. See the [deployment guide](../deployment-guide.md#configuration--secrets-management) for details.

| Service | Vault Support |
|---------|:---:|
| Citra-Service | Yes (`vault_env_loader.py`) |
| User-Service | Yes (`vault_env_loader.js`) |
| DuckDB Query | Yes (`vault_env_loader.py`) |
| Reranker | No (`.env` only) |
| Playwright | No (`.env` only) |
