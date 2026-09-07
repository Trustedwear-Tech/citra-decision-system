<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# Observability

Metrics, logs, traces and alerts for a running stack. The pieces are standard
open-source components -- Prometheus, Grafana, Loki, Tempo, Alertmanager --
shipped as a second Compose file with dashboards and alert rules already
provisioned. **It is off by default.** The quickstart does not start it, and
nothing in the product depends on it. You switch it on when you want to see
inside the stack.

## What ships

| Component | Role | Port |
|---|---|---|
| Prometheus | pulls `/metrics` from each service every 30 s, evaluates alert rules | 9090 |
| Alertmanager | dedupes and routes firing alerts to a webhook | 9093 |
| Loki + Promtail | Promtail tails every container's stdout and ships it to Loki | 3100 |
| Tempo | receives OTLP traces on gRPC 4317 and HTTP 4318 | 3200 |
| node-exporter | host CPU, memory, disk and network | 9100 |
| Grafana | the UI over all three stores, datasources pre-wired | 3000 |

Five dashboards are provisioned into a **Citra** folder in Grafana: services
overview, system health, logs, traces overview and alerting overview.

Eleven alert rules ship in
[`rules/citra.yml`](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/infrastructure/compose/observability/rules/citra.yml):
a Prometheus target down, an MCP circuit open or slow, the worker queue backed
up, stalled or with a non-empty dead-letter stream, discovery stale, embed
queue back-pressure, DuckDB slow, and two for backups -- no successful backup
recently, and no successful restore drill recently (see [Backups](Backups)).

## Which services expose metrics

Only some services publish `/metrics`. This matters because the shipped
Prometheus config lists every target by container name.

| Service | `/metrics` | Container in the quickstart |
|---|---|---|
| Citra-Service | yes, unauthenticated | `citra-service-dev:8085` |
| duckdb-query-service | yes | `duckdb-query-service-dev:7301` |
| reranker-service | yes | `reranker-service-dev:7302` |
| the demo tenant's MCP (source-mcp-template) | yes: query count and duration | its own container, port 8090 |
| smart-app-service, user service, discovery, app runtime, MCP service, data discovery, Playwright | no | logs only, via Promtail |

The services without a metrics endpoint are still fully observable in Loki,
because Promtail ships every container's logs regardless.

## Switching it on

The stack joins the same `citra-network` the quickstart creates, so it can
scrape the running containers by name.

1. **Fix the scrape targets.** The shipped
   [`prometheus.yml`](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/infrastructure/compose/observability/prometheus.yml)
   was written for a production layout and names containers that do not exist
   in a quickstart install (`citra-ai-prod-1`, `citra-worker`, `vault`, and so
   on). Replace the `scrape_configs` targets with the quickstart names from
   the table above, and remove the jobs for services you do not run. Every
   target that does not resolve fires `PrometheusTargetDown` until you do.

2. **Set the Grafana password.** Grafana reads `GRAFANA_ADMIN_PASSWORD` from
   the environment and falls back to `admin`. Export a real one before
   starting.

3. **Start it.**

   ```bash
   GRAFANA_ADMIN_PASSWORD='choose-one' docker compose -f infrastructure/compose/docker-compose.observability.yml up -d
   ```

4. **Open Grafana** at `http://localhost:3000`, user `admin`. The Prometheus,
   Loki and Tempo datasources and the five dashboards are already there.
   Check *Status, Targets* in Prometheus at `http://localhost:9090` to see
   that every job you kept is green.

5. **Reload after editing rules or targets** without restarting:

   ```bash
   curl -X POST http://localhost:9090/-/reload
   ```

## Where alerts go

Alertmanager's shipped
[`alertmanager.yml`](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/infrastructure/compose/observability/alertmanager.yml)
posts every alert to a webhook on the standalone Monitoring-Service, which
turns it into an email over SES or SMTP. If you do not run Monitoring-Service,
point the receiver at whatever you do run -- Slack, PagerDuty and email
receivers are all native to Alertmanager -- or the alerts fire into nothing.

## Traces

Tempo listens, but a service only sends spans if it is configured to. None of
the services do so out of the box. Point an OTLP exporter at
`http://tempo:4318` from inside the network, or `localhost:4318` from the
host, and the traces dashboard fills in.

## More than one box

If a service runs on a second host, Docker container names no longer resolve
across the network boundary. That host runs the agent compose,
[`docker-compose.obs-agent.yml`](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/infrastructure/compose/docker-compose.obs-agent.yml)
-- node-exporter plus a Promtail pointed at the central Loki -- and its
targets are added to the central Prometheus by private IP rather than by name.
The comments at the top of the shipped `prometheus.yml` say the same thing,
because it is the mistake everyone makes once.

## Exposing it

Every port above is unauthenticated except Grafana. Prometheus and Alertmanager
have no login of their own. Do not publish them to a network you do not
control without a gateway in front; an SSO-gated tunnel or a reverse proxy
with authentication is the minimum. See
[`infrastructure/compose/observability/README.md`](https://github.com/Trustedwear-Tech/citra-decision-system/blob/main/infrastructure/compose/observability/README.md)
for the config-file map, and `HOW-IT-WORKS.md` next to it for the full data
flow.
