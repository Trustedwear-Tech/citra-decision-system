<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# How Citra Observability Works

A practical guide to: what runs, what feeds what, where to look when
something breaks, and how to extend the stack.

## TL;DR — the four pillars

| Pillar | Question it answers | Stored in | How data gets in |
|---|---|---|---|
| **Metrics** | "How much / how often / how slow?" — request rate, latency, CPU, memory | **Prometheus** | Prom **pulls** every 30s by scraping `/metrics` HTTP on each service |
| **Logs** | "What happened, in words?" | **Loki** | service writes to stdout → Docker captures → **Promtail tails + pushes** to Loki |
| **Traces** | "How did this one user request flow across services?" | **Tempo** | service emits **OTLP spans + pushes** to Tempo on `:4317` (gRPC) or `:4318` (HTTP) |
| **Errors** | "What blew up, where, with what stacktrace?" | **GlitchTip** | service catches exception via `sentry_sdk` → **pushes** to GlitchTip DSN |

**Grafana** is just the UI in front of Prometheus, Loki, and Tempo — it
stores nothing.

Plus two operational pieces:
- **Alertmanager** — Prom rules fire → Alertmanager dedupes/groups/routes them
- **Monitoring-Service** — receives Alertmanager webhook → sends email
  via AWS SES (also tails container log files for ERROR patterns directly)

---

## Full data flow

```
                          ┌─────────────────────────────────────────────────────────────┐
                          │                  YOUR SERVICES                              │
                          │  Citra-Service x8, citra-workflow, smart-app-service, …     │
                          │  (Vault AppRole at startup → env populated)                 │
                          │                                                             │
                          │  emits 4 outgoing telemetry streams:                        │
                          └──┬──────────────┬──────────────────┬───────────────────┬───┘
                             │              │                  │                   │
                  metrics    │   logs       │   traces         │   errors          │
                  (pulled)   │   (stdout)   │   (OTLP pushed)  │   (Sentry SDK)    │
                             ▼              ▼                  ▼                   ▼
                       ┌──────────┐    ┌──────────┐      ┌──────────┐        ┌──────────┐
                       │PROMETHEUS│    │ PROMTAIL │      │  TEMPO   │        │GLITCHTIP │
                       │  scrapes │    │  agent   │      │  :4318   │        │  /api/   │
                       │  /metrics│    │  tails   │      │   OTLP   │        │store/    │
                       │  every   │    │  docker  │      │ receiver │        │          │
                       │  30s     │    │  logs +  │      │          │        │          │
                       │          │    │ /var/log │      │          │        │          │
                       └─────┬────┘    └─────┬────┘      └─────┬────┘        └─────┬────┘
                             │               │                 │                   │
                             │               ▼                 │                   │
                             │           ┌──────┐              │                   │
                             │           │ LOKI │              │                   │
                             │           │ (TSDB│              │                   │
                             │           │ for  │              │                   │
                             │           │ logs)│              │                   │
                             │           └──┬───┘              │                   │
                             │              │                  │                   │
                             ▼              ▼                  ▼                   │
                       ┌─────────────────────────────────────────────┐             │
                       │              GRAFANA  (UI only)             │             │
                       │  unified dashboards across 3 datasources    │             │
                       │  Explore → Prom / Loki / Tempo dropdown     │             │
                       │  Dashboards → folder "Citra" (3 pre-built)  │             │
                       └─────────────────────────────────────────────┘             │
                                                                                   │
                       ┌─────────────────────────────────────────────┐             │
                       │  Prometheus rule fires (e.g. CPU > 90%)     │             │
                       │             │                               │             │
                       │             ▼                               │             │
                       │       ALERTMANAGER (dedupe, group, route)   │             │
                       │             │                               │             │
                       │             ▼ webhook POST                  │             │
                       │       Monitoring-Service :8400              │             │
                       │             │                               │             │
                       │             ▼                               │             │
                       │       AWS SES → rohit@trustedweartech.com   │             │
                       └─────────────────────────────────────────────┘             │
                                                                                   │
                                                                                   ▼
                                                              GlitchTip UI: errors
                                                              grouped by stacktrace,
                                                              user counts, release tags
```

---

## What runs where (all on Citra-AI-1)

| Container | Image | Internal port | Purpose |
|---|---|---|---|
| `citra-prometheus` | prom/prometheus | 9090 | Metrics TSDB + alert rule evaluation |
| `citra-alertmanager` | prom/alertmanager | 9093 | Alert dedupe + routing |
| `citra-loki` | grafana/loki | 3100 | Log database (filesystem storage, 7d retention) |
| `citra-promtail` | grafana/promtail | (no port) | Log shipper sidecar |
| `citra-tempo` | grafana/tempo | 3200 query, 4317 gRPC, 4318 HTTP | Trace database |
| `citra-grafana` | grafana/grafana | 3000 | UI for all 3 above |
| `glitchtip-web` | glitchtip/glitchtip | 8080 | Sentry-compatible error tracker UI + API |
| `glitchtip-worker` | glitchtip/glitchtip | — | Background event processing |
| `glitchtip-postgres` | postgres:15 | 5432 (internal) | Error storage |
| `glitchtip-redis` | redis:7 | 6379 (internal) | GlitchTip task queue |
| `monitoring-service` | citra-ai/monitoring-service | 8400 | Alertmanager webhook receiver + SES sender + log fingerprinting |
| `cloudflared` | cloudflare/cloudflared | — | Outbound tunnel to CF edge for public UIs |

