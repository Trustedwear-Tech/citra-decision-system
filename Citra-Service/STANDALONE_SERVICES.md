<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Standalone Services Guide

This document describes the standalone services that run separately from the main FastAPI application.

## Services Overview

### 1. Credit Flush Service
**Location**: `credit-flush-service/`  
**Purpose**: Continuously monitors and flushes credit buffers from Redis to MongoDB  
**Schedule**: Every 30 seconds  
**Priority**: High (billing critical)

**Start Command**:
```bash
cd credit-flush-service
python main.py
```

---

### 2. Metadata Sync Service
**Location**: `metadata-sync-service/`  
**Purpose**: Daily sync of supplementary data source metadata (SQL schemas, SaaS app data)  
**Schedule**: Daily at 2:00 AM (configurable)  
**Priority**: Low (can skip days)

**Start Command**:
```bash
cd metadata-sync-service
python main.py
```

**Configuration** (via environment variables):
```bash
METADATA_SYNC_HOUR=2          # Hour to run (0-23)
METADATA_SYNC_MINUTE=0         # Minute to run (0-59)
METADATA_SYNC_USER_DELAY=5     # Delay between users (seconds)
METADATA_SYNC_MAX_USERS=0      # Max users per run (0=unlimited)
```

---

### 3. citra-workflow Service (Phase J split)

**Location**: `../citra-workflow/` (sibling repo dir; built as its own Docker image)
**Purpose**: Workflow CRUD + lifecycle endpoints + dept-data-flow templates. Used to be mounted inside Citra-Service via `app.include_router(workflow_router)`; extracted so workflow CPU never blocks Citra-Service's chat / files event loop.
**Port**: `9200`
**Priority**: High (everything SmartApp does talks to it)

**What it serves**:
```
/api/workflows/*          Workflow CRUD + the 7 lifecycle endpoints
                          (share, transfer, claim-for-dept,
                           escalate-to-org, archive, restore,
                           inheritance-policy)
/api/workflows/{id}/execute    Enqueues to Citra-Worker; returns 202.
                               Does NOT execute in-process.
/api/admin/workflows/*    Admin reassign / inheritance overrides
/api/dept-sources/*       Dept-data-flow registry (moved from
                          Citra-Service/dept_sources/)
/health                   Liveness — always 200 unless hung
/health/live              Explicit liveness
/health/ready             Readiness — pings Mongo + Redis;
                          ECS withdraws traffic on 503
```

**Does NOT serve**:
- Workflow execution itself — that runs in Citra-Worker, triggered by Redis queue
- Cron / scheduler — also in Citra-Worker (single replica with leader election)

**Start Command**:
```bash
cd ../citra-workflow
pip install -r requirements.txt
uvicorn citra_workflow.main:app --host 0.0.0.0 --port 9200 --reload
```

**Production (Docker)**:
```bash
# From monorepo root so editable -e ../citra-common/citra-queue etc. resolves
docker build -f citra-workflow/Dockerfile -t citra/workflow:latest .
docker run -p 9200:9200 --env-file .env citra/workflow:latest
```

**Configuration**:
```bash
JWT_SECRET=<shared with Citra-Service>
MONGODB_CONN_STRING=<same Mongo as Citra-Service>
REDIS_HOST, REDIS_PORT, REDIS_PASSWORD  # same Redis as Citra-Worker
CORS_ALLOWED_ORIGINS=<comma-separated origins>
APP_VERSION=1.1.0                       # surfaced in /health
```

**Replaces**: the previously-mounted `from citra_workflow.router import router` inside Citra-Service/main.py (now removed).

---

### 4. Citra-Worker (Phase A–H — already standalone)

**Location**: `../Citra-Worker/`
**Purpose**: Async job consumer. Hosts `workflow.run`, `workflow.resume`, `user.deactivated`, `user.delete_applied` handlers (and the workflow scheduler with Redis leader election).
**Port**: (none — Redis-driven consumer)
**Priority**: High

**Start Command**:
```bash
cd ../Citra-Worker
python -m worker
```

**Configuration**:
```bash
CITRA_WORKER_CONCURRENCY=4              # parallel async tasks per process
CITRA_WORKER_QUEUES=default,high,low   # priority lanes
CITRA_WORKER_SHUTDOWN_GRACE=30          # seconds to drain on SIGTERM
JWT_SECRET, MONGODB_CONN_STRING, REDIS_*  # same as other services
CITRA_SERVICE_URL=http://citra-service:7001  # for /admin/user-content-apply callbacks
```

---

## Quick Start (Local Development)

### Setup Virtual Environment
```bash
cd C:\Github\Citra-AI\Citra-Service
python -m venv myenv
myenv\Scripts\activate
pip install -r requirements.txt
```

