<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Point Citra at your own data

The stack ships with a demo bank — **Acme Bank** — wired end to end: four SQL
sources, sixteen datasets, a SOP library, and four Decision Apps that read them.
This page replaces that with *your* data.

There are three routes. They differ in how much you hand-author, not in what
they produce — all three end with the same thing: a `sources.json` describing
your data, an MCP serving it, and the catalogue crawled so the builder can see
it.

| | route | use it when |
|---|---|---|
| **1** | `make wizard` → *My own database* | first time, one SQL or Mongo source |
| **2** | `make_mcp.py --conn` | adding a second source, or scripting it |
| **3** | hand-author `sources.json` | REST, fraud screening, media columns, multi-source departments |

> **Do not point your data at the demo tenant.** Editing
> `demo-data/tenants/acme-bank/` puts your source inside the demo's
> organisation, with two MCPs answering for one org. Earlier versions of this
> page told you to do exactly that. Use your own `org_id`.

---

## Route 1 — the wizard

```bash
make wizard
```

Choose **2) My own database**. It asks for a connection string and a department,
then reads your schema and interviews you about what a scan cannot infer.

It writes the ontology to `my-source/sources.json`, generates an MCP for it under
`deployments/<org>/mcp/`, puts the credentials in the root `.env`, starts the
container, waits for it to register with discovery, and crawls the catalogue.

**Pick an `org_id` that is not `acme-bank`.** The prompt suggests `northwind`.

### Testing it without a real database

```bash
make source-db
```

Stands up a separate Postgres on port **15544**, database `northwind`, seeded
with the same generator as the demo — its own container, volume and compose
project, sharing nothing with the demo tenant. It prints the connection string
to paste at the wizard's prompt. `make source-db ARGS=--fresh` rebuilds it;
`ARGS=--down` stops it and keeps the data.

It is deliberately **not** on `citra-network`, because a customer's database
would not be either.

---

## Route 2 — generate an MCP from a connection string

For a second source, or a scripted install:

```bash
python scripts/quickstart/make_mcp.py \
  --org northwind --depts ops \
  --sources my-source/sources.json \
  --conn "postgresql://user:pass@localhost:5432/mydb" \
  --up
```

It writes `deployments/<org>/mcp/docker-compose.yml`, copies the registry in
beside it, parses `--conn` into the `{PFX}_*` variables in your root `.env`, and
with `--up` builds and starts the container. On boot the MCP registers its
sources with `discovery-service`.

Then crawl, or the builder's palette stays empty:

```bash
JWT_SECRET=... python scripts/quickstart/build_catalogue.py --org northwind
```

### `localhost` is not localhost

The wizard introspects from your **host**; the MCP reads from a **container**,
where `localhost` is the container itself. `--conn` rewrites a loopback address
to `host.docker.internal` and says so. If you set the variables by hand, do the
same — or use the database's container name if it is on the same network.

---

## Route 3 — hand-author the file

Everything the interview writes, you can write. Some things only you can:

- **REST/API sources** — introspected from an OpenAPI spec, and the spec does
  not say how to *call* the API. `connection.base_url`, the auth `env_prefix`
  and `options.invocation_template` are yours. See `source-mcp-template/docs/sources-file.md` §5.1.
- **`fraud_screening` and `artifact_role`** — never written by any guided flow,
  by design. Screening fingerprints real bytes across a whole corpus; whether it
  finds fraud or cries wolf depends on how alike your documents already are,
  which no interview can know. Author it by hand against your real documents and
  check the findings before anyone relies on them.
- **Multi-source departments** — the wizard writes the first source; copy its
  shape for the rest.

```bash
$EDITOR my-source/sources.json
make validate-sources FILE=my-source/sources.json
docker compose --env-file .env -f deployments/<org>/mcp/docker-compose.yml up -d --build
```

The rebuild matters: the MCP copies the registry in when it is generated and
loads it **once, at boot**. A container left running serves the file it started
with.

---

## What the file is

The registry is mounted read-only at `/app/sources.json` and read via
`SOURCES_FILE`. It is a JSON array of source objects (a `{"sources": [...]}`
wrapper is also accepted).

A minimal source:

