<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Wizard: two paths that both finish — plan

**Status:** plan. Nothing implemented yet.

**Problem.** The wizard fully installs the DEMO and only signposts the custom
path. A bring-your-own-database user finishes with data stores running, no
services, no org, and a printed list of commands. Since the catalogue is
`tenant_id`-scoped, even a perfect `sources.json` produces a catalogue no one
can reach, because no org exists that matches what the sources declare.

**Goal.** Both options end at a working system the user can sign into.

---

## Part 1 — Drop Runware and Serper from the wizard

**Verified before proposing it:**

- Both default to `""` in `citra-mcp-service/config.py` — genuinely optional.
- **None of the four acme-bank Decision Apps reference image generation or web
  tools.** A grep across `demo-data/tenants/acme-bank/apps/` returns nothing.
- Consumers are `citra-mcp-service/tools/{image,web}.py`, the action-sandbox
  toolkit, and `Citra-Service`'s reader/serper service — all inert without a key.

**So:** remove Step 3 from the wizard. Two prompts, both answered "no" by
almost everyone, on the path to a first run. Runware also costs money per image,
which is a poor thing to meet before you have seen the product work once.

Nothing is deleted from the codebase. `IMAGE_GEN_*` and `SERPER_API_KEY` stay
supported in `.env` and `.env.example`, documented as post-install extras. The
wizard drops from 5 steps to 4.

---

## Part 2 — What each path must actually do

`seed-demo.sh` already encodes the correct sequence. A custom setup is the same
shape with different inputs, and the ORDER is the hard part:

| # | Demo | Custom |
|---|---|---|
| 0 | validate `sources.json` | validate `sources.json` |
| 1 | **seed org + depts + users** | **seed org + dept + admin** |
| 2 | seed Postgres SoR (211k rows) | *skip — the data is already theirs* |
| 3 | start MCP with `SOURCES_FILE` | start MCP with `SOURCES_FILE` |
| 4 | ingest SOP documents | *optional, skip for v1* |
| 5 | refresh the data catalogue | refresh the data catalogue |
| 6 | publish 4 Decision Apps | *nothing to publish yet — user builds one* |

### The ordering constraint

The custom path **cannot** simply call `start.sh` at the end, because
`sources.json` does not exist until the user has authored it. Two stages:

```
setup.sh                    data stores only
  └─ author sources.json    template + introspect + validate   <- new
start.sh                    services + super-admin (no --demo)
  └─ seed org               org + dept + org-admin             <- new
  └─ start MCP              SOURCES_FILE -> registers to discovery
  └─ crawl catalogue        data-discovery with ORG_ID
```

The demo dodges this by generating its sources file before starting.

---

## Part 3 — Org seeding for the custom path

`seed_tenant.py` is **already generic** — its docstring says so, it only POSTs
to public Citra-User-Service admin APIs (no Mongo writes), and it takes
`--tenants-root`. What is missing is a caller and the two fixture files.

**Inputs to ask for** (four questions, all with defaults):

| Ask | Writes | Why it must match |
|---|---|---|
| Organisation id | `tenant.json` → `org.id` | **must equal the `org_id` in `sources.json`**, or the catalogue is scoped to an org that does not exist |
| Organisation name | `org.name` | display |
| One department id | `depts[0].id` | **must equal `dept_id` in `sources.json`** — the MCP filters by it |
| Admin email | `users.json` → one org_admin | who signs in |

**Where the files go:** `tenants/<org-id>/` at the repo root, **not**
`demo-data/tenants/`. A real deployment should not be writing into a folder
called demo-data, and `--tenants-root` already supports this.

**`is_demo`:** the demo fixtures set `true`. A custom org sets `false`.

**Consistency check, not a form.** The two ids above must agree with
`sources.json`. The wizard should read the sources file and **default the
prompts from it**, then fail loudly if they diverge — a mismatch here produces
an empty catalogue with no error, which is the failure mode this whole exercise
exists to remove.

**Auth:** mint a super-admin JWT exactly as `seed-demo.sh` does (HS256 over
`JWT_SECRET`, `org_id: citra-ai`, `roles: [super_admin]`, 1 h). Reuse it, do not
reinvent it.

---