### Run Main API Service
```bash
myenv\Scripts\activate
uvicorn main:app --reload --host 0.0.0.0 --port 8085
```

### Run Credit Flush Service (Terminal 2)
```bash
cd credit-flush-service
..\myenv\Scripts\activate
python main.py
```

### Run Metadata Sync Service (Terminal 3)
```bash
cd metadata-sync-service
..\myenv\Scripts\activate
python main.py
```

### Run citra-workflow (Terminal 4) — Phase J
```bash
cd ..\citra-workflow
..\Citra-Service\myenv\Scripts\activate
pip install -e ..\citra-queue -e ..\citra-auth -e ..\citra-mongo
uvicorn citra_workflow.main:app --host 0.0.0.0 --port 9200 --reload
```

### Run Citra-Worker (Terminal 5)
```bash
cd ..\Citra-Worker
..\Citra-Service\myenv\Scripts\activate
python -m worker
```

---

## Service Dependencies

| Service | Requires Main API | Shared Resources |
|---------|------------------|------------------|
| **Main API** | N/A | MongoDB, Redis, Milvus, External APIs |
| **Credit Flush** | No | MongoDB, Redis |
| **Metadata Sync** | No | MongoDB, External APIs (SQL, SaaS) |

---

## Deployment Architecture

### Local Development
```
Terminal 1: uvicorn main:app (Port 8085)
Terminal 2: credit-flush-service/main.py
Terminal 3: metadata-sync-service/main.py (optional, only for testing)
```

### Production (Docker)
```yaml
services:
  main-api:
    build: .
    command: uvicorn main:app --host 0.0.0.0 --port 8085
    ports:
      - "8085:8085"
  
  credit-flush:
    build: 
      context: .
      dockerfile: credit-flush-service/Dockerfile
    deploy:
      replicas: 1  # Single instance with leader election
  
  metadata-sync:
    build:
      context: .
      dockerfile: metadata-sync-service/Dockerfile
    deploy:
      replicas: 1  # Single instance only
```

---

## Logs

### Main API Service
- Console output
- Location: stdout
- Format: Standard Python logging

### Credit Flush Service
- Console output
- Location: stdout
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

### Metadata Sync Service
- Console output + File
- File: `logs/metadata_sync.log` (rotated, 10MB max, 5 backups)
- Format: `YYYY-MM-DD HH:MM:SS | LEVEL | Message`

---

## Health Checks

### Credit Flush Service
Check Redis buffer counts:
```bash
redis-cli KEYS "credit_buffer:*"
```

Check flush logs:
```bash
docker logs credit-flush-service
```

### Metadata Sync Service
Check scheduler status via API:
```bash
curl http://localhost:8085/api/integrations/data/scheduler/status
```

Check sync logs:
```bash
tail -f logs/metadata_sync.log
```

Manually trigger sync:
```bash
curl -X POST http://localhost:8085/api/integrations/data/scheduler/trigger
```

---

## Troubleshooting

### Service Won't Start

**Missing Dependencies**:
```bash
myenv\Scripts\activate
pip install -r requirements.txt
```

**Import Errors**:
- Ensure virtual environment is activated
- Verify all modules are in Python path
- Check `vault_env_loader` loads environment correctly

### Credit Flush Service

**Buffers Not Flushing**:
1. Check Redis connection
2. Verify MongoDB connection
3. Check leader election logs
4. Ensure only 1-2 instances running

### Metadata Sync Service

**Scheduler Not Running**:
1. Verify `apscheduler` is installed
2. Check environment variables
3. Review logs for import errors

**No Users Processed**:
1. Check MongoDB collections: `nango_connections`, `live_data_connections`
2. Verify users have active connections
3. Check API credentials for external services

---

## Monitoring Checklist

- [ ] Main API: Responds on port 8085
- [ ] Credit Flush: Buffers being flushed regularly
- [ ] Metadata Sync: Daily sync completing successfully
- [ ] MongoDB: All connections healthy
- [ ] Redis: Cache and buffers operational
- [ ] Logs: No critical errors

---

## Best Practices

1. **Always use virtual environment**: Prevents dependency conflicts
2. **Monitor logs regularly**: Catch issues early
3. **Single instance for scheduled services**: Avoid conflicts
4. **Environment variables**: Use `.env` for local, secrets for production
5. **Graceful shutdown**: Use Ctrl+C, not kill -9

---

## Reference

- Main API: `main.py`
- Credit Flush: `credit-flush-service/README.md`
- Metadata Sync: `metadata-sync-service/README.md`
- Requirements: `requirements.txt`
