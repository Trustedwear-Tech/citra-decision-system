<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Point Citra at your own data

The stack ships with a demo bank — **Acme Bank** — already wired end to end:
four SQL sources, sixteen datasets, a SOP library, and four Decision Apps that
read them. This page turns that into *your* data.

There is no wizard and no hidden database. **The MCP's source registry is a
file.** You edit it, restart one container, and the platform picks it up.

> **Why a file?** Until July 2026 the registry lived in a Mongo collection
> (`dept_sources`) written by a wizard. That mode was removed. A file is a
> better contract: it is schema-validated, it diffs in review, your editor
> autocompletes it, and a mistake fails loudly at boot instead of silently
> mis-routing a query at 2am. The two worst bugs of the wizard era were exactly
> the silent kind a schema prevents — a source typed `structured` for every
> connector (so Mongo and REST sources were dispatched as SQL), and visibility
> keys that were quietly dropped.

---

## The 60-second version

```bash
$EDITOR demo-data/tenants/acme-bank/mcp/sources.json
make validate-sources FILE=demo-data/tenants/acme-bank/mcp/sources.json
docker compose -f demo-data/tenants/acme-bank/mcp/docker-compose.yml up -d --build
```

The MCP re-registers with `discovery-service` on boot, `data-discovery-service`
re-crawls it, and your datasets appear in the Decision App Builder's palette.

---

## What the file is

`demo-data/tenants/acme-bank/mcp/sources.json` is mounted **read-only** at
`/app/sources.json` and read via the `SOURCES_FILE` environment variable. It is
a JSON array of source objects (a `{"sources": [...]}` wrapper is also accepted).

A minimal source:

```json
[
  {
    "source_id": "loan_origination",
    "type": "structured",
    "org_id": "acme-bank",
    "dept_id": "lending",
    "name": "Loan Origination",
    "description": "Loan applications from intake through decision.",
    "connection": { "type": "postgresql", "env_prefix": "ACME_BANK_SQL" },
    "datasets": [
      {
        "id": "loan_origination.loan_applications",
        "physical_name": "loan_applications",
        "name": "Loan Applications",
        "kind": "sql",
        "description": "One row per application.",
        "columns": [
          { "name": "application_id", "is_primary_key": true, "type": "text" },
          { "name": "declared_income", "type": "numeric", "sensitivity": "confidential" }
        ]
      }
    ]
  }
]
```

`source_id`, `type`, `org_id`, `dept_id`, `name` and `description` are required.
Everything else is optional.

**Credentials never go in this file.** `connection.env_prefix` names a group of
environment variables (`ACME_BANK_SQL_HOST`, `_PORT`, `_DB`, `_USER`, `_PASS`)
supplied to the MCP container. The registry describes *what* the data is; the
environment says *how to reach it*.

## The schema is enforced, twice

`source-mcp-template/schema/sources.schema.json` is generated from the Pydantic
models in `registry_models.py`. Point your editor at it and you get completion
and inline errors.

Validate before you restart anything:

```bash
make validate-sources FILE=demo-data/tenants/acme-bank/mcp/sources.json
```

**Unknown keys are a hard failure, not a warning.** `RegistrySource`,
`RegistryDataset` and `RegistryColumn` are all `extra="forbid"`, so a typo like
`"descripton"` aborts the MCP at boot rather than leaving a silently
undocumented source. This is deliberate: a half-loaded registry produces
confidently wrong answers, which is worse than no answer.

`connection` is the one exception — it is `extra="allow"`, because it is backend
wiring and every connector needs different keys.

## Pointing at your own database

1. **Create the connection env vars.** Add them to the `environment:` block of
   `demo-data/tenants/acme-bank/mcp/docker-compose.yml`, keyed by your chosen
   `env_prefix`:

   ```yaml
   MY_WAREHOUSE_HOST: warehouse.internal
   MY_WAREHOUSE_PORT: "5432"
   MY_WAREHOUSE_DB:   analytics
   MY_WAREHOUSE_USER: citra_ro
   MY_WAREHOUSE_PASS: ${MY_WAREHOUSE_PASS:?set this in .env}
   ```

   Use a **read-only** database user. Citra writes only through explicitly
   declared `write_actions`, and those should have their own credential.