## Part 4 — `build_ontology.py`: an agentic, LLM-driven author

**Decided:** the LLM proposes, the user confirms. Not one-shot generation — an
agent that pulls the schema itself, asks clarifying questions, and builds the
file incrementally.

### Model — `deepseek/deepseek-v4-pro`

Reuses the key the wizard already collected, and is already this stack's
`LLM_LARGE_MODEL`, so it is known-good here rather than a new dependency.

```
ONTOLOGY_MODEL=deepseek/deepseek-v4-pro    # new, defaulted, overridable
ONTOLOGY_MAX_TOOL_ROUNDS=20                # runaway-loop guard
ONTOLOGY_MAX_INPUT_TOKENS=200000           # conversation ceiling
ONTOLOGY_MAX_OUTPUT_TOKENS=200000          # per-call max_tokens
```

**Token headroom so a long run cannot truncate mid-way.** Checked against the
model's real limits: context is **1,048,576** and max completion is **393,216**,
so 200k/200k sits comfortably inside both.

- **Input 200k** is a working ceiling, not the model's limit. If the
  conversation grows past it — a wide database with many `describe_tables`
  results — the loop compacts the OLDEST tool results (keeping their table names
  and the running draft) rather than failing. Losing early raw schema is
  survivable; failing at round 15 is not.
- **Output 200k** as per-call `max_tokens` is a ceiling, not a spend: a full
  `sources.json` is a few thousand tokens, and billing is on tokens actually
  produced. It exists so a large registry is never cut off half-written.

Note for whoever implements this: OpenRouter **accepts** a `max_tokens` above the
model's cap and silently clamps it rather than erroring, so an out-of-range value
fails quietly rather than loudly. Set it explicitly and inside the cap.

**Verified live before committing to it** — the whole design rests on a 20-round
tool loop, so guessing was not good enough:

- `finish_reason: tool_calls`, and it called `list_tables` FIRST rather than
  inventing a table name.
- `content` comes back populated alongside `tool_calls`. Worth checking: a
  reasoning model with `reasoning.exclude=true` can return `content: null`,
  which has bitten this codebase before. Do **not** blindly copy
  `LLM_EXTRA_BODY` from the platform config into this loop.

### Cost — measured, not estimated

Full schema + grammar as the system prompt is 55,559 chars / **12,963 tokens**:

| | tokens | cached | cost |
|---|---|---|---|
| call 1 (cold) | 12,963 | 0 | **$0.0083** |
| call 2 (warm) | 12,963 | 12,928 (**99.7%**) | **$0.0008** |

A 20-round run is therefore **~$0.016** — under two cents.

**Caching is automatic.** DeepSeek caches the constant prefix server-side with a
99.7% hit rate on the second call; there is no `cache_control` to set. The
earlier plan's Anthropic cache-breakpoint design is unnecessary — this is simpler
AND cheaper.

Which means the honest justification for the round cap has changed: at under two
cents per run it is **not** primarily a credit guard any more. It is there so a
confused agent cannot loop forever, and so a user is never left watching a
terminal with no end in sight. Keep it, for that reason.

### The five tools

Deliberately few. Every one is executed **locally**; the model only ever sees
their results.

| Tool | Does | Notes |
|---|---|---|
| `list_tables()` | table/collection names only | cheap first look |
| `describe_tables(names[])` | columns, types, PK/FK, enum values, ranges, sample rows | **batched** — one round for many tables, not one per table |
| `ask_user(question, options?)` | puts a question to the human, returns the answer | this is what makes it a conversation instead of a guess |
| `validate_draft(json)` | runs `validate_sources.py` | returns hard problems **and** capability advisories, so the agent can self-correct |
| `save(json)` | writes the file, ends the loop | refuses a draft that has not passed `validate_draft` |

`describe_tables` is backed by the existing `introspect()` dispatch, which
already covers PostgreSQL, MySQL, SQL Server, MongoDB, OData/SAP, Salesforce and
REST. No new connector code.

### The connection string never reaches the model

The user gives it to the **script**, which holds it and executes the queries.
The agent asks for `describe_tables(["claims"])` and receives schema. Credentials
are never in a prompt, never in a tool argument, never in a transcript.