```json
[
  {
    "source_id": "loan_origination",
    "type": "structured",
    "org_id": "northwind",
    "dept_id": "ops",
    "name": "Loan Origination",
    "description": "Loan applications from intake through decision.",
    "connection": { "type": "postgresql", "env_prefix": "NORTHWIND_SQL" },
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
environment variables — `{PFX}_HOST`, `_PORT`, `_DB`, `_USER`, `_PASS` for SQL,
`{PFX}_URI` and `_DB` for Mongo — supplied to the MCP container. The registry
describes *what* the data is; the environment says *how to reach it*.

Those variables live in the **root `.env`**, and the generated compose
references them as `${VAR:?...}` pass-throughs. Do not paste literal values into
the compose's `environment:` block: an `environment:` entry **overrides**
`env_file`, so a credential correctly set in `.env` is blanked out by the very
line meant to declare it.

## Document and image columns

A column holding a location rather than a value needs `column_kind`, or an
officer cannot open the document a recommendation cites — and nothing errors,
because the column still reads as a string:

```json
{ "name": "kyc_document_url", "type": "text",
  "column_kind": "document_url", "mime_hint": "application/pdf" }
```

`column_kind` is exactly one of `plain`, `url`, `image_url`, `document_url`,
`file`. The wizard asks about every link-shaped column and warns at save time
about ones you left unmarked. `artifact_role` is separate, drives fraud
screening only, and is never set for you.

## The schema is enforced, twice

`source-mcp-template/schema/sources.schema.json` is generated from the Pydantic
models in `source-mcp-template/registry_models.py`. Point your editor at it for completion and
inline errors, and validate before restarting anything:

```bash
make validate-sources FILE=my-source/sources.json
```

**Unknown keys are a hard failure, not a warning.** `RegistrySource`,
`RegistryDataset` and `RegistryColumn` are all `extra="forbid"`, so a typo like
`"descripton"` aborts the MCP at boot rather than leaving a silently
undocumented source. A half-loaded registry produces confidently wrong answers,
which is worse than no answer.

`connection` is the one exception — it is `extra="allow"`, because it is backend
wiring and every connector needs different keys.

Supported connectors: PostgreSQL, MySQL, SQL Server, MongoDB, OData/SAP,
Salesforce, REST, BigQuery.

## Descriptions matter more than you expect

The `description` on every source, dataset and column is not documentation — it
is **prompt context**. The planner reads those strings to decide which table
answers a question and which column means "overdue".

A column called `dpd_bkt` described as `"Days-past-due bucket: 0, 1-30, 31-60,
61-90, 90+"` gets used correctly. The same column with no description gets
guessed at. If a Decision App reasons badly about your data, the description is
the first thing to fix — before touching the model.

Mark sensitivity honestly (`public`, `internal`, `confidential`, `restricted`)
and set `pii: true` where it applies. Those drive redaction and audit.

## Ontology: the fields the apps reason over

Beyond schema, sources carry decision structure — `value_semantics` (what
"amount" means in money terms), `decision_history`, `write_actions`. These are
what let a Decision App cite precedent and quantify impact rather than merely
summarise rows. `source-mcp-template/docs/sources-file.md` documents every field; Acme Bank's
`sources.json` is a worked example.

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
> vectors — re-run ingestion afterwards, or retrieval silently returns nonsense
> from a mismatched vector space.

## Removing the demo

Once your own source works, the demo tenant is just taking up disk:

```bash
docker compose --env-file .env \
  -f demo-data/tenants/acme-bank/mcp/docker-compose.yml down -v
```

`--env-file` is not optional there. The tenant compose lives in its own
directory, so compose looks for `.env` beside it, finds none, and fails
interpolating `MCP_API_KEY`.

## When it does not work

| Symptom | Cause |
|---|---|
| MCP exits, `No source registry configured` | `SOURCES_FILE` unset or the mount path is wrong |
| MCP exits, `extra fields not permitted` | a typo'd or unknown key — run `make validate-sources` |
| MCP exits, `SOURCES_FILE is a directory` | the registry was not copied next to the compose, so docker created a directory at the mount path |
| MCP starts but `[REGISTRATION] Failed to register` | `DISCOVERY_URL` unreachable, or `MCP_API_KEY` differs from the platform's |
| MCP cannot reach the database | `{PFX}_HOST` is `localhost`, which inside a container means the container — use `host.docker.internal` or the container name |
| `required variable X is missing a value` | `.env` absent, or a compose in another directory invoked without `--env-file` |
| Builder's dataset palette is empty | catalogue not crawled — `python scripts/quickstart/build_catalogue.py --org <org>` |
| Edited `sources.json`, nothing changed | the MCP loads it once at boot — recreate the container |
| A cited document will not open | the column has no `column_kind` |
| Queries hit the wrong table | descriptions too thin; fix them before touching the model |
| RAG cites nothing | SOPs not ingested, or the embedding model changed without re-ingesting |

Logs, in the order worth reading:

```bash
docker compose --env-file .env -f deployments/<org>/mcp/docker-compose.yml logs --tail 50
docker compose -f docker-compose.quickstart.yml logs --tail 50 data-discovery-service
docker compose -f docker-compose.quickstart.yml logs --tail 50 discovery-service
```
