<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Observability stack

The Citra observability stack lives on **Citra-AI-1** (the app box) and is
reachable from the public internet via per-service `*.citra-ai.com`
subdomains, gated by Cloudflare Access SSO.

## Topology

```
                              public internet
                                     │
                                     ▼
                           Cloudflare edge + Access
                              (SSO: @trustedweartech.com,
                               OTP-by-email, 8h session)
                                     │
                                     ▼
                        Cloudflare Tunnel  citra-obs
                              (id 3b618743-…)
                                     │ outbound QUIC, no inbound rules
                                     ▼
                    cloudflared (container on Citra-AI-1)
                                     │ joined to citra-network
                                     ▼
        ┌───────────────────┬───────────────────┬───────────────────┬───────────────────┐
        ▼                   ▼                   ▼                   ▼                   ▼
   citra-grafana:3000  glitchtip-web:8080  citra-prometheus:9090  citra-alertmanager:9093
        │                                            │                                  │
        │ datasources (Prom, Loki, Tempo)            │                                  │
        ▼                                            ▼                                  ▼
   citra-loki:3100   citra-tempo:3200 :4317 :4318          rules at ./rules/  → alerts
                                                            (alertmanager.yml routes
                                                             alerts to Monitoring-Service
                                                             webhook on :8400)

  promtail (sidecar) — tails /var/log + docker container logs → ships to Loki
```

## Public URLs

| URL | Backend | Notes |
|---|---|---|
| https://grafana.citra-ai.com | `citra-grafana:3000` | Dashboards. Datasources Prom, Loki, Tempo pre-wired. Native admin login behind CF Access. |
| https://glitchtip.citra-ai.com | `glitchtip-web:8080` | Sentry-compatible error tracker. Has its own user accounts behind CF Access. |
| https://prom.citra-ai.com | `citra-prometheus:9090` | Raw PromQL UI + targets page. No native auth — CF Access is the only gate. |
| https://alerts.citra-ai.com | `citra-alertmanager:9093` | Alert silences + status. No native auth — CF Access is the only gate. |

**SSO:** every URL hits CF Access first. Visitor enters their work email →
receives a one-time PIN by email → enters PIN → 8-hour session.

### Who can access (CF Access policy)

Allow if email matches either of these domains (OR-logic):
- `*@trustedweartech.com`
- `*@citra-ai.com`

Any email outside both domains is **denied** automatically (CF Access default
deny). No exceptions configured — to grant access to a one-off email or
restrict to specific people, see [cloudflared-tunnel.md](./cloudflared-tunnel.md)
"Restricting access tighter".

Adjust in the Cloudflare dashboard:
https://one.dash.cloudflare.com → Access → Applications → pick one of the
4 "Citra Obs: …" apps → Policies tab → edit the "Allow …" policy.

Or via API (one PUT per app, see runbook section "Adding a new obs URL"
for the body format).

## Config files (this folder)

| File | What |
|---|---|
| `prometheus.yml` | Scrape targets + global config. Static config; reload via `curl -X POST http://localhost:9090/-/reload` after edits. |
| `rules/*.yml` | Prometheus alert rules. Auto-loaded. |
| `alertmanager.yml` | Routes + receivers (Monitoring-Service webhook by default). |
| `loki-config.yml` | Loki storage + retention (currently filesystem). |
| `promtail-config.yml` | Log sources (docker containers, /var/log). |
| `tempo.yml` | Tempo receivers (OTLP/HTTP :4318) + storage. |
| `grafana/` | Grafana provisioning (datasources, dashboards). |

Secret deployment values live outside git. On Citra-AI-1, Grafana's native
admin password is stored in `/home/ubuntu/citra-ai/obs/.env` as
`GRAFANA_ADMIN_PASSWORD=<REDACTED>`.

## Running services (on Citra-AI-1)

| Container | Image | Port |
|---|---|---|
| `citra-grafana` | grafana/grafana:11.2.0 | 3000 |
| `citra-prometheus` | prom/prometheus | 9090 |
| `citra-alertmanager` | prom/alertmanager | 9093 |
| `citra-loki` | grafana/loki | 3100 |
| `citra-tempo` | grafana/tempo | 3200, 4317, 4318 (OTLP gRPC + HTTP) |
| `citra-promtail` | grafana/promtail | (sidecar, no port) |
| `glitchtip-web` | glitchtip/glitchtip | 8080 |
| `glitchtip-worker` | glitchtip/glitchtip | (worker) |
| `glitchtip-postgres` | postgres:15 | 5432 (internal only) |
| `glitchtip-redis` | redis:7 | 6379 (internal only) |
| `cloudflared` | cloudflare/cloudflared | (outbound only) |

All on `citra-network` (docker bridge). The `cloudflared` container reaches
each backend by its docker container name (e.g. `citra-grafana:3000`).

## Public-access management

For everything about adding hostnames, rotating the tunnel, or changing the
SSO policy, see [cloudflared-tunnel.md](./cloudflared-tunnel.md).

## Service-side observability config

Each service in the platform either:
1. Calls `from citra_service_utils import setup_tracing` and sends OTLP
   traces to `http://citra-tempo:4318/v1/traces` for OTLP/HTTP (via
   `OTEL_EXPORTER_OTLP_ENDPOINT` in Vault or compose env), OR
2. Has `prometheus-fastapi-instrumentator` exposing `/metrics`, scraped
   by Prometheus per `prometheus.yml`.

For errors, services init `sentry_sdk` against the `SENTRY_DSN` in their
Vault bag (DSN points at GlitchTip's project URL).

## Adding a new service to observability

1. Service `main.py`: add `setup_tracing(app, service_name=…)` from
   `citra-service-utils` (if Python) — picks up OTEL env vars from Vault.
2. Service exposes `/metrics` via `prometheus-fastapi-instrumentator`
   (FastAPI) or `prometheus_client.start_http_server` (worker).
3. Add scrape job to `prometheus.yml` here, commit, push, and reload prom
   on the box.
4. (Optional) Add a GlitchTip project + paste its DSN to the service's
   Vault bag.

## Troubleshooting

- **`https://grafana.citra-ai.com` returns 502 / Bad Gateway:**
  cloudflared can't reach the backend. Check:
  - `docker ps --filter name=cloudflared` running?
  - `docker logs cloudflared` for ingress errors?
  - `docker exec cloudflared wget -qO- http://citra-grafana:3000/api/health`
    works inside cloudflared?
- **CF Access login loop:** browser cookies stale. Try incognito or clear
  `cloudflareaccess.com` cookies.
- **Prometheus shows target `down`:** `/metrics` endpoint not exposed by
  the service. `curl` it directly inside `citra-network`:
  `docker run --rm --network citra-network curlimages/curl <svc>:<port>/metrics`.
- **Alerts silent:** check `alertmanager.yml` receivers + Monitoring-Service
  webhook on `:8400`.
- **Grafana password reset:** `docker exec -it citra-grafana grafana cli
  admin reset-admin-password <new>`. (Native Grafana password, separate
  from CF Access SSO.)

## Related docs / memory

- Vault AppRole rollout for services: see CLAUDE memory
  `project_vault_approle_rollout`.
- Vault KMS auto-unseal: `project_vault_kms_auto_unseal`.
- CF tunnel runbook: `./cloudflared-tunnel.md`.