Related: the agent does **not** get to run arbitrary SQL. It was tempting to let
it write its own introspection query, but that is remote code execution against
the user's production database in exchange for nothing — the fixed introspection
tools return strictly more reliable results.

### What the model is told

The system prompt carries the **complete** ontology contract, generated rather
than hand-copied so it cannot drift:

1. `schema/sources.schema.json` — every field, type and enum, straight from
   `gen_sources_schema.py`.
2. `templates/README.md` — the grammar: the four dataset parts (screened /
   verification target / explicit opt-out / API-as-dataset) and the four
   `artifact_role` values and what each one changes.
3. Field-by-field *meaning* — why `decision_history` enables grounding, why
   `value_semantics` drives Money Impact, why a wrong `artifact_role` is worse
   than none.
4. One worked example — a shipped template.

All of it is constant across the run, which is exactly what the provider's
automatic prefix caching rewards — measured at 99.7% on the second call, so the
big contract is effectively charged once with no client-side cache directives.

### Rules the agent runs under

- **Propose, never invent.** Every claim traces to schema evidence or a user
  answer. When unsure, `ask_user` — never guess a role.
- **Ask before writing.** It must clarify decision history, document roles and
  money columns rather than inferring all three silently.
- **Domain last and optional.** Offer the template cells; state plainly that
  skipping costs only locale packs and vertical defaults.
- **Validate before saving**, then show the capability advisories so the user
  sees what is still switched off.
- **Never fabricate a table or column.** Only names returned by the tools.

### Credit protection, concretely

- Hard cap of **20 tool rounds**. On reaching it the run **stops and saves the
  draft so far** with a clear message — it does not silently truncate, and the
  user can resume. Now a runaway-loop guard rather than a cost one.
- Automatic prefix caching does the heavy lifting (99.7% measured).
- Batched `describe_tables`, so a 40-table database is a few rounds, not 40 —
  this matters more for latency and round budget than for money.
- Token usage and actual cost printed at the end, so the number is never a
  mystery.
- `max_tokens` no lower than 4000: reasoning tokens count against the cap but
  are not returned, so a small cap yields empty content.

### The user confirms

`save` writes to a temp file. The wizard then shows a summary — datasets,
declared roles, what the advisories say is off — and asks for an explicit yes
before it becomes `sources.json`. Re-runnable, and `_preserve_authored` keeps
anything hand-edited afterwards.

## Part 4b — Tell the user which databases actually work, in the wizard

A user who picks "my own database" and discovers three screens later that their
driver is missing, or that DuckDB cannot be scanned at all, has hit exactly the
dead-end this whole exercise is about. State it up front, tiered by what it
costs them.

### The four tiers (verified against the code)

| Tier | Databases | Status |
|---|---|---|
| **1. Ready now** | PostgreSQL, MySQL, MongoDB | drivers pinned in `requirements.txt` — the wizard can scan, build and test end to end |
| **2. One install away** | SQL Server, Oracle, BigQuery, Snowflake, Redshift, Databricks, Trino | SQLAlchemy handles them; the package is named in `_DIALECT_PKG`. Declares as `kind: sql` at runtime, so first-class once installed |
| **3. Scannable, expect hand-tuning** | OData/SAP, Salesforce/SOQL, REST/OpenAPI | introspection and runtime connectors both exist; the shapes are less uniform than SQL |
| **4. Runtime only — no scan** | DuckDB, file, GCS | valid `SourceType`/`DatasetKind` with a runtime connector, but **no introspection path**. Hand-author against `docs/sources-file.md` |

Tier 4 is a genuine gap found while checking this, not a design choice: `duckdb`
is an accepted source type with a connector, and `introspect_source.py` cannot
read one.

### How the wizard should say it

Show tier 1 as the immediate answer, name tier 2 with its exact `pip install`,
and be honest that 3 and 4 exist and where they lead:

```
  Databases the wizard can scan right now:
      PostgreSQL, MySQL, MongoDB

  Also supported, needs one install first:
      SQL Server        pip install pyodbc
      Snowflake         pip install snowflake-sqlalchemy
      ...

  Also connectable, but hand-authored rather than scanned:
      DuckDB, files, Google Cloud Storage
      -> source-mcp-template/docs/sources-file.md   (field reference)
      -> source-mcp-template/connectors/            (what each one can do)
```

