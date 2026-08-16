<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Write Actions — how SmartApps write to source systems

> **One-line model:** a write action is a *pre-declared, parameterized DML
> statement* authored once in the MCP's `sources.json`. The SmartApp builder
> never writes DML — it only *references* a registered action by id. The
> dept-MCP translates an LLM/officer JSON payload into the registered write at
> execution time.

This doc is the reference for the write path. It is grounded in code; where it
restates a field or rule, the code wins. Key files:

- `source-mcp-template/models.py` — `WriteAction` (the contract)
- `source-mcp-template/catalogue.py` — `execute_action`, `_validate_action`,
  `_exec_sql_action`, `_exec_mongo_action`, `_exec_rest_action`
- `smart-app-service/models.py` — `McpActionTool`, `Action.data_bindings`,
  `DataBindingWrite` (what the builder authors)

Related: [smart-app-architecture.md](smart-app-architecture.md),
[access-control.md](access-control.md),
[catalogue-retrieval-plan.md](catalogue-retrieval-plan.md).

---

## 1. The `WriteAction` contract

A write action lives in the MCP's `sources.json`, under a dataset's
`write_actions[]` (tabular sources) or the source-level `write_actions[]`
(non-tabular sources). It is loaded at boot via `router.load_sources()` →
`load_sources_from_file()`. It is the **only** place real write semantics
exist.

> The MCP is **file-defined**. The central Mongo `dept_sources` load mode was
> removed (not deprecated) — `config.py` refuses to boot without `SOURCES_FILE`
> or `SOURCES_JSON`, and `load_sources_from_mongo()` no longer exists.

`source-mcp-template/models.py` → `WriteAction`:

| Field | Meaning |
|---|---|
| `id` | Action identifier the app references (e.g. `record_restoration`) |
| `verb` | `create` \| `update` \| `upsert` \| `delete` \| `rpc` |
| `method` | HTTP verb for `rest` / `odata` (POST/PATCH/MERGE) |
| `endpoint` | OData entity URL / REST path / Salesforce sObject name |
| `sql_template` | The parameterized DML, for `sql` kind (SQLAlchemy `:named` params) |
| `input_schema` | JSON Schema of the payload (`required` + `properties`) |
| `idempotency_key_field` | Field used to dedupe a retried write |
| `requires_csrf` | OData S/4 X-CSRF-Token handshake |
| `key_fields` | Filter keys for mongo `update`/`upsert`/`delete` |
| `roles_allowed_write` | Write-authz gate, enforced **on top of** the read PDP |

How each backend kind interprets the fields:

- **sql** — `sql_template` is a parameterized statement.
- **mongodb** — no DML text; `verb` + `key_fields` + `input_schema` *are* the
  contract; the executor builds the pymongo op from them.
- **odata** — `endpoint` is the entity URL; `method` = POST/PATCH/MERGE.
- **rest** — `endpoint` + `method` from the source's OpenAPI map.
- **soql** — `endpoint` is the sObject; `method` = create/update/upsert.

### Example — SQL (the utility deployment `field_operations.outages`)

```json
{
  "id": "record_restoration",
  "verb": "update",
  "sql_template": "UPDATE outages SET end_time = COALESCE(:end_time, end_time), saidi_minutes = COALESCE(:saidi_minutes, saidi_minutes), restoration_crew = COALESCE(:restoration_crew, restoration_crew) WHERE outage_id = :outage_id",
  "input_schema": {
    "type": "object",
    "required": ["outage_id"],
    "properties": {
      "outage_id":        { "type": "string" },
      "end_time":         { "type": "string", "format": "date-time" },
      "saidi_minutes":    { "type": "number" },
      "restoration_crew": { "type": "string" }
    }
  },
  "roles_allowed_write": ["field_officer", "dept_admin"]
}
```

### Example — Mongo (verb-driven, no SQL)

```json
{
  "id": "route_complaint",
  "verb": "update",
  "key_fields": ["complaint_id"],
  "input_schema": {
    "type": "object",
    "required": ["complaint_id", "assigned_to"],
    "properties": {
      "complaint_id": { "type": "string" },
      "assigned_to":  { "type": "string" },
      "status":       { "type": "string", "enum": ["open","routed","closed"] }
    }
  }
}
```

---

## 2. What the SmartApp builder authors — a *reference*, not DML

**The builder never writes SQL or any DML statement.** The app spec has no field
for it. When the builder wires a write into an app, it authors a pointer plus a
copied schema — one of two shapes:

### (a) Agent path — `McpActionTool` (`smart-app-service/models.py`)

```json
{
  "kind": "mcp_action",
  "source_id": "field_operations",
  "dataset_id": "field_operations.outages",
  "action_id": "record_restoration",
  "input_schema": { "...copied VERBATIM from catalogue write_actions[].input_schema..." }
}
```

