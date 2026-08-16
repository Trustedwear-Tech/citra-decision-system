<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# data-discovery-service

Builds a tenant-scoped catalogue of every dataset reachable through any
registered Citra `dept-mcp` shim. The catalogue is what the **smart-app
builder** reads when a BA picks a dataset, and what the LLM reads at
runtime to plan reads/writes.

## What it does

For each MCP registered in `discovery-service`:

1. `GET /datasets` → list every advertised dataset.
2. `GET /datasets/{id}` → fetch full schema (columns + read_via + write_actions).
3. `GET /datasets/{id}/sample?n=N` → seed the column classifier.
4. Run `classifier.classify_column` over column names + sample values to
   set `semantic_type` and `pii` flags (Aadhaar, PAN, email, phone, …).
5. Upsert the result into Mongo `data_catalogue` keyed by
   `(tenant_id, source_id, dataset_id)`.

It does **not** move data. It calls the MCP contract and stores derived
metadata only.

## Endpoints

| Method | Path                          | Auth   | Purpose                          |
| ------ | ----------------------------- | ------ | -------------------------------- |
| GET    | `/health`                     | none   | liveness                          |
| GET    | `/catalogue`                  | Bearer | list catalogue entries (filters) |
| GET    | `/catalogue/{dataset_id}`     | Bearer | single catalogue entry            |
| POST   | `/crawl/run`                  | Bearer | one-shot tenant-scoped crawl      |
| POST   | `/crawl/dataset`              | Bearer | re-crawl a single MCP             |

## Background crawl

Disabled by default. Set `CRAWL_ENABLED=true` **and** `ORG_ID=<your-org>` and the
catalogue is rebuilt **once, on startup** — not on a timer. Source schemas change
only through IT change management, so the deliberate act after a schema change is
to **restart this service** (or call `POST /crawl/run` for an on-demand rebuild).

The crawl is tenant-scoped like any other: it mints a short-lived `org_admin`
token for `ORG_ID` and crawls as that tenant (dept-MCPs run `AUTHZ_ENFORCE=true`
and reject an unauthenticated read). `ORG_ID` is required when the crawl is
enabled — the service refuses to boot without it rather than crawl into a
phantom tenant.

Each pass also **prunes** catalogue rows the registry no longer backs — a source
removed from `sources.json`, or a table dropped from a live source. Without it a
retired dataset stayed in the catalogue forever and the builder would still offer
it. The prune is conservative: an empty registry prunes nothing, and a source
whose crawl errored is skipped entirely.

Only one replica crawls per pass (a Mongo leader-lease, released as soon as the
pass ends; `CRAWL_LOCK_TTL_SECONDS` is just the backstop for a replica killed
mid-crawl).

> Earlier versions ran a nightly `while True` loop (removed 2026-07-01) that
> crawled unauthenticated under `tenant_id="_system"`. Neither is true today.

## Architecture

```
   discovery-service (tool registry)
            │
            ▼
   list_registered_mcps()
            │
            ▼
   per-MCP:
     GET /datasets
     GET /datasets/{id}
     GET /datasets/{id}/sample
            │
            ▼
   classifier.classify_column()
            │
            ▼
   Mongo: data_catalogue
            │
            ▼
   smart-app builder UI ◀── /catalogue
   smart-app runtime    ◀── /catalogue/{id}
```

## Why this isn't Airbyte / Fivetran / Uniphore "Zero Data"

This service does not ingest, replicate, or move row data. It builds a
*map* of the enterprise's datasets so the LLM can generate the right
query at runtime against the source of record. Citra moves questions,
not data.
