<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra Decision System -- Architecture

## 1. What this is

An agent recommends a decision on one case at a time, grounded in your own
SOPs and enterprise data. A human approves it. The outcome feeds back so the
next recommendation is sharper. Everything in this repository serves that
loop -- authoring the agent, running it, connecting it to your systems, and
recording what happened.

## 2. Services

### The decision path

| Service | Port | Responsibility |
|---|---|---|
| **smart-app-service** | 9100 | The core: authors Decision Apps from plain English, runs the agent loop, records decisions, learns from outcomes. |
| **citra-app-runtime** | 3100 | Next.js renderer for a published Decision App. Embedded in the Citra-UI shell, never standalone. |
| **source-mcp-template** | 18504+ | The dept-MCP image. One container per tenant; governed read/write to that tenant's systems. **File-defined** -- see §3. |
| **discovery-service** | 9010 → 9000 | Registry of running MCPs. Each MCP self-registers on boot. The only service whose host port differs from its container port: sibling containers call `discovery-service:9000`, but from your machine it is **9010**. |
| **data-discovery-service** | 8095 | Crawls registered MCPs into a searchable data catalogue for the builder's dataset palette. |

### Platform

| Service | Port | Responsibility |
|---|---|---|
| **Citra-Service** | 8085 | Chat, documents, the SOP library, the RAG reader. |
| **Citra-UI** | 8081 | The single shell (Expo / React Native web). |
| **Citra-User-Service** | 7004 | Auth, orgs, departments, users, service accounts. |
| **citra-mcp-service** | 9090 | Sandbox toolbelt (web, files, OCR, discovery) for builder pods. |
| **action-sandbox-host** | 7090 | Spawns builder pods for authoring a Decision App. |
| **duckdb-query-service** | 7301 | In-process analytics over structured files. |
| **reranker-service** | 7302 | Retrieval reranking. |
| **playwright-render-service** | 3001 | Headless render. |
| **Monitoring-Service** | -- | Log/health/container monitoring with forensic alerts; no autonomous action. |
| **decision-api-sdk** | -- | Client SDK for the headless Decision API. |

### Shared packages

Six, each independently installable, none depending on another: `citra-auth`,
`citra-mongo`, `citra-cache`, `citra-queue`, `citra-llm`, `citra-service-utils`.
A consumer that only needs JWT verification does not end up installing Redis,
Mongo, and a vector database as a side effect.

### Data stores

MongoDB (application state), Milvus (vectors), MinIO (object storage), Redis
x2 (cache and a durable job queue), Postgres (used by the bundled demo
tenant's system-of-record only -- not required for the platform itself).

## 3. The MCP is file-defined

A dept-MCP loads its source registry from a **local `sources.json`**, mounted
read-only at `/app/sources.json` via `SOURCES_FILE`. There is no central
service holding every tenant's connection strings -- each MCP container only
ever knows about the systems its own `sources.json` names.

The registry schema is strict: an unrecognised key is a hard boot failure by
design. A half-loaded registry that starts anyway produces confidently wrong
answers, which is worse than refusing to start. Validate a registry before
using it:

```bash
make validate-sources FILE=demo-data/tenants/acme-bank/mcp/sources.json
```

See `docs/change-the-demo.md` for how to point a deployment at your own
sources instead of the bundled `acme-bank` demo tenant.

## 4. Conventions worth knowing

- **Fail loud.** No silent defaults and no swallowed exceptions. A missing
  bucket, an unreachable database, or an empty model API key exits
  non-zero at startup rather than limping forward -- a warning that lets a
  broken deployment look healthy is treated as a bug.
- **A guard that stops guarding is worse than none.** Safety checks
  (permission gates, write guards) are built to actually block the case they
  name, not to exist symbolically.
- **This tree is LF.** `.gitattributes` normalises every text file to `eol=lf`
  so shell scripts run correctly on Linux/macOS on first clone.

## 5. Where to go next

- `README.md` -- quickstart and license.
- `docs/change-the-demo.md` -- replace the bundled demo tenant with your own
  data sources.
- `CONTRIBUTING.md` / `SECURITY.md` -- how to contribute or report a
  vulnerability.