`input_schema` becomes the LLM's argument contract for that tool. It is a *copy*
of the registered action's schema (from `citra-mcp-discover` output), not an
authored one.

### (b) On-demand path — `Action.data_bindings.writes` (`DataBindingWrite`)

```json
{ "source_id": "field_operations",
  "dataset_id": "field_operations.outages",
  "action_id": "record_restoration" }
```

In both cases the entire contract the builder produces is the triple
**`(source_id, dataset_id, action_id)`** (+ the copied `input_schema` for the
agent tool). The "language" the builder/LLM/officer speaks is **JSON keyed by
the action's `input_schema`** — never DML.

---

## 3. Translation: JSON payload → real DML

At runtime the LLM (agent path) or the officer's Approve (on-demand
plan-then-apply) produces a JSON payload keyed by `input_schema.properties`.
The dept-MCP `/execute_action` turns it into a real write
(`catalogue.py` → `execute_action`):

```
JSON payload  {outage_id, end_time, saidi_minutes}
   │  POST /execute_action {source_id, dataset_id, action_id, payload, dry_run}
   ▼
1. _find_dataset → look up WriteAction by action_id          (404 if not registered)
2. check_write_permission(claims, roles_allowed_write)        (write-authz gate)
3. _validate_action(action, req, kind)                        (required fields;
                                                               mongo: field allow-list + key_fields)
4. if dry_run → return {validated:true}, NO write
5. dispatch by kind:
      sql    → _exec_sql_action      (SQLAlchemy bound params)
      mongo  → _exec_mongo_action    (pymongo op from verb + key_fields)
      rest   → _exec_rest_action     (method + endpoint)
      soql   → soql_connector
      odata  → odata_connector
```

### SQL translation (`_exec_sql_action`)

```python
declared = action.input_schema["properties"]      # every declared field
params = {field: None for field in declared}       # omitted → NULL
params.update(req.payload)                          # caller's values win
with engine.begin() as conn:
    conn.execute(sqla_text(action.sql_template), params)   # SQLAlchemy bound params
```

Three invariants:

- **`:field` placeholders are SQLAlchemy named parameters** — never
  string-interpolated. Every value flows through bound params, so there is no
  SQL-injection surface even though the payload is LLM-produced.
- **Partial payloads are normal.** One action is called across an entity's
  lifecycle (dispatch sets `restoration_crew`; restoration later sets
  `end_time`). Omitted declared fields bind to `NULL`, and templates pair that
  with `COALESCE(:field, field)` so a NULL bind *preserves* the current column
  rather than wiping it.
