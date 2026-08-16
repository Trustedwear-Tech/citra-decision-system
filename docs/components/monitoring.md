<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Monitoring & Alerts — Deployment Guide

The Monitoring Service performs health checks on all Citra services and sends alerts when services go down.

## Health Check Targets

The monitoring service checks these endpoints:

| Service | Endpoint | Default URL |
|---------|----------|-------------|
| Citra-Service | `GET /health` | `http://localhost:7001/health` |
| User-Service | `GET /health` | `http://localhost:7004/health` |
| DuckDB Query | `GET /health` | `http://localhost:7301/health` |
| Reranker | `GET /health` | `http://localhost:7302/health` |
| Playwright | `GET /health` | `http://localhost:3001/health` |

## Alert Transports

| Transport | `ALERT_TRANSPORT` | Use Case |
|-----------|-------------------|----------|
| **log** | `log` | Development — writes to stdout/file |
| **SMTP** | `smtp` | Email alerts via any SMTP server |
| **AWS SES** | `ses` | Email alerts via Amazon SES |
| **Webhook** | `webhook` | Slack, Discord, PagerDuty, Teams, custom HTTP |

## Option A: Docker Compose

The monitoring compose file lives in the Monitoring-Service project folder:

```bash
docker compose -f Monitoring-Service/docker-compose.monitoring.yml \
  --env-file .env up -d
```

## Option B: Run Directly

```bash
cd Monitoring-Service
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Configuration

### Basic (log transport)

```env
ALERT_TRANSPORT=log
```

### SMTP Email

```env
ALERT_TRANSPORT=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=alerts@yourdomain.com
SMTP_PASSWORD=your-app-password
MONITOR_EMAIL_FROM=alerts@yourdomain.com
MONITOR_EMAIL_TO=admin@yourdomain.com
```

### AWS SES

```env
ALERT_TRANSPORT=ses
AWS_SES_REGION=us-east-1
AWS_SES_ACCESS_KEY=AKIAxxxxxxxxxx
AWS_SES_SECRET_KEY=your-secret-key
MONITOR_EMAIL_FROM=alerts@yourdomain.com
MONITOR_EMAIL_TO=admin@yourdomain.com
```

### Webhook (Slack, Discord, etc.)

```env
ALERT_TRANSPORT=webhook
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXX
```

For Discord:
```env
ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
```

### Remote Service Monitoring

When services are on different machines, configure the monitoring service to reach them by IP/hostname:

```env
# Override default localhost URLs for remote monitoring
CITRA_SERVICE_URL=http://10.0.1.20:7001
USER_SERVICE_URL=http://10.0.1.20:7004
DUCKDB_SERVICE_URL=http://10.0.1.22:7301
RERANKER_URL=http://10.0.1.22:7302
PLAYWRIGHT_RENDER_URL=http://10.0.1.22:3001
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALERT_TRANSPORT` | No | `log` | `log`, `smtp`, `ses`, or `webhook` |
| `SMTP_HOST` | For SMTP | — | SMTP server hostname |
| `SMTP_PORT` | For SMTP | `587` | SMTP port |
| `SMTP_USERNAME` | For SMTP | — | SMTP username |
| `SMTP_PASSWORD` | For SMTP | — | SMTP password |
| `MONITOR_EMAIL_FROM` | For email | — | Sender address |
| `MONITOR_EMAIL_TO` | For email | — | Recipient address |
| `ALERT_WEBHOOK_URL` | For webhook | — | Webhook endpoint URL |
| `AWS_SES_REGION` | For SES | — | AWS SES region |
| `AWS_SES_ACCESS_KEY` | For SES | — | AWS access key |
| `AWS_SES_SECRET_KEY` | For SES | — | AWS secret key |
