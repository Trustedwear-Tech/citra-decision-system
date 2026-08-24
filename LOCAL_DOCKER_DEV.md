<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Local Docker Development Environment — Citra AI

This setup provides a high-performance local Docker-based development environment equivalent to the VS Code task "Start All Citra Services". It is optimized for fast startup, hot-reloading (via code bind mounts), and minimal rebuild times.

---

## Architecture Overview

The setup is split into two layers:
1. **Infrastructure Layer** (`docker-compose.infra.yml`): Runs the supporting infrastructure databases (MongoDB, Redis, Milvus with its etcd/MinIO, and MinIO).
2. **Application Layer** (`docker-compose.dev.yml`): Runs the 12 Citra services.

All services are connected via a shared bridge network named `citra-network`. Source code directories are bind-mounted directly into the containers so local edits trigger immediate hot-reload.

---

## Service-to-Port Map

The following ports are exposed on `localhost`:

| Service | Port | Internal Technology | Reload Mode |
| :--- | :---: | :---: | :--- |
| **Citra-Service** | `8085` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **Citra-UI** | `8081` | Node React Native Expo | Metro Live Reload |
| **Citra-User-Service** | `7004` | Node Express (Nodemon) | Express Live Reload |
| **duckdb-query-service**| `7301` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **reranker-service** | `7302` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **discovery-service** | `9010` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **action-sandbox-host** | `7090` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **smart-app-service** | `9100` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **citra-app-runtime** | `3100` | Node Next.js (Dev server) | Next.js Fast Refresh |
| **data-discovery-service**| `8095` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **citra-mcp-service** | `9090` | Python FastAPI (Uvicorn) | ASGI Live Reload |
| **playwright-render-service** | `3001` | Node + Playwright | Container Restart Required |

---

## Commands and Script Usage

The `Makefile` wraps the compose lifecycle -- `make help` lists every target.

### 1. Start All Services
To launch the databases, wait for MongoDB to become healthy, and then start the application services:
```bash
make install        # first run: generates .env, brings up the data stores
make start          # build and start the application layer
```
*(Or directly: `docker compose -f docker-compose.infra.yml up -d` followed by `docker compose -f docker-compose.dev.yml up`)*

### 2. Stop All Services
To cleanly stop both layers and tear down container instances:
```bash
make down
```
*(Or directly: `docker compose -f docker-compose.dev.yml down` followed by `docker compose -f docker-compose.infra.yml down`)*

### 3. Rebuild One or All Services
```bash
docker compose -f docker-compose.dev.yml build                  # everything
docker compose -f docker-compose.dev.yml build citra-service    # one service
```

### 4. Checking on the stack
```bash
make ps             # what is running
make logs           # follow logs
```

---

## Hot-Reload Behavior

* **Python ASGI Services**: Uvicorn is executed with `--reload`. Since the service's source directory is bind-mounted, saving a `.py` file on your host machine will immediately trigger an in-container restart of the Uvicorn worker.
* **Node Express Services**: Nodemon watches for file changes on the bind mount and restarts the express listener.
* **Vite/Next.js/React Services**: Next.js and Expo dev packagers watch files and use hot refresh to update the UI on the fly.
* **playwright-render-service**: the headless renderer does not watch files. After changing it, restart the container:
  ```bash
  docker compose -f docker-compose.dev.yml restart playwright-render-service
  ```

---

## Troubleshooting Docker Desktop on Windows

### 1. File Watching and Windows Bind Mounts (WSL 2)
If hot-reload does not trigger when you save files:
* Ensure Docker Desktop is configured to use the **WSL 2 backend** (instead of Hyper-V).
* If changes are still not picked up, check the polling overrides. Under the hood, we pass `CHOKIDAR_USEPOLLING=true` and `WATCHPACK_POLLING=true` to Node/Next.js to ensure file watch events are correctly generated across WSL filesystem boundaries.
* For Uvicorn, if reload is missed, you can set `--reload-delay 1` or restart the specific container using `docker compose -f docker-compose.dev.yml restart <service>`.

### 2. Docker socket permission on Windows (sandbox-host)
The `action-sandbox-host` container manages sandboxes by communicating with the Docker daemon via `/var/run/docker.sock`. If you see permission errors:
* Ensure that "Expose daemon on tcp://localhost:2375 without TLS" is disabled in Docker Desktop general settings.
* Ensure you are running Docker Desktop with your current active Windows user account.