- **Fail loud (RULE #1):** a `:param` referenced in the template but **not**
  declared in `input_schema` stays unbound → SQLAlchemy raises → a malformed
  action definition fails visibly instead of silently binding NULL.

### Mongo translation (`_exec_mongo_action`)

No template. The executor reads `verb`, filters by `key_fields`, and writes only
fields present in `input_schema.properties` — `_validate_action` rejects any
undeclared field (allow-list), and requires `key_fields` for
`update`/`upsert`/`delete` so a write can never match more documents than
intended.

---

## 4. Creation vs. maintenance — and the schema-crawl boundary

Write actions are **authored, not introspected.** This is deliberate: a write
contract is a governance decision, not a fact you can scrape.

- **Created** in `sources.json`, on the source's `datasets[].write_actions[]`
  (or the source-level `write_actions[]` for non-tabular sources). The source
  owner authors the `WriteAction`: `sql_template` / verb + `input_schema` +
  roles.
- **Maintained** by editing that same block and re-validating with
  `make validate-sources FILE=...`.

**Introspection does not touch write actions.**
`scripts/quickstart/introspect_source.py` re-derives structure — tables,
columns, types, keys — and carries authored blocks (including
`write_actions`) forward on a re-run rather than replacing them. Its
`--propose-writes` phase can *draft* an action from a decision table, but it
ships the draft locked behind a sentinel role that nobody holds, so a human
has to review and unlock it before it can ever fire.

Schema = auto; write actions = governed.

> Known gap: there is no validator asserting a `WriteAction.input_schema` stays
> consistent with the introspected columns. A dropped/renamed upstream column
> re-crawls the read schema but leaves a stale write action pointing at the old
> column until it fails at the DB (fail-at-write, not fail-at-publish). A
> registration/crawl-time drift check would catch it earlier — not built today.

---

## 5. What happens if a write action is **not** declared in `sources.json`

It fails loud at every layer — no fallback path:

| Stage | Behavior when `write_actions` is absent |
|---|---|
| **Build (discovery)** | `citra-mcp-discover` returns `write_actions: []`; the builder has nothing to bind, so it cannot author an `McpActionTool` / `data_bindings.writes`. The app degrades to read-only. |
| **Publish (validation)** | If the spec references an `action_id` not in the catalogue, publish rejects it — `data_bindings` is validated against the discovery-service catalogue at publish time. |
| **Runtime (execute)** | `execute_action` raises **HTTP 404**: "Action … not registered on …. Register it under the source's `datasets[].write_actions` block." No write occurs. |

End to end: **you cannot write through a source until a human has registered it
AND authored its write actions.** The introspection workflow only fills the read
schema; the write contract is always authored, always governed, and its absence
is always a hard, visible failure — never a silent no-op.

---

## 6. Audit, reversal, and access-enforcement boundaries

The platform is deliberately **simple here by design**, not by omission. Two
boundaries are worth stating explicitly so nobody assumes a guarantee that
isn't there.

### Audit is a forward log, not a reversal log

Every write — auto-process *and* officer-approved — is **audited before it
commits, fail-loud**. Auto-process writes a `pending` DecisionRecord first
(`main.py` → `_record_auto_process_pending`) and finalizes it after
(`_finalize_auto_process_decision`); if the audit store is unavailable it
**refuses to commit** ("refusing to commit unaudited", RULE #1). The record
captures *what was written* (full `payload` = the to-values), `action_id`,
`dataset_id`, `source_id`, the actor (`authority="auto_process_policy"` or the
officer), the timestamp, and *why* (`policy_reason` or the approval).

What it does **not** capture is a **before-image of the source row** (the prior
column values). The `{"from", "to"}` delta in the approved path
(`_replay_planned_writes_with_overrides`) is the **officer overriding the LLM's
proposed value** — *not* the row's prior DB state.

This is intentional. `runtime.py`: *"the platform doesn't second-guess writes.
Audit, reversal, and the chat-write block remain in force regardless."*
**Reversal is a property of audit + the source system, not an engine feature:**

- The Citra audit tells IT **precisely what to reverse** (which action, full
  payload, on which record).
- The **prior value** comes from the SoR's own change history (temporal tables /
  CDC / transaction log / backup — which enterprise SoRs have) or is
  self-evident for state transitions (a `routed`→`closed` status reverses to
  `routed`; the `COALESCE(:f, f)` pattern preserves untouched columns).

We deliberately do **not** build automated inverse/compensation actions or a
read-before-write snapshot — both would re-implement capability the SoR already
owns, for the one narrow case it doesn't (a history-less SoR + an overwrite of a
free-form value). If that case ever becomes real for a customer, a
read-before-write row snapshot into the audit record is the *minimal* answer —
strictly smaller than inverse actions — but it is **not built and not needed by
default**.

### Read access: dataset scope + PII are hard-enforced; non-PII columns are not

Three things are enforced server-side, not advisory:

- **Dataset scope** — `bindings.reads` produces an `enum` of allowed
  `dataset_id`s, so the agent **cannot reference** a dataset it isn't bound to
  (`data_tools.py`).
- **PII redaction** — `_redact_rows(rows, pii_cols)` masks PII columns in
  returned rows when `redact_pii=True` (`data_tools.py`).
- **Write field allow-list** — mongo/SQL executors reject any payload field not
  declared in `input_schema.properties`.

> **Footgun — `DataBindingRead.columns` is NOT a hard access gate.** It is a
> *projection default* ("Optional projection; defaults to binding columns"), not
> a filter that drops undeclared columns from returned rows. So a **non-PII**
> column inside an *allowed* dataset is **not** access-restricted by listing
> other columns in `columns`. Do not assume a sensitive-but-non-PII column is
> protected just because it's omitted from a binding. To actually restrict such
> a column, either flag it for **PII redaction** or **split the dataset** so the
> sensitive columns live in a separately-bound dataset. (We intentionally do not
> carry a sensitivity/classification taxonomy beyond PII — "sensitive" is
> customer- and jurisdiction-specific; access is expressed via roles, dataset
> scope, and the PII flag.)

---

## 7. Onboarding friction & a possible enhancement

Because write actions are authored, onboarding a *writable* source has a manual
step: someone hand-writes the `sql_template` / verb + `input_schema` in
`sources.json`. Introspection gives columns for free but **not** the write
contract. The builder is only ever as capable as the write actions an admin has
pre-declared.

`introspect_source.py --propose-writes` now does exactly this: it drafts
candidate write actions from a decision table and writes them **locked** —
`roles_allowed_write: ["__locked_pending_it_review__"]`, a sentinel role nobody
holds — so a proposed write can never fire until a human reviews it and swaps
in the real roles. That is the governance gate; the draft is a starting point,
not an approval.