All 12 are on `citra-network` (docker bridge). Services reach each
other by container name (e.g. `http://citra-loki:3100`).

---

## Public access (the 4 obs URLs)

| URL | Backend container | Notes |
|---|---|---|
| https://grafana.citra-ai.com | `citra-grafana:3000` | **Spend 90% of time here.** Dashboards + Explore. |
| https://glitchtip.citra-ai.com | `glitchtip-web:8080` | Error triage. Native account login behind CF Access. |
| https://prom.citra-ai.com | `citra-prometheus:9090` | Raw PromQL UI. Status→Targets shows scrape health. |
| https://alerts.citra-ai.com | `citra-alertmanager:9093` | See firing alerts. **Silence** noisy ones during maintenance. |

Public path:
```
Browser → Cloudflare edge → CF Access (SSO challenge)
                              │ allow if email ∈ @trustedweartech.com OR @citra-ai.com
                              ▼
                        Cloudflare Tunnel (id 3b618743-…)
                              │ outbound QUIC, no inbound rules
                              ▼
                  cloudflared container on Citra-AI-1
                              │ ingress rules → container by name
                              ▼
                    citra-grafana / glitchtip-web / …
```

Full operator runbook for the tunnel: [`cloudflared-tunnel.md`](./cloudflared-tunnel.md).

---

## How each pillar actually works in code

### 1. Metrics — services expose `/metrics`, Prom scrapes

Each FastAPI service has at the top of `main.py`:
```python
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not installed; /metrics disabled")
```

This adds a `/metrics` endpoint on the service that exposes:
- `http_requests_total{job, handler, status_code}` — counter
- `http_request_duration_seconds_bucket` — histogram for latency percentiles
- `process_cpu_seconds_total`, `process_resident_memory_bytes` — host stats

Prometheus scrapes each service every 30s. Scrape targets are defined
in [`prometheus.yml`](./prometheus.yml) — one job per service, pointing
at the container's internal port. Container names resolve via
docker-network DNS.

**`Citra-Worker` is different** — it has no HTTP API. Instead it calls
`prometheus_client.start_http_server(8080)` to expose `/metrics` on a
dedicated port. Prom scrape job for `citra-worker` points at port 8080.

### 2. Logs — Promtail tails docker, ships to Loki

Promtail config in [`promtail-config.yml`](./promtail-config.yml).
Actively shipping logs from 27+ containers via docker log-driver tail
(per Loki labels: `service`, `container`, `service_name`, `stream`).

Retention is enforced in Loki itself: 7 days (`retention_period: 168h`
in `loki-config.yml`), with the compactor running every 10 min to
purge older chunks. Disk usage stays bounded.

To find a service's logs:
```
Grafana → Explore → datasource: Loki → query: {service="citra-workflow"}
```

Or filter for errors:
```
{service="citra-workflow"} |~ "(?i)(error|exception|traceback)"
```

### 3. Traces — services push OTLP to Tempo

In each service's `main.py`:
```python
from citra_service_utils import setup_tracing
setup_tracing(app, service_name="citra-workflow")
```

`setup_tracing()` reads `OTEL_EXPORTER_OTLP_ENDPOINT` (= `http://citra-tempo:4318`
in Vault bag) and configures the OpenTelemetry SDK to push spans for
every inbound FastAPI request + outbound httpx call.

⚠️ **Current gap (2026-05-18):** Tempo has zero traces — services are
not actually pushing. Need to investigate why `setup_tracing()` isn't
emitting (likely a config issue or OTel SDK init failing silently).
This is the only pillar not yet fully operational.

### 4. Errors — services push exceptions to GlitchTip

In each service:
```python
from observability import init_sentry
init_sentry("citra-workflow")
```

`init_sentry()` reads `SENTRY_DSN` (from Vault bag) and inits
`sentry_sdk` with FastAPI/Starlette/Asyncio/Logging integrations. Any
unhandled exception from a request handler → captured with full
stacktrace + request context + breadcrumbs → POSTed to GlitchTip's
ingest endpoint.

`SENTRY_DSN` is typically `https://<key>@glitchtip-web:8080/<project-id>`
when GlitchTip is internal. Each service is its own project in
GlitchTip — easier to filter errors per service.

---

## Alerts

### Rules (Prometheus)

Defined in [`rules/citra.yml`](./rules/) (~7 alert rules as of 2026-05-18):

- `DeptBackupStale` — no successful dept backup in 24h, critical
- `DeptBackupRestoreDrillStale` — no restore drill in 45d, critical
- `MCPCircuitOpen` — circuit breaker tripped, warning
- + ~4 more — see the file

