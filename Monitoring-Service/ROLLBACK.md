<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Monitoring-Service — Rollback Procedure

## Context

All hardening changes in this session are **uncommitted** working-tree modifications
relative to commit `5a2fdf7` (branch `Citra-Ai-Enterprise`).  
The previous Docker image tag is `monitoring-service:latest` (pre-hardening).  
The hardened image tag is `monitoring-service:hardened`.

---

## Quick Rollback (before committing)

If you have NOT yet committed the hardening changes, revert all Monitoring-Service
source files back to the last commit in one command:

```bash
git checkout HEAD -- Monitoring-Service/app/alert_manager.py \
                     Monitoring-Service/app/config.py \
                     Monitoring-Service/app/daily_report.py \
                     Monitoring-Service/app/docker_monitor.py \
                     Monitoring-Service/app/log_monitor.py \
                     Monitoring-Service/app/logging_setup.py \
                     Monitoring-Service/app/webhook.py \
                     Monitoring-Service/docker-compose.yml \
                     Monitoring-Service/entrypoint.sh \
                     Monitoring-Service/Dockerfile \
                     Monitoring-Service/README.md
```

Then rebuild the image and restart the container:

```bash
cd Monitoring-Service
docker build -t monitoring-service:latest .
docker compose up -d --force-recreate
```

---

## Rollback After Committing

### Step 1 — Identify the rollback commit

```bash
git log --oneline Monitoring-Service/app/docker_monitor.py
# Copy the commit SHA immediately BEFORE the hardening commit
```

### Step 2 — Revert the files

```bash
git checkout <prev-sha> -- Monitoring-Service/app/alert_manager.py \
                           Monitoring-Service/app/config.py \
                           Monitoring-Service/app/daily_report.py \
                           Monitoring-Service/app/docker_monitor.py \
                           Monitoring-Service/app/log_monitor.py \
                           Monitoring-Service/app/logging_setup.py \
                           Monitoring-Service/app/webhook.py \
                           Monitoring-Service/docker-compose.yml \
                           Monitoring-Service/entrypoint.sh \
                           Monitoring-Service/Dockerfile \
                           Monitoring-Service/README.md
git commit -m "revert: roll back Monitoring-Service hardening"
```

### Step 3 — Rebuild and restart

```bash
cd Monitoring-Service
docker build -t monitoring-service:latest .
docker compose down
docker compose up -d
```

---

## Container-Only Rollback (no code change, image only)

If the hardened image causes a runtime problem but the old image is still present:

```bash
# Stop and remove the running container
docker rm -f monitoring-service

# Re-tag the pre-hardening image (if you had saved it)
docker tag monitoring-service:pre-hardening monitoring-service:latest

# Or pull the old image from your registry
docker pull <registry>/monitoring-service:<old-tag>
docker tag  <registry>/monitoring-service:<old-tag> monitoring-service:latest

# Restart with docker compose
cd Monitoring-Service
docker compose up -d
```

---

## What Changes Were Made (files modified in this hardening session)

| File | Change |
|------|--------|
| `app/docker_monitor.py` | **CRITICAL**: wrapped `docker.from_env()` in try/except; graceful degradation when docker socket unavailable |
| `app/log_monitor.py` | Lint: E741 ambiguous var `l`→`ln`; B324 SHA1 `usedforsecurity=False`; B110 bare except→log.debug |
| `app/config.py` | Lint: E302/E303 blank line fixes |
| `app/daily_report.py` | Lint: W293 trailing whitespace |
| `app/logging_setup.py` | Lint: E302 blank line before class/function |
| `app/webhook.py` | Security: `# nosec B104` for intentional 0.0.0.0 binding |
| `docker-compose.yml` | Volume mount paths corrected (`~/citra-ai/logs/...`) |
| `entrypoint.sh` | Dynamic GID fix for docker.sock + gosu privilege drop |
| `Dockerfile` | gosu install, appuser creation, entrypoint.sh |
| `README.md` | Updated documentation |

---

## State Preserved Through Rollback

- **Alert cooldown state**: In-memory only, lost on any container restart (expected).
- **Log fingerprint state**: In-memory only, lost on container restart (expected).
- **Log files**: Mounted from host volumes (`~/citra-ai/logs/...`). Not affected by rollback.
- **`.env` file**: Not reverted by git checkout of app files. Back up manually if changed.

---

## Emergency Stop

```bash
cd Monitoring-Service
docker compose down
# Service stops; logs preserved on host volumes
```
