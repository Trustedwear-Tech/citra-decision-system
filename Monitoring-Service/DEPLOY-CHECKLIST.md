<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Monitoring-Service — Final Deploy Checklist

> Generated after pre-deploy hardening session.  
> All items below were **verified** with commands. Do not mark complete without re-running.

---

## Pre-Deploy Checks

### 1. Environment Variables
| Variable | Required | Status |
|----------|----------|--------|
| `AWS_ACCESS_KEY_ID` | Yes (SES transport) | ✅ Set in `.env` |
| `AWS_SECRET_ACCESS_KEY` | Yes (SES transport) | ✅ Set in `.env` |
| `LOG_DIRECTORIES` | Yes | ✅ Set in `.env` |
| `MONITOR_LOG_DIR` | Yes | ✅ Set in `.env` |
| `ALERT_TRANSPORT` | Defaults to `log` | Dockerfile default = `log`; set to `ses` for production email |
| `ALERT_FROM_EMAIL` | Yes if transport=ses | Dockerfile default = `info@trustedweartech.com` |
| `ALERT_TO_EMAILS` | Yes if transport=ses | Dockerfile default = `deeepakumar@gmail.com` |
| `AWS_REGION` | Yes if transport=ses | Dockerfile default = `ap-south-1` |
| `SENTRY_DSN` | Optional | Leave unset to disable Sentry |

**Action for production**: Change `ALERT_TRANSPORT=ses` in `.env` and verify SES is enabled in your AWS account for both sender and recipient addresses.

---

### 2. Log Directories (on host)
All directories must exist before starting the container.
```bash
mkdir -p ~/citra-ai/logs/Citra-Service
mkdir -p ~/citra-ai/logs/Citra-User-Service
mkdir -p ~/citra-ai/logs/playwright-render-service
mkdir -p ~/citra-ai/logs/Monitoring-Service
```
**Status**: ✅ Verified writable from inside container.

---

### 3. Docker Socket
- `/var/run/docker.sock` must be accessible on the host.
- `entrypoint.sh` dynamically matches the socket GID at runtime.
- If socket is unavailable, docker monitoring degrades gracefully — **service continues running**.

**Status**: ✅ Graceful degradation verified (Exit=0 with docker daemon disconnected).

---

### 4. Docker Network
The `citra-network` external bridge network must exist before running `docker compose up`.
```bash
docker network inspect citra-network
# If missing: docker network create citra-network
```
**Status**: ✅ `citra-network: bridge` confirmed present.

---

### 5. Image Build
Build and tag the hardened image before deploying:
```bash
cd Monitoring-Service
docker build -t monitoring-service:hardened .
docker tag monitoring-service:hardened monitoring-service:latest
```
**Status**: ✅ `monitoring-service:hardened` built and tested.

---

### 6. `.env` Not in Git
`.gitignore` must exclude `.env`.

**Status**: ✅ `.env` and `.env.*` are in `.gitignore` (`.env.example` is allowed).

---

## Deploy Steps

```bash
cd Monitoring-Service

# 1. Build the hardened image
docker build -t monitoring-service:latest .

# 2. Stop the current container (if running)
docker compose down

# 3. Start with the new image
docker compose up -d

# 4. Verify startup
docker logs monitoring-service --tail 30
```

---

## Post-Deploy Verification

### Health Check
```bash
curl http://localhost:8400/health
# Expected: {"status": "ok", "transport": "ses"}  (or "log" if transport=log)
```
**Status**: ✅ Returns `{"status":"ok","transport":"log"}` at current config.

### Resource Usage
```bash
docker stats monitoring-service --no-stream
# Expected: CPU < 1%, MEM < 100 MiB
```
**Status**: ✅ CPU=0.12–0.16%, MEM=45.8 MiB (0.39% of 11.6 GiB).

### Container State
```bash
docker inspect monitoring-service --format "Status={{.State.Status}} OOMKilled={{.State.OOMKilled}}"
# Expected: Status=running OOMKilled=false
```
**Status**: ✅ `Status=running OOMKilled=false Dead=false`.

### Log Output
```bash
docker logs monitoring-service --tail 50
# Expected: no ERROR or CRITICAL lines on startup
# Acceptable: "DockerMonitor: cannot connect to Docker daemon" if docker.sock not mounted
```

### Webhook Test
```bash
curl -s -X POST http://localhost:8400/webhook/alert \
  -H "Content-Type: application/json" \
  -d '{"source":"deploy-test","alert_type":"info","subject":"Deploy test","body":"ok"}'
# Expected: {"status": "received"}
```

---

## What Was Hardened (Session Summary)

| Check | Result |
|-------|--------|
| Git diff — 11 files changed | ✅ |
| flake8 lint — 0 issues | ✅ |
| bandit security — 0 issues (1 suppressed `nosec B104`) | ✅ |
| Linux/Debian 13 Python 3.11 import validation | ✅ |
| Docker daemon unavailable — graceful degradation | ✅ CRITICAL FIX |
| SES failure (bad creds / empty creds / empty from_email) | ✅ All Exit=0 |
| Fingerprint deduplication — same error suppressed | ✅ |
| AlertManager cooldown — same source+type suppressed | ✅ |
| CPU: 0.12–0.16% idle, RAM: 45.8 MiB | ✅ |
| Rollback procedure documented | ✅ |

---

## Remaining Action Before Production

- [ ] Set `ALERT_TRANSPORT=ses` in `.env` on the production host
- [ ] Verify SES sender `info@trustedweartech.com` is verified in AWS SES console
- [ ] Verify SES recipient `deeepakumar@gmail.com`, `contact@trustedweartech.com` are verified (if in sandbox mode)
- [ ] Run `docker compose up -d` with hardened image on production host
- [ ] Run the webhook test curl above and confirm receipt
- [ ] Watch `docker logs monitoring-service --tail 100 -f` for 2–3 minutes after deploy
