<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Acme Bank & Insurance — demo tenant

India-flavoured BFSI demo: retail lending, collections, general insurance
claims, and a sales dashboard. Replaces `acme-power` as the running demo — the
platform serves **one org at a time**.

- **Contract:** [`SPEC.md`](SPEC.md) — identifiers, schema, sources, apps,
  personas, bring-up order. Every script must follow it exactly.
- **Plan and rationale:** [`../../../docs/acme-bank-demo-plan.md`](../../../docs/acme-bank-demo-plan.md),
  including the acme-power cut-over.

## Status

| Phase | State |
|---|---|
| 0 — purge stale `dept_sources` instructions | ✅ done |
| A — scaffold + SPEC + tenant/users fixtures | ✅ done |
| B — Postgres schema + deterministic seeder | ✅ done — 211,615 rows, loads in ~15s |
| C — `sources.json` generator + MCP bring-up | ✅ done — 5 sources live on :18504, NL→SQL verified |
| D — 12 SOP documents | ✅ done — ~7,800 words, ingested and retrievable |
| E — seed org/depts/users | ✅ done — 14 personas across 5 departments |
| F — 3 Decision Apps + 1 dashboard app | ✅ done — published and promoted |
| G — memory seed + E2E | ✅ done — 3 team judgements; 15/15 E2E steps |
| H — cut-over in dev, then prod | ⬜ |

## Seed the database

```powershell
cd mcp
docker compose up -d citra-ds-acme-bank-postgres
cd ..\scripts
C:\Github\Citra-AI\Citra-Service\myenv\Scripts\python.exe seed_postgres.py
```

Use Citra-Service's venv — it has `psycopg2` and `faker`. `--dry-run` generates
in memory and writes nothing. The seeder drops and recreates every table, so
re-running is safe and produces an identical database.

## Bring up the MCP

```powershell
cd scripts
C:\Github\Citra-AI\Citra-Service\myenv\Scripts\python.exe build_mcp_sources.py
cd ..\mcp
copy .env.example .env      # then fill in — see below
docker compose up -d --build citra-ds-mcp-demo-acme-bank
curl http://localhost:18504/health      # must list 4 structured + register 5
```

`.env` is **gitignored** and must stay that way — it carries the shared
`JWT_SECRET`, the `MCP_API_KEY` and provider keys. On a laptop that already
runs another demo tenant, the platform-shared values (JWT, Milvus, embeddings,
LLM, discovery, bucket) can be copied across.

**`MCP_API_KEY` is NOT tenant-unique.** data-discovery-service forwards one
global `SERVICE_API_KEY` as Bearer to every dept-MCP, so this must equal that
value or the crawler gets a 403 on `/datasets` and the catalogue comes up with
the semantic source and no structured datasets at all. Only
`MCP_PUBLIC_BASE_URL` and `BUCKET_KEY_PREFIX` genuinely differ per tenant.

Calling the MCP directly takes **two** credentials, which is easy to get
backwards: `Authorization: Bearer <MCP_API_KEY>` is the *service* key, and the
end user travels separately in `X-User-JWT` — that second one is what the
visibility PDP evaluates.

## Layout

```
acme-bank/
  SPEC.md          The contract. Read first.
  tenant.json      Org + 5 departments      → seed_tenant.py --tenant acme-bank
  users.json       14 personas              → same script
  mcp/             (Phase C) compose + sources.json — sources.json IS the
                   registry, mounted read-only as the MCP's SOURCES_FILE
  raw/policy/      (Phase D) 12 SOP markdown docs → shared dept-library collection
  scripts/         (Phase B+) seed_postgres.py, build_mcp_sources.py,
                   ingest_docs.py, seed_memory.py, acme_bank_e2e.py
  apps/            (Phase F) app specs
```

## Two things that are easy to get wrong

**One org at a time.** `data-discovery-service` pins `ORG_ID` to a single org.
Bringing acme-bank up is a cut-over, not an addition: acme-power is
deregistered and its data deleted. Sequence and rollback are in the plan §7.5 —
follow it in order, because the crawler re-files a live registration and a dead
registration over deleted data breaks the builder.

**Apps 1–3 must ship with `case_signature`.** Without one, officer corrections
are stored *uncoded*, and consolidation can only ever use uncoded corrections to
reinforce an existing judgement — never to author a new one. The app would
record feedback forever and learn nothing. The publish gate warns; treat that
warning as an error here. App 4 (sales) is dashboard pages only, makes no
officer decision, and correctly needs no signature.