2. **Describe the source** in `sources.json` with a matching
   `"env_prefix": "MY_WAREHOUSE"`.

3. **Validate, then restart the MCP** (the two commands at the top).

4. **Check it registered:**

   ```bash
   curl -s localhost:18504/health | python -m json.tool
   ```

   You want your `source_id` in `sources` and `"registered": true`.

Supported connectors: PostgreSQL, MySQL, SQL Server, MongoDB, OData/SAP,
Salesforce, REST, BigQuery.

## Describing the data well matters more than you expect

The `description` on every source, dataset and column is not documentation —
it is **prompt context**. The planner reads those strings to decide which table
answers a question and which column means "overdue".

A column called `dpd_bkt` with description `"Days-past-due bucket: 0, 1-30,
31-60, 61-90, 90+"` gets used correctly. The same column with no description
gets guessed at. If a Decision App reasons badly about your data, the
description is the first thing to fix — before touching the model.

Mark sensitivity honestly (`public`, `internal`, `confidential`, `restricted`)
and set `pii: true` where it applies. Those drive redaction and audit.

## Ontology: the fields the apps reason over

Beyond schema, sources can carry decision structure — `value_semantics` (what
"amount" means in money terms), `fraud_screening`, `decision_history`. These are
what let a Decision App cite precedent and quantify impact rather than merely
summarise rows. Acme Bank's `sources.json` is a worked example; copy its shape.

## When the model should be yours

The default `.env` points `LLM_API_KEY` at OpenRouter so you can evaluate in
minutes without provisioning a GPU. **For production, point it at your own
endpoint** — anything OpenAI-compatible (vLLM, TGI, Ollama):

```bash
LLM_API_KEY=not-used-but-required
LLM_BASE_URL=http://vllm.internal:8000/v1
LLM_MODEL=Qwen/Qwen3-32B-Instruct
EMBEDDING_BASE_URL=http://vllm.internal:8001/v1
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_DIMENSION=768
```

Nothing else changes. Once those point inside your network, no prompt, no row
and no document leaves your infrastructure.

> Changing `EMBEDDING_MODEL` or `EMBEDDING_DIMENSION` invalidates existing
> vectors — re-run the ingestion for each tenant afterwards, or retrieval will
> silently return nonsense from a mismatched vector space.

## Starting from an empty tenant

Copy the whole directory and rename:

```bash
cp -r demo-data/tenants/acme-bank demo-data/tenants/my-org
```

Then update `tenant.json`, `users.json`, `sources.json` (`org_id`, `dept_id`),
the MCP compose (`ORG_ID`, `MILVUS_COLLECTION_PREFIX`, the host port), and the
app specs under `apps/`. Add a Postgres database for it in
`infrastructure/init-scripts/postgres-init.sql`, wire it into
`scripts/quickstart/seed-demo.sh`, then:

```bash
make seed-demo TENANT=my-org
```

## When it does not work

| Symptom | Cause |
|---|---|
| MCP exits immediately, `No source registry configured` | `SOURCES_FILE` unset or the mount path is wrong |
| MCP exits, `extra fields not permitted` | a typo'd or unknown key — run `make validate-sources` |
| Health shows `registered: false` | `DISCOVERY_URL` unreachable, or `MCP_API_KEY` differs from the platform's |
| Builder's dataset palette is empty | catalogue not crawled — `python scripts/quickstart/build_catalogue.py --org <org>` |
| Queries hit the wrong table | descriptions too thin; fix them before touching the model |
| RAG cites nothing | SOP documents not ingested, or the embedding model changed without re-ingesting |

Logs, in the order worth reading:

```bash
docker compose -f demo-data/tenants/acme-bank/mcp/docker-compose.yml logs --tail 50
docker compose -f docker-compose.quickstart.yml logs --tail 50 data-discovery-service
```