To add a new alert: edit `rules/citra.yml`, mount-reload Prom via
`curl -X POST http://prom.citra-ai.com/-/reload`.

### Routing (Alertmanager + Monitoring-Service)

```
Prom rule fires
  → Alertmanager (group_by [alertname, component, dept])
  → webhook POST http://monitoring-service:8400/webhook/alert
  → Monitoring-Service (cooldown 600s per alert + service)
  → AWS SES (boto3 from ap-south-1)
  → rohit@trustedweartech.com
```

Recipient changes: edit `Monitoring-Service/docker-compose.yml`,
`EMAIL_DEFAULT_TO=` (comma-separated). Push via
`scripts/push-box-services.ps1 -Force -Only Monitoring-Service` then
`docker compose up -d --force-recreate`.

Full breakdown: see memory [`project_alert_email_routing`].

### When alerts fire, where to triage

| Step | Tool | URL |
|---|---|---|
| 1. See what's firing | Alertmanager | https://alerts.citra-ai.com |
| 2. Silence noisy ones during maintenance | Alertmanager | https://alerts.citra-ai.com → New Silence |
| 3. Investigate metric the alert is on | Grafana | https://grafana.citra-ai.com → search metric name |
| 4. Drill into logs at that timestamp | Grafana → Loki | Explore → Loki → `{service="..."}` |
| 5. If there's a stacktrace, look there | GlitchTip | https://glitchtip.citra-ai.com |
| 6. Trace request if cross-service | Grafana → Tempo | (once Tempo gap fixed) |

---

## Trace ↔ log correlation

[`grafana/datasources.yml`](./grafana/datasources.yml) has correlation rules:

- In a **log line** (Loki): if the line contains `trace_id=<hex>`, Grafana
  shows a clickable "View trace" link that jumps to Tempo.
- In a **Tempo span**: a "Logs for this span" button queries Loki for the
  same service over the span's time window.

This is the killer feature of having all 3 pillars wired — debug
flows: alert → metric → log → trace.

---

## "I notice a problem, where do I look?" cheat sheet

| Symptom | First stop | Then |
|---|---|---|
| Latency suddenly high | **Grafana** → Citra-Services-Overview dashboard | drill into specific service's p95 panel; Tempo trace if cross-service |
| 500s on a service | **GlitchTip** → see grouped error + stacktrace | Loki for surrounding log context |
| Container won't start | **Grafana → Loki** `{service="..."}` filter for last 5m | `docker logs <name>` if Loki doesn't have it (Promtail might be catching up) |
| Is the service even being scraped? | **Prom** → Status → Targets | If DOWN: check `/metrics` endpoint exists |
| Alert won't stop pinging me | **alerts.citra-ai.com** → silence for 4h | then investigate the actual issue |
| Random user complaint | **Grafana → Loki** filter by user_id from JWT logs | follow `trace_id` → Tempo for full request path |

---

## Adding a new service to observability

1. **Metrics**: in `main.py`, add `prometheus-fastapi-instrumentator`
   (FastAPI) or `prometheus_client.start_http_server(8080)` (worker).
2. **Logs**: services log to stdout — Promtail picks them up
   automatically (no config change needed; container name becomes the
   `service` label in Loki).
3. **Traces**: call `setup_tracing(app, service_name=...)` from
   `citra-service-utils`. OTEL env vars come from Vault bag.
4. **Errors**: call `init_sentry("service-name")` from `observability.py`.
   `SENTRY_DSN` must be in the Vault bag for the service.
5. **Prom scrape**: add the service as a new job in `prometheus.yml`
   here. Reload Prom.
6. (Optional) **Add dashboard panel**: edit
   `grafana/dashboards/citra-services-overview.json` to include the
   new service's `job` label, or create a service-specific dashboard.

---

## What's NOT in the stack (gaps & out-of-scope)

| Missing | When you'd add it |
|---|---|
| **Mimir / Thanos / VictoriaMetrics** | When Prom's 30d retention isn't enough or you need HA. Not needed at current scale. |
| **Synthetic / blackbox monitoring** | Real-user perf. Add `prom-blackbox-exporter` when you care. |
| **PagerDuty / Opsgenie** | When email isn't fast enough for paging. Today: email only. |
| **APM** (DataDog/NewRelic) | Commercial all-in-one. You've built the OSS equivalent. |
| **RUM** (browser-side) | Frontend perf metrics. Requires JS snippet in Citra-UI. |

---

## Related docs

- [`./README.md`](./README.md) — quick reference, public URLs, troubleshooting
- [`./cloudflared-tunnel.md`](./cloudflared-tunnel.md) — public access (CF Tunnel + Access) operator runbook
- [`./prometheus.yml`](./prometheus.yml) — scrape targets
- [`./rules/`](./rules/) — alert rules
- [`./loki-config.yml`](./loki-config.yml) — log retention
- [`./grafana/datasources.yml`](./grafana/datasources.yml) — datasource provisioning + trace-log correlation
- [`./grafana/dashboards/`](./grafana/dashboards/) — 3 starter dashboards