### Derive it, do not hard-code it

A hand-written list goes stale the first time a connector is added — the same
failure the template menu was designed to avoid. Extend `list_templates.py`, or
add a sibling, that reads:

- `_SQL_KINDS` and `_DIALECT_PKG` from `introspect_source.py` → tiers 1 and 2
- `source-mcp-template/connectors/*.py` → what can be queried at runtime
- `SourceType` / `DatasetKind` from `registry_models.py` → what may be declared

Tier 4 is then computed, not typed: **declarable and queryable, but absent from
the introspection kinds**. Adding a DuckDB introspector would move it up a tier
with no wizard change.

### Fail loud on a missing driver

`introspect_source.py` already catches SQLAlchemy's `NoSuchModuleError` and has
`_DIALECT_PKG` to hand. The agent's `describe_tables` tool must turn that into
*"Snowflake needs `pip install snowflake-sqlalchemy`"* rather than a stack
trace — and the loop should surface it to the user rather than let the model
retry blindly and burn rounds.

### Decision worth taking

Pin `pyodbc` and `oracledb` in the quickstart requirements? SQL Server and
Oracle are the two most likely enterprise sources after Postgres, and a
mid-wizard `pip install` is precisely the improvisation the install test exists
to catch. The cost is a heavier default install; `pyodbc` also needs system ODBC
drivers, which is not a pure pip problem — so this is a real trade-off, not an
obvious yes.

## Part 5 — Order of work

All five are in scope. Ordered so each lands on solid ground.

| # | Item | Size | Depends on |
|---|---|---|---|
| 1 | Drop Runware/Serper from the wizard | XS | — |
| 2 | `seed_org.py` — generic org + dept + admin | S | — |
| 3 | `build_ontology.py` — the agentic author | L | — |
| 4 | Wire the custom path to RUN it all | M | 2, 3 |
| 5 | Verify step on both paths | S | 4 |

**1** is a pure removal. **2** and **3** are independent and testable alone.
**4** is the sequencing from Part 2, which only becomes meaningful once 2 and 3
exist. **5** proves each path finishes — the pattern citra-flows already has and
the other two lack.

### What "done" looks like per item

1. Wizard is 4 steps; `IMAGE_GEN_*`/`SERPER_API_KEY` still work from `.env`.
2. `python scripts/quickstart/seed_org.py --org-id acme --dept ops --admin a@b.c`
   creates them via the user-service admin API and is re-runnable.
3. `python scripts/quickstart/build_ontology.py --kind postgres --conn "..."`
   produces a `sources.json` that passes `validate_sources.py`, having asked the
   user real questions along the way and never exceeded 20 tool rounds.
4. `make wizard` → "my own database" → ends at a signed-in system with a
   non-empty catalogue scoped to the user's org.
5. Demo: `e2e-onboarding-test.sh`. Custom: catalogue non-empty **and** one
   NL→SQL question answered against the user's own data.

## Decisions — resolved

1. ~~Should the LLM propose ontology?~~ **Yes** — `anthropic/claude-opus-5`,
   agentic, user confirms every proposal. Part 4.
2. **Custom path with no database yet** — the wizard should say so and offer the
   demo rather than write an empty registry. A source with no datasets is valid
   and useless, which is the silent-degradation shape all over again.
3. **SOP ingest for custom orgs** — out of scope for v1. It needs a documents
   folder and a dept library; the decision loop works without it, and the
   capability advisory already says grounding is off.

## Risks worth naming

- **Agent quality is the product here.** A confident wrong `artifact_role` is
  worse than none, because screening then runs on the wrong document and looks
  correct. Mitigations: propose-not-invent, `ask_user` when unsure, mandatory
  `validate_draft`, and explicit human confirmation before the file is written.
- ~~Cost~~ — measured away. A 20-round run is ~$0.016. The template +
  hand-edit path remains as a fallback regardless, for anyone who wants no LLM
  in their setup at all.
- **The agent is not the only way in.** Templates, `introspect_source.py` and
  hand-authoring all remain. This adds a guided path; it does not replace the
  deterministic ones.
