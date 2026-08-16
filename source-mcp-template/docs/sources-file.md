# `sources.json` (SOURCES_FILE) — Authoring Guide

The dept-MCP reads **one local JSON file** — the `SOURCES_FILE` — to learn which backends
it serves, how to read them, and which writes it may perform. This file replaced the
retired central `dept_sources` Mongo collection: the MCP now runs entirely inside the
customer's estate with **no Citra database dependency**.

This guide explains every field, how the pieces fit together, and how to compose a file
from scratch, with worked examples.

---

## 1. What the file is and where it goes

- **Env var:** `SOURCES_FILE=/app/sources.json` (required unless `SOURCES_JSON` is set — the MCP refuses to start without a registry, `config.py`).
- **File-free hosts:** on a pure env-var runtime (Cloud Run, Container Apps, Fargate, a k8s ConfigMap env) where you'd rather not mount or bake in a file, set **`SOURCES_JSON`** to this file's JSON payload *inline* as an env var instead. Same shape, same filtering; `SOURCES_FILE` wins if both are set (`router.load_sources_from_file`). The registry is non-secret, so it's safe in a ConfigMap.
- **Shape:** a JSON **array** of source objects, or `{"sources": [ ... ]}`. Both are accepted (`router.py`).
- **Fails loud:** a missing / unreadable / malformed file raises at startup — the MCP never boots on a silently-empty registry (`router.py:166-168` unreadable JSON, `176-177` bad `SOURCES_JSON`, `179-182` not a list).
- **Schema-validated:** every source the MCP is about to serve is validated against `registry_models.py`, and an **invalid registry refuses the boot** — see §15. This is new: the file used to be read as raw JSON, so a typo'd field name was simply never read and did nothing, silently. Validate at your desk first: `python validate_sources.py sources.json`.
- **Filtered on load:** only sources whose `org_id` matches the MCP's `ORG_ID` **and** whose `dept_id` is in the MCP's `DEPT_IDS`, with `is_active != false`, are loaded (`router.py:90-106`). Everything else is ignored.
- **Reload:** loaded **once at boot** by default. Set `SOURCES_REFRESH_SECONDS > 0` to hot-reload on a cadence; otherwise **restart the MCP** after editing (the deliberate change-management act).

### Where the fields end up (the pipeline)

```
sources.json
   │  (MCP loads + serves)
   ├─► GET /datasets, GET /datasets/{id}, POST /run_query, POST /execute_action
   │
   ├─► discovery-service  ── registration (source metadata + rag_collection; NOT actions)
   │        └─► main chat / routing pick the source
   │
   └─► data-discovery-service crawls GET /datasets/{id}
            └─► builds `data_catalogue` (columns, input_schema, write_actions[])
                     └─► smart-app BUILDER reads it (/builder/catalogue?full=true)
                              └─► runtime executes reads/writes back through the MCP
```

Two consequences worth remembering:
- **Write actions reach the builder only through the catalogue**, i.e. only if you declare them here.
- **Semantic (RAG) sources are never queried by the MCP** — they're advertised to discovery and answered by the platform reader (the "RAG short-circuit").

---

## 2. Top-level source fields

Each object in the array is one **source** (one backend system for one dept).

| Field | Req | Type | Meaning |
|---|---|---|---|
| `source_id` | ✅ | string | Unique id. Dataset ids are `"<source_id>.<table>"`. |
| `type` | ✅* | string | `structured` \| `mongodb` \| `rest_api` \| `bigquery` \| `sap_rfc` \| `duckdb` \| `semantic` (`catalogue._source_kind:104-116`). **Defaults to `semantic` if omitted** — a structured source that forgets `type` is silently dropped from `/query` (`router.py:119-138`). **Always set it.** |
| `dept_id` | ✅ | string | Must be one of the MCP's `DEPT_IDS`, else filtered out. |
| `org_id` | ✅ | string | Must equal the MCP's `ORG_ID`, else filtered out. |
| `name` | ✅ | string | Human/LLM-facing display name — drives routing + the catalogue card. |
| `description` | ✅ | string | Rich description — the single biggest signal for source selection. Be specific about what's inside and what apps use it. |
| `is_active` | | bool | `false` excludes the source. Default `true`. |
| `tags` | | string[] | Keywords that boost discovery ranking. |
| `visibility` | | object | Who may read it — see §3. |
| `connection` | ✅ (structured/mongodb) | object | How to reach the backend — see §4. Credentials via `env_prefix`, never inline. |
| `options` | | object | `{"tables": [...]}` to pin which tables auto-discovery exposes. For `type: "rest_api"`, also `{"invocation_template": "…"}` — prose appended to the routing description as an "Invocation hint" so the routing LLM knows how to phrase upstream calls (`registration.py:171-174`). |
| `datasets` | | object[] | Explicit dataset blocks — see §5. Required for **writes** and precise control. |
| `write_actions` | | object[] | Source-level actions (only used by the non-tabular single-dataset fallback — see §5). Normally put actions **inside a dataset**. |
| `columns` | | object[] | Source-level columns (non-tabular fallback only). |
| `rag` | ✅ (semantic) | object | `{"milvus_collection": "...", "s3_prefix": "..."}` — see §8. |
| `taxonomy` | | object | Doc-type vocabulary for semantic sources — see §8. |
| `query_timeout_seconds` | | int | Per-source read timeout override. |
| `supports_history` | | bool | Capability flag published to discovery (`registration.py:137`, default `false`). Tells the app builder this source can back a **"Refresh from History"** workflow — i.e. it can serve a bulk historical pull for few-shot grounding. Declares a capability; it does **not** make a dataset a decision record (that's `decision_history`, §9). |
| `domain` | | object | The **deployment-targeting triple** — see §2.1. Every dataset inherits it; a dataset may declare its own complete block to override. |
| `workflow_id` | | string | Informational link to the ingestion workflow, if any. |

\* `type` is now **schema-enforced and required** (`registry_models.RegistrySource`) — the MCP
refuses to boot without it (§15). Historically it wasn't: omitting it defaulted to `semantic` and
silently dropped a structured source from `/query`, which is precisely why it is required now.

**Reserved / not consumed.** `structured_detection` is accepted and carried through the loader
(`router.py:75`) but **has no consumer anywhere** — it does not influence NL→SQL. Don't author it
expecting an effect.

### 2.1 `domain` — vertical / sub-vertical / country (deployment targeting)

Declares WHAT industry line this source serves and WHERE, so downstream behavior
packs configure themselves — locale-correct ID validators and date parsing for
fraud checks, vertical defaults, and the admin domain badge. It **selects from
built-in packs; it never forks behavior**, and it never turns fraud screening on
by itself (`fraud_screening` stays the only on-switch).

```json
"domain": {
  "vertical": "utility",
  "sub_vertical": "power_recovery",
  "country": "IN",
  "region": "BR",
  "notes": "Bihar DISCOM arrears recovery"
}
```

| Field | Req | Values | Meaning |
|---|---|---|---|
| `vertical` | ✅ | `insurance` \| `banking` \| `utility` \| `field_service` | The industry. **Closed enum** — a typo is rejected at publish; extending a market is a schema change, never a loosened string. |
| `sub_vertical` | ✅ | insurance: `claims`, `underwriting` · banking: `loan_origination`, `loan_recovery` · utility: `power_recovery`, `metering_inspection` · field_service: `equipment_inspection` | The line of business. Must belong to its `vertical` (validated). |
| `country` | ✅ | `IN` \| `US` (ISO-3166 alpha-2) | Selects the locale pack: which ID checksum validators run (PAN/Aadhaar/GSTIN/IFSC vs SSN/EIN/ABA-routing/ZIP) and how ambiguous dates parse (03/04 = 3 Apr in IN, Mar 4 in US). **The ontology wins over the deployment's `FRAUD_LOCALE` env**; sources with no `domain` keep the env fallback. |
| `region` | | free string | Optional state/province code. Advisory. |
| `currency` / `date_order` | | e.g. `INR` / `DMY` | **Derived from `country` when omitted** (IN → INR/DMY, US → USD/MDY) — the served catalogue value is always filled. An explicit contradicting value is allowed (deliberate override). |
| `notes` | | free string | Shown in the catalogue. |

Placement: on the **source** (every dataset inherits) or on a **dataset** as a
complete override block (`vertical`+`sub_vertical`+`country` are required, so a
partial merge is impossible by design — restate the whole triple). One MCP
serving two lines of business is the only reason to override per dataset.

### 2.2 `organization` — the customer's display identity

Declares WHO the customer is, for presentation: every app built on this source
inherits the company name/logo — app headers ("Acme Power · Recovery
Tracker"), browser titles, agent prompts ("Acme Power's recovery assistant"),
and the Money-impact card. Declared **once by IT at connection time** so no BA
ever types the company name into a spec; the builder may still override the
resulting theme per app.

```json
"organization": {
  "name": "Acme Power & Utilities Co.",
  "short_name": "Acme Power",
  "logo_url": "https://…/acme-logo.svg",
  "brand_color": "#0f6b3f"
}
```

| Field | Req | Meaning |
|---|---|---|
| `name` | ✅ | Full display name. |
| `short_name` | | Compact form for headers/nav. Defaults to `name`. |
| `logo_url` | | Logo image; becomes the app theme's default `logo_url`. |
| `brand_color` | | `#rrggbb` seed for the app theme's `primary` color. |

**Presentation only** — auth/tenancy (`org_id`) is completely separate and
untouched. Source-level, no per-dataset override (a company doesn't change per
table); it rides every dataset's catalogue entry. If a tenant's sources
declare conflicting blocks, consumers take the first connected source's block
(deterministic, and visible in the catalogue).

---

## 3. `visibility` — who may read the source

```json
"visibility": {
  "roles_allowed": ["user", "dept_admin", "org_admin", "super_admin"],
  "cross_org_ids": [],
  "public_within_org": false
}
```

- `roles_allowed` — roles that can query/read this source (checked against the caller's JWT).
- `cross_org_ids` — other orgs allowed to see it (rare; usually `[]`).
- `public_within_org` — `true` = any member of the org can read it (used for org-wide corpora like a policy library). For structured PII sources keep it `false`.

Write authorization is **separate and stricter** — see `roles_allowed_write` on each action (§7).

---

## 4. `connection` — reaching the backend (credentials via env)

Secrets are **never** written in `sources.json`. You declare a `type` + an `env_prefix`, and
the MCP reads the actual credentials from its environment (docker-compose `environment:` /
Vault bag).

**SQL (postgres / mysql / sqlserver / …):**
```json
"connection": { "type": "postgres", "env_prefix": "ACME_POWER_SQL" }
```
The SQL connector then reads (`connectors/sql_connector.py:45-52`):
`ACME_POWER_SQL_HOST`, `ACME_POWER_SQL_PORT`, `ACME_POWER_SQL_DB`, `ACME_POWER_SQL_USER`, `ACME_POWER_SQL_PASS`.

**MongoDB:**
```json
"connection": {
  "type": "mongodb",
  "env_prefix": "DEMO_MONGO",
  "mongo_db": "acme",
  "collection": "claims"          // ← REQUIRED. Without it every read errors.
}
```
Reads `DEMO_MONGO_URI` (and `DEMO_MONGO_DB`, or the inline `mongo_db`) — `_mongo_data_conn`,
`catalogue.py:506-542` (uri: 528, db_name: 536).
A `mongodb` source with **no** `env_prefix`/`uri` **fails loud** (no shared-DB fallback).

> **`collection` is not optional.** Omit it and `run_query` returns
> `"mongodb source has no connection.collection"` on **every** read (`catalogue.py:1039-1043`),
> and introspection silently returns **zero columns** (`_introspect_mongo`, `catalogue.py:556-558`)
> — so the dataset looks schema-less in the catalogue rather than broken. Declare it.

**BigQuery / others:** same pattern — `env_prefix` → `{PFX}_*` env vars (see the per-connector docstring).

### `connection` field reference

| Field | Applies to | Meaning |
|---|---|---|
| `type` | all | Backend driver: `postgres` \| `mysql` \| `sqlserver` \| `mongodb` \| `odata` \| `salesforce` \| … Also selects the dataset kind for `type: "structured"` sources (`catalogue.py:95-103`). |
| `env_prefix` | all | Credential prefix — the MCP reads `{PFX}_*` from its environment. Never inline secrets. |
| `mongo_db` | mongodb | DB name; else `{PFX}_DB`. One of the two is required. |
| `collection` | mongodb | **Required.** The collection this source reads/writes. |
| `uri` | mongodb | Explicit connection URI. **Discouraged** — it puts credentials inside the registry. Prefer `env_prefix` → `{PFX}_URI`. |
| `primary_key` | mongodb | String array — field names introspection marks as primary keys (`catalogue.py:577`). |
| `tenant_filter` | mongodb | **Security-relevant** — see below. |
| `base_url`, `auth`, `headers`, `timeout_seconds` | rest_api | REST target + auth (`connection.auth = {type, env_prefix, header_name?}`; `bearer` → `{PFX}_TOKEN`/`{PFX}_API_KEY`, `api_key` → `{PFX}_API_KEY`, `basic` → `{PFX}_USER`/`{PFX}_PASSWORD`). See §5.1. |

### `connection.tenant_filter` — the write partition fence

```json
"connection": { "type": "mongodb", "env_prefix": "DEMO_MONGO",
                "mongo_db": "acme", "collection": "claims",
                "tenant_filter": { "org_id": "acme-power" } }
```

A dict of fixed field/value pairs that scopes this source to one tenant partition. It is
**enforced on the write path**, not merely advisory:

- **ANDed into every `update` / `delete` filter** and **merged into every inserted document**
  (`catalogue.py:1533`, `1544`) — so **a write can never escape the source's tenant partition**,
  regardless of what the caller's payload says.
- **Keys the idempotency index** — the partial-unique index is compound over `_idempotency_key`
  **plus the tenant fields** (`catalogue.py:1471-1482`), so an idempotency key is unique *per
  tenant* and concurrent duplicates can't double-insert across partitions.

Mongo write actions only. Omit it for a source that owns its whole collection; declare it whenever
one collection is shared across tenants.

> Rule of thumb: `sources.json` is **safe to commit**; the `{PFX}_*` secrets live in the MCP's `.env` (dev) or Vault bag (prod).

---

## 5. Datasets — declared vs. auto-discovered

A source exposes one or more **datasets** (a table / view / object / corpus). Resolution
order (`catalogue._datasets_for`, `catalogue.py:119-176`):

1. **Explicit `datasets[]`** (top-level) — you spell out each dataset. **Required for write
   actions and for precise column/schema control.** Wins over everything.
2. `catalogue.datasets[]` — legacy nested location (back-compat).
3. **Tabular auto-discovery** (`sql` / `soql` / `odata` only): if no `datasets[]`, the MCP
   uses `options.tables[]`, or live-introspects the DB. **Auto-discovered datasets are
   READ-ONLY — they carry no `write_actions`.**
4. **Single-dataset fallback** (mongodb / semantic / rest with no `datasets[]`): the source
   itself becomes one dataset, carrying source-level `columns[]` + `write_actions[]`.

**Why two modes?** Auto-discovery is for **low-friction read onboarding** (point at a 50-table
DB, get reads for free). **Writes are deliberately opt-in** — an update/delete needs a curated
verb, key, authz, and payload contract that can't be safely inferred, so you must declare it.

### Dataset block fields

```json
{
  "id": "billing.consumers",          // required — "<source_id>.<table>"
  "physical_name": "consumers",       // real table/object name in the DB
  "name": "Consumer master",          // display name
  "kind": "sql",                      // sql|odata|soql|rest|mongodb|duckdb|bigquery|sap_rfc
  "description": "…",                 // what's in it (LLM-facing)
  "columns": [ /* see §6 */ ],
  "input_schema": { /* READ parameter contract — see below */ },
  "read_via": { "kind": "sql", "target": "consumers" },  // optional; derived from kind/table if omitted
  "write_actions": [ /* see §7 */ ],
  "decision_history": { /* see §9 */ },
  "fraud_screening": { /* see §10 */ },
  "mandatory_when_used": true          // optional — a policy-required check (see §11)
}
```

| Field | Req | Meaning |
|---|---|---|
| `id` | ✅ | `"<source_id>.<table>"` — globally unique. |
| `physical_name` | | Real table/object name in the DB (defaults from `read_via.target`/`name`/`id`). |
| `name` | | Display name. |
| `kind` | | `sql`\|`odata`\|`soql`\|`rest`\|`mongodb`\|`duckdb`\|`bigquery`\|`sap_rfc`. |
| `description` | | What's in it (LLM-facing). |
| `columns` | | The schema — see §6. |
| `input_schema` | ✅ (rest) | **The READ parameter contract** — see below. |
| `read_via` | | How `run_query` interprets the query: `{kind, target, extra}` — see below. |
| `write_actions` | | Governed writes — see §7. |
| `samples_redacted` | | Default `true` — sample rows are PII-masked. |
| `decision_history` | | Grounding contract — see §9. |
| `fraud_screening` | | Fraud opt-in — see §10. |
| `mandatory_when_used` | | Policy-required check — see §11. |

### `input_schema` — the READ parameter contract

The **read-side mirror of `write_actions[].input_schema`** (`models.py:298-302`): a JSON Schema of
the params a caller must supply to invoke this read. **Empty for a plain table read** (you query a
table, you don't parameterise it); **required for REST / parameterised sources**, where there is no
table to scan — the read *is* a call.

```json
"input_schema": {
  "type": "object",
  "required": ["pan"],
  "properties": { "pan": { "type": "string", "description": "PAN to look up." } }
}
```

It rides the catalogue verbatim (`catalogue.py:451`), the builder wires a form/filter to it, and the
MCP **validates it before firing** — a missing or blank required param fails loud
(`"missing required parameter 'pan'"`) rather than issuing a malformed upstream request
(`catalogue.py:985-987` → `rest_connector._validate_params`).

### 5.1 `read_via` — and the REST read contract

`read_via = {kind, target, extra}`. For plain SQL tables it's derived from `kind` + the table name.
Declare it for REST / OData / parameterised reads.

For **`kind: "rest"`**, `read_via.extra` carries the **request/response mapping** — this is what
turns caller params into an HTTP call and the JSON reply into typed rows. It is **load-bearing: a
REST dataset with no request mapping fails loud** ("no REST request mapping — dataset
`read_via.extra.request` is missing") rather than firing a bare GET.

```json
"read_via": {
  "kind": "rest",
  "extra": {
    "request": {
      "method": "GET",
      "path":   "/v2/credit/{{pan}}",        // {{param}} placeholders ← input_schema
      "query":  { "loanId": "{{loan_id}}" }, // unresolved optionals are dropped, never leaked
      "body":   null                          // for POST/PUT
    },
    "response": {
      "path":     "data",                     // dotted path to the object/list in the body
      "row_mode": "object",                   // "object" → 1 row | "list" → N rows
      "columns":  { "credit_score": "score", "status": "status" }  // {column: dotted-field-path}
    }
  }
}
```

| Key | Meaning |
|---|---|
| `request.method` | HTTP verb (default `GET`). |
| `request.path` | Appended to `connection.base_url`. `{{param}}` values are **percent-encoded**, so a value's `/ ? #` can't alter request structure. |
| `request.query` | Query params; httpx encodes them. A placeholder left unresolved is **dropped**, not sent literally. |
| `request.body` | JSON body, `{{param}}`-interpolated. |
| `response.path` | Dotted path to the payload (`""` = the whole body). A **wrong path fails loud**; a path resolving to `null` is a valid **empty** result. |
| `response.row_mode` | `object` (one row) or `list` (N rows, capped at the row limit). |
| `response.columns` | `{column: dotted-field-path}` projection. Omit to pass a dict row through unchanged. A field the API omits becomes `null`. |

Interpolation is **single-pass** — a substituted value is never re-scanned, so there is no
second-order injection. The connector also applies an **SSRF guard** (default-deny private /
loopback / metadata hosts) and does **not follow redirects**. The mapping may also be authored at
`read_via` top level; `read_via.extra` is the passthrough that survives to the catalogue/builder,
so prefer it (`catalogue.py:982-988`, `connectors/rest_connector.py`).

---

## 6. `columns` — the schema the agent reads

```json
{
  "name": "consumer_id",              // semantic/display name (defaults to physical_name)
  "physical_name": "consumer_id",     // the real DB column
  "type": "string",                   // native type as reported by the source
  "description": "10-digit account number — primary key.",
  "is_primary_key": true,
  "is_foreign_key": false,
  "foreign_ref": "bills.consumer_id", // "<table>.<column>" (physical names)
  "semantic_type": "account_id",      // inferred meaning (email/pan/amount/dob/…)
  "column_kind": "plain",             // plain | url | image_url | document_url | file (advisory)
  "mime_hint": "application/pdf",     // content-type fallback for media columns
  "nullable": true,
  "pii": false,                       // ENFORCED — usually set by the data-discovery classifier
  "sensitivity": "internal",          // advisory only — public | internal | confidential | restricted
  "distinct_values": ["A+", "A", "B", "C", "D"],   // for low-cardinality enums
  "range": { "min": "0", "max": "999999" }
}
```

- `column_kind` of `image_url` / `document_url` / `file` is an **advisory hint the builder LLM
  reads** — it tells the builder "this column references an image/doc, not plain text" so it wires
  media handling (and the per-image `image_analyze` / `doc_extract` tools) to the right columns.
  It is **not** a runtime switch: the runtime resolves media off the builder-authored
  `url_column` / `url_columns`, and `/resolve_media` validates only that the column is **declared
  on the dataset** — never its `column_kind` (`catalogue.py:1143-1145`). So a wrong `column_kind`
  misleads the builder; it does not block or enable a resolve.
- `mime_hint` — the **content-type fallback** on `/resolve_media`. The **file extension wins**;
  `mime_hint` is used only when the ref's extension is unrecognised (or absent). Either way it's
  a hint for the consumer's decoder — the stored object's own `Content-Type` wins at fetch time.
- `pii` is the **one enforced classification flag**: it redacts sample rows (`catalogue.py:798`)
  and is surfaced to the NL→SQL planner as a `[PII]` marker on the column
  (`planners/nl_to_sql.py:60`).
- `sensitivity` is **advisory / not enforced.** It is carried verbatim into the catalogue, but **no
  consumer anywhere in the platform reads it** — nothing gates, redacts, or filters on it. The
  platform deliberately carries **no classification taxonomy beyond PII** (see
  `docs/write-actions.md`); if a column needs enforcement, mark it `pii`.
- `distinct_values` on an enum column is gold for the builder (it can build a real filter
  dropdown instead of guessing).

---

## 7. `write_actions` — governed inserts / updates (the execute contract)

This is the **only** way an app can write back to a system of record. Each action is a
curated, authorized capability with an explicit parameter schema. Put it **inside the dataset**
it writes.

```json
"write_actions": [
  {
    "id": "acknowledge_tamper",          // = action_id callers invoke
    "verb": "update",                    // create | update | upsert | delete | rpc
    "description": "Mark a tamper event acknowledged and attach a note.",
    "sql_template": "UPDATE tamper_events SET acknowledged=true, notes=:notes WHERE event_id=:event_id",
    "key_fields": ["event_id"],          // fields that identify the target row (update/upsert/delete)
    "roles_allowed_write": ["dept_admin", "org_admin", "super_admin"],
    "input_schema": {                    // ← THE PARAMETER CONTRACT (JSON Schema)
      "type": "object",
      "required": ["event_id"],
      "properties": {
        "event_id": { "type": "string", "description": "Target tamper event." },
        "notes":    { "type": "string", "description": "Note attached to the ack." }
      }
    }
  }
]
```

Field notes:
- **`input_schema`** is the heart of it — a JSON Schema (`type`/`required`/`properties`). It is
  carried verbatim into `data_catalogue`, copied by the builder into the app's action tool, used
  as the LLM's argument contract, and validated by the MCP on execute. **If you want an app to
  offer this write, this schema is what makes its parameters known.**
- Per **kind** the write target differs:
  - `sql` → **`sql_template`** with `:named` params (params must be declared in `input_schema.properties`; omitted ones bind `NULL`).
  - `rest` / `odata` → **`endpoint`** + **`method`** (POST/PATCH/…).
  - `soql` → **`endpoint`** = the sObject name; `method` = create/update/upsert.
- `roles_allowed_write` — write-only authz gate, checked **on top of** read visibility. Empty = platform default (dept_admin+).
- `idempotency_key_field`, `requires_csrf` (OData/S4), `key_fields` (mongo/DML target filter) — optional per backend.

At runtime the app posts `{source_id, dataset_id, action_id, payload, dry_run}` to the MCP's
`POST /execute_action`; the MCP checks write authz, validates `payload` against `input_schema.required`,
then binds it into the `sql_template`/endpoint.

> The acme `billing` source has **no** `write_actions` — it's read-only by design. That's why
> the builder reports "no cataloged write actions" for billing. To make it writable, add a
> `write_actions[]` block (e.g. `update_consumer_status`) to the `billing.consumers` dataset.

---

## 8. Semantic (RAG) sources

A `type: "semantic"` source is a document corpus. **The MCP does not query it** — it advertises
it to discovery, and the Citra-Service platform reader answers RAG. So it needs almost no
structured config, just where its vectors live:

```json
{
  "source_id": "acme_power_policy_library",
  "dept_id": "central_pmu",
  "org_id": "acme-power",
  "type": "semantic",
  "name": "Central PMU — Acme Power Policy Library",
  "rag": {
    "milvus_collection": "mcp_dept_libraries",              // the Milvus collection to read
    "s3_prefix": "power-distribution/acme-power/policy/"    // where originals live (Open button)
  },
  "visibility": { "roles_allowed": ["user","dept_admin","org_admin","super_admin"],
                  "public_within_org": true },
  "taxonomy": {
    "doc_types": [
      { "id": "sop",          "label": "SOP",          "synonyms": ["procedure"], "examples": ["Theft Inspection SOP"] },
      { "id": "tariff_order", "label": "Tariff Order" },
      { "id": "circular",     "label": "Circular" }
    ],
    "classification_levels": ["public", "internal", "confidential"]
  }
}
```

- `rag.milvus_collection` — the shared dept-library collection (`mcp_dept_libraries`) or a
  per-source collection. This is the authoritative name the reader uses (it's what
  registration publishes to discovery).
- `taxonomy.doc_types` — the vocabulary the routing LLM filters on (`doc_types` on `/query`).
  Each entry: `{id, label?, synonyms?, examples?}`.

---

## 9. `decision_history` — optional, for self-improving apps

If a dataset is an **append-only record of closed decisions** (each row = the input a team saw
+ the outcome they reached), declare it so the builder can ground an app in past judgments and
the platform can observe outcomes:

```json
"decision_history": {
  "is_decision_record": true,                // the gate — makes this dataset grounding-usable
  "decision_column": "final_status",         // the decision/outcome column
  "timestamp_column": "closed_at",           // used for date-range history pulls
  "terminal_states": ["approved", "rejected"], // values that mean "closed / decided"
  "reasoning_column": "reviewer_notes",      // reviewer/adjuster notes, if any

  // ── outcome observation (the self-improving loop reads the record back later) ──
  "outcome_field": "final_status",           // column to judge on later
  "good_values": ["approved"],               // values meaning the decision WORKED
  "bad_values": ["reversed", "reopened"],    // values meaning it FAILED / was reversed
  "neutral_values": ["escalated"],           // settled but NO quality signal — stops polling,
                                             //   write-back ignores it (distinct from good/bad)
  "outcome_hold_field": "assigned_to",       // a field the decision WROTE; if its value later
                                             //   changes, the decision was overturned → bad
  "key_field": "case_id",                    // record key for the read-back (defaults to the dataset id field)
  "settling_window_days": 7,                 // days to wait before judging (default ~7)
  "declared": true                           // provenance — see below (default true)
}
```
A live transactional table is **not** a decision record; an adjudicated/closed-case log is.

**`declared`** (`models.py:235`) marks the block's **provenance**: `true` (the default) = authored
here in `sources.json`, i.e. authoritative. `false` = *inferred* — reserved for a future
data-discovery enricher pass that may **propose** candidate decision-record datasets. Hand-authored
blocks leave it alone.

**What each side drives.** This one block powers BOTH learning surfaces: the fields above
`outcome_field` drive the **few-shot historical refresh** (grounding on past judgments); the
`outcome_field`/`good_values`/`bad_values`/`neutral_values`/`outcome_hold_field`/`settling_window_days`
group drives the **outcome poll** (reading the record back to label the decision good/bad).

**Note — `input_fields` are NOT declared here.** The grounding contract needs to know which columns
are the *case input* (the decision drivers the agent reasons over), but you do **not** mark those in
`decision_history`. They are chosen **at app-build time, by the builder**: the eligible candidates
are every column EXCEPT the `decision_column`, `timestamp_column`, `reasoning_column`, and the
primary key(s), and the builder **selects the actual drivers** from those candidates (it may pick a
subset — not necessarily all). The build hard-fails if there are **zero** eligible candidate
columns ("nothing for the model to reason from"). There is currently no per-column flag in
`sources.json` to force-include or exclude a grounding input — so keep the dataset's non-decision
columns meaningful and free of noise (IDs, UI metadata), since whatever the builder picks defines
the few-shot similarity match. Full grounding-contract reference: `docs/smart-app-grounding.md`.

---

## 9.1 `value_semantics` — the MONEY definition (ROI spine)

`decision_history` defines what an OUTCOME is; `value_semantics` defines what
that outcome is **worth**. It is the canonical spine every "money saved /
recovered" number is computed from — the platform's Money-impact card, the
`/value-stats` endpoints, and every builder-generated ROI/KPI page all read
values stamped by THIS definition, so the same question can never produce two
different numbers. Because it lives in `sources.json`, a pilot's metric
definitions are **frozen by a git commit at day zero**, and every stamped value
carries the hash of the block that computed it (`definition_version`) — a
definition change mid-pilot is visible, never a silent re-cut of history.

A sibling of `decision_history` on the dataset block:

```json
"decision_history": { "...": "outcome mapping (§9)" },
"value_semantics": {
  "value_kind": "recovered",
  "exposure_field": "outstanding_amount",
  "realization": {
    "dataset": "billing.payments",
    "match_field": "consumer_id",
    "amount_field": "amount",
    "date_field": "payment_date",
    "window_days": 90
  },
  "attribution": "approved_recommendation",
  "prevented_when": ["rejected", "denied"]
}
```

| Field | Req | Meaning |
|---|---|---|
| `value_kind` | ✅ | **Closed enum**: `recovered` (money actually collected post-decision — collections, arrears), `prevented_loss` (the exposure of cases decided BAD — denied fraudulent claims, refused bad sanctions), `sanctioned` / `settled` (throughput value). Each kind enforces its own required fields at publish. |
| `exposure_field` | ✅ for `prevented_loss` | The record's OWN amount column (claim amount / outstanding / sanction amount). For `prevented_loss` the exposure **frozen at decision time** IS the value — never re-read later (today's balance is not what was at stake). Must be a declared column (validated at publish). |
| `realization` | ✅ for `recovered`/`settled` | WHERE money actually lands — usually a **different dataset** (the payments ledger). `dataset` + `match_field` (realization rows join the case by this) + `amount_field`, optional `date_field` + `window_days` (default 90): value counts only when realized within the window after the decision. Cross-dataset like `payment_proof` — the dataset and columns are validated against the catalogue when consumed. The realization dataset must be a **structured** kind (sql/odata/soql/mongodb), never `rest`. |
| `attribution` | | **THE rule people fight about later — agree it up front.** Closed enum: `approved_recommendation` (default — value counts only when the officer approved the AI's call), `any_citra_touched` (any committed decision on the case), `approved_within_window` (approved AND realized inside `window_days`). There is no objectively right answer; there is a pre-agreed one, versioned in git. |
| `prevented_when` | ✅ for `prevented_loss` | Which committed decision outcomes count as prevention (matched against the `decision_history` outcome values). |

**How it flows** (same one-file contract as everything else): you author it
here → it rides the catalogue → the app publish resolves and validates it
(realization dataset/columns must exist) → the **outcome poller stamps
`outcome.value`** on each decision record (realized sum from the realization
dataset, or the frozen exposure for prevented_loss) → `/value-stats` and the
Money-impact card aggregate those stamps → builder ROI pages bind to the
`decision_ledger` platform dataset whose rows already carry the canonical
amounts. Builder pages **never recompute "recovered" from raw payments** —
they visualize the spine. Currency derives from the `domain` triple (§2.1).

Per-vertical presets (see `templates/`): collections/arrears → `recovered`
with a payments-ledger realization; insurance claims → `prevented_loss` on
denied fraudulent claims; loan origination → `prevented_loss` (bad sanction
avoided) and/or `sanctioned` throughput.

**The block is OPTIONAL — declare it only when the dataset genuinely carries
money.** A compliance/quality vertical with no monetary column (e.g. the
`field_service-equipment_inspection-US` template: pass/fail inspections, no
amounts) has **no** `value_semantics` — its ROI story is told in decisions and
outcomes, not currency. Never invent an exposure column to force one: the
exposure must be a real declared column (publish rejects ghosts), and a made-up
number on the Money-impact card is exactly the credibility failure this spine
exists to prevent.

**Fail-loud guarantees:** a mis-named exposure column rejects at publish; an
unknown realization dataset/column is dropped loudly at consumption (never a
silent zero); a realization read that errors stamps `outcome.value_error` —
a zero always means "genuinely nothing realized", never "the read failed".

---

## 10. Fraud detection — ontology-driven artifact screening

**Fraud detection is a feature the ontology turns ON.** A source that says nothing
about fraud gets **no** artifact fingerprinting and **no** cross-record matching —
nothing is captured. You opt a dataset in, and you declare what each artifact
*means*, right here in `sources.json`. Two field groups do it:

### 10.1 Per-column: `artifact_role` + `reuse_policy`

Add these to a column that holds an artifact URL (an `image_url` / `document_url`):

| Field | Values | Meaning |
|---|---|---|
| `artifact_role` | `identity` \| `evidence` \| `supporting` | What the artifact **is** |
| `reuse_policy` | `expected` \| `suspicious` \| `ignore` | Override the role's default (optional) |

Why it matters — **the same "seen before" bit means opposite things by role**:

| Role | Reuse across records is… | Default policy |
|---|---|---|
| `identity` (a headshot, an ID scan) | **expected** — same subject; a match *verifies* identity | `expected` |
| `evidence` (an accident/damage/defect photo, a receipt) | **suspicious** — recycled proof / double-dip | `suspicious` |
| `supporting` (a brochure, generic T&Cs) | meaningless — never fingerprinted | `ignore` |
| `payment_proof` (a submitted receipt / UTR slip / bank acknowledgment) | suspicious — the same receipt cannot clear two accounts; **also pins E4 ledger verification to THIS column** | `suspicious` |

> A student re-applying for a job after six months with the **same headshot** is
> not fraud (`identity` → reuse expected). An insurance claim reusing an **old
> accident photo** *is* fraud (`evidence` → double-dip). Declaring the role is
> what lets the platform tell these apart.

Only `evidence`, `identity` and `payment_proof` columns are captured + matched;
`supporting` is never fingerprinted.

> **`payment_proof` is a pinning role.** A record can carry several documents —
> a payment receipt AND a purchase bill. The E4 ledger check reads its
> reference/amount/date/party ONLY from the column tagged `payment_proof`, so a
> different attached bill can never be matched against the payment ledger by
> mistake (which would fire a false "reference not found"). Declaring
> `fraud_screening.payment_proof` **requires** one column with this role — the
> file is rejected at publish otherwise.

### 10.2 Per-dataset: `fraud_screening` (the master switch)

A sibling of `columns` / `read_via` on the dataset block:

```json
"fraud_screening": {
  "applies": true,
  "value_fields": ["assessed_amount"],
  "identity_fields": ["policy_no", "vin", "consumer_id"],
  "incident_date_field": "reported_date",
  "location_lat_field": "premise_lat",
  "location_lon_field": "premise_lon",
  "gps_radius_km": 10,
  "payment_proof": {
    "ledger_dataset": "billing.payments",
    "match_field": "transaction_ref",
    "amount_field": "amount",
    "date_field": "payment_date",
    "party_field": "consumer_id",
    "doc_ref_field": "transaction_ref",
    "amount_tolerance_pct": 1,
    "date_window_days": 3
  }
}
```

| Field | Purpose |
|---|---|
| `applies` | **The screening switch** (one of three preconditions — see below). `true` → the screen runs on **every recommendation** for records in this dataset; `false` → **hard OFF** (even if columns declare roles); **absent** → ON only if ≥1 column declares an `artifact_role` |
| `value_fields` | *Optional, advisory.* Monetary / asset-value columns — a hint for future severity weighting (bigger stakes + reused evidence ⇒ higher severity). **Not consumed by the wiring today.** |
| `identity_fields` | **Functional.** Cross-record linkable keys (`policy_no` / `vin` / `consumer_id` / …). The builder autowires them onto the screen, and the entity index links these columns across cases → ring / double-dip / synthetic-identity signals (same identifier on another case; one identifier, many names). **Declaring a column here is what makes the SOURCE — not a field-name heuristic or the builder's `field_types` pin — decide which identifiers join cases.** Additive: an undeclared column can still link on the heuristic, so listing keys only ever *widens* linking. |
| `incident_date_field` | **Functional (EXIF↔claim).** The record column holding the **claimed incident/report date**. When declared, every `evidence` photo's EXIF capture time is compared against the record's value at screening time: a photo **captured before the incident date** cannot show the incident — a deterministic, near-zero-false-alarm signal. Absent EXIF stays a NON-signal (messengers strip it). |
| `location_lat_field` / `location_lon_field` | **Functional (EXIF↔claim).** The record columns holding the **claimed site/premise coordinates** (decimal degrees). Declare **both or neither**. When declared and an `evidence` photo carries EXIF GPS, the haversine distance is checked — "photo taken 40 km from the premise" — firing only beyond `gps_radius_km`. Text addresses are NOT supported (no geocoder — deterministic only). |
| `gps_radius_km` | *Optional.* The distance beyond which the GPS check fires. Default **10 km** — deliberately generous; a false "wrong location" flag costs officer trust. |
| `payment_proof` | **Functional (E4 — payment-proof verification).** Declares WHICH ledger dataset can verify this dataset's submitted payment proofs ("I already paid — here's the receipt"). At screening time the reference extracted from the proof document is looked up **in the declared `ledger_dataset` by `match_field`**, server-side through the structured read plane. "Reference not found" is a fact-grade fraud signal (the ledger either has the payment or it doesn't); a mismatched `amount_field`/`date_field`/`party_field` flags a doctored or reused receipt; a full match renders as **payment VERIFIED** — clearing honest disputes in seconds. `doc_ref_field`/`doc_amount_field`/`doc_date_field`/`doc_party_field` name the fields as the app's document extraction emits them (defaults: `transaction_ref`/`amount`/`payment_date`). OCR-noise guards: `amount_tolerance_pct` (default 1%) and `date_window_days` (default 3). Cross-dataset by design — the case dataset points at its ledger; both must be in the catalogue, and every named column is validated at publish (a bad name is dropped LOUDLY, never silently no-oped). **Requires one column tagged `artifact_role: "payment_proof"`** (§10.1) — the check is pinned to that document and is skipped with a visible note when the record carries none, so other attached bills can never be matched by mistake. Give the ledger dataset a good `description`: it is surfaced to the recommending agent so it knows which bill matches which ledger. |

> Like every other fraud declaration, the EXIF↔claim checks are **ontology-driven
> end-to-end**: no `incident_date_field` → no date check; no lat/lon pair → no GPS
> check. The record's claimed values are read **server-side by key** through the
> MCP's structured read plane at screening time — the LLM never supplies them.

### 10.3 `verify_against` — generic cross-dataset document verification

The `payment_proof` shape, generalized: a value extracted from a **pinned**
document is looked up **by key** in any declared dataset, server-side. Use it for
purchase bill → land registry, a field agent's "cash collected" → the ledger,
serial-number-in-photo → asset master, repair bill → surveyor estimate.

```json
"verify_against": [{
  "name": "purchase_vs_registry",
  "target_dataset": "registry.sale_deeds",
  "match_field": "deed_no",
  "doc_column": "purchase_bill_url",
  "doc_ref_field": "deed_no",
  "compare": [
    { "doc_field": "sale_value", "target_field": "sale_value", "type": "amount", "tolerance_pct": 1 },
    { "doc_field": "reg_date",   "target_field": "reg_date",   "type": "date", "window_days": 3 },
    { "doc_field": "buyer_id",   "target_field": "buyer_id",   "type": "id" }
  ],
  "description": "collateral valuation must match the registered deed"
}]
```

Rules for `verify_against` (all enforced): `name` is a unique slug (identifies the check in findings);
`doc_column` must be a **role-tagged artifact column** of this dataset — the check
reads its reference/values ONLY from that document (same two-bills protection as
payment_proof); `target_dataset` + every named column are validated against the
catalogue at publish (bad name ⇒ dropped LOUDLY). `compare[].type` is one of
`amount` (honors `tolerance_pct`, default 1%), `date` (honors `window_days`,
default 3), `id` (separator/case-insensitive), `text` (exact after trim).
"Reference not found" is a fact-grade signal; a mismatch flags with the exact
values; a full match renders as **VERIFIED** — positive evidence. Missing sides
are non-signals; unparseable values become visible notes. The target dataset's
`description` is surfaced to the recommending agent so it knows which document
matches which dataset — write a good one.

### 10.4 `date_rules` — declarative date rules (E6)

Deterministic date arithmetic over the record's **own** columns, evaluated
server-side against the record read by key (never agent-supplied values):

```json
"date_rules": [
  { "name": "claim_after_policy_start", "earlier_field": "policy_start",
    "later_field": "claim_date", "min_days_between": 15 },
  { "name": "inspection_after_work_order", "earlier_field": "work_order_date",
    "later_field": "inspection_date" },
  { "name": "statement_fresh", "earlier_field": "statement_date",
    "later_field": "application_date", "max_days_between": 90 }
]
```

A rule fires when `(later − earlier) < min_days_between` — with the default
`0` that is the plain ordering rule (an inspection dated before its work order
is impossible) — or `> max_days_between` when set (stale documents). Classic
uses: claim-within-days-of-policy-start, service date outside the coverage
window, payslip/statement older than N days at application. Every named column
must exist (rejected at publish); a missing value on either side is a
NON-signal; an unparseable date is a visible note. Date parsing follows the
`domain.country` locale.

> **Statement reconciliation (E5) needs no ontology.** When the app's document
> extraction emits bank-statement rows, the screen reconciles the running
> balance row by row automatically (each balance = prior ± transaction).
> Repeated chain breaks flag a fabricated statement; a single break is treated
> as OCR noise and only noted.

### 10.2.1 Auto-wiring needs THREE things — including a primary key

`applies: true` is necessary but **not sufficient**. The builder auto-creates the
`consistency_check` screen only when **all three** hold (`fraud_roles.py:623-635`):

| # | Precondition | Where you declare it |
|---|---|---|
| 1 | **Screening is on** — `applies: true`, or ≥1 column declares an `artifact_role` (and `applies` is not `false`) | `fraud_screening.applies` (§10.2) |
| 2 | **≥1 fingerprint target** — a column with `artifact_role` of `evidence` or `identity` (`supporting` doesn't count) | `columns[].artifact_role` (§10.1) |
| 3 | **A primary key** — a column with `is_primary_key: true`, which binds the screen to a record | `columns[].is_primary_key` (§6) |

> ⚠️ **A dataset with no declared `is_primary_key` column gets NO fraud screen** — even with
> `applies: true` and perfectly annotated artifact columns. `_primary_key(...)`
> (`fraud_roles.py:397-402`) scans `columns[]` for `is_primary_key: true`; if it finds none it
> returns `None`, the builder **skips screen creation** and only logs a warning
> (`[fraud-autowire] … no primary key`). Nothing fails, nothing raises — **fraud detection is
> simply absent**. Auto-discovered datasets (§5) introspect PKs from the DB; a hand-authored
> `datasets[]` block only has the PK **you declare**, so declaring it is on you.

**When it runs:** the screen fires while the **agent is producing its recommendation** — so its findings are already in front of the officer. It is **not** hooked to `approve`/`reject`: those are the officer's later decisions, and in on-demand mode the agent only recommends, the human decides. There is no per-outcome trigger — `applies:true` means "screen every time," full stop.

### 10.3 What "captured + matched" means (all driven by the above)

When a dataset is screened, its `evidence`/`identity` columns are, per record,
**fingerprinted three ways** and matched against every prior record:
SHA-256 (byte-identical) → dHash (recompressed) → open-weight image embedding
(crops / screenshots / re-shoots). A hit is read **by role**: an `identity`
match is *verification* (it never contributes to the fraud score); an `evidence`
match is a fraud signal. The actual severity is assigned centrally by the
screening gate (and is operator-tunable), so it is not fixed here. Every signal
carries an explanation; it is **evidence on a recommendation, never an
auto-reject**.

### 10.4 How it flows (you only edit `sources.json`)

```
sources.json  →  data-discovery crawls it into data_catalogue
              →  the app builder AUTO-WIRES the consistency_check tool from the
                 ontology: it sets which columns to fingerprint and their roles —
                 the LLM never chooses fraud scope
              →  runtime scores each match by role
```

You do **not** hand-wire anything in the app. Declare the ontology; the builder
does the rest on publish.

### 10.5 Worked column snippet

```json
{
  "name": "inspection_id", "physical_name": "inspection_id",
  "type": "string", "is_primary_key": true,
  "description": "Inspection reference — the record key the screen binds to."
},
{
  "name": "defect_photo_url", "physical_name": "defect_photo_url",
  "type": "string", "column_kind": "image_url",
  "artifact_role": "evidence", "reuse_policy": "suspicious",
  "description": "Defect PHOTO. Evidence — the same photo on another inspection is recycled proof (double-dip)."
},
{
  "name": "applicant_photo_url", "physical_name": "applicant_photo_url",
  "type": "string", "column_kind": "image_url",
  "artifact_role": "identity",
  "description": "Applicant headshot. Identity — reuse by the same applicant is expected; a match verifies identity."
}
```

The `is_primary_key` column is **not decoration** — without it these two annotated artifact
columns are never screened (§10.2.1).

**Safe default & non-regression.** A column that IS being screened but has no
explicit role is read as `evidence`/`suspicious` (so a mis-annotation never
*hides* fraud). The dataset gate still decides whether screening runs at all —
so an un-annotated source stays completely off. Existing apps pick up screening
on their next publish (when the builder re-reads the ontology).

---

## 11. `mandatory_when_used` — required policy checks (bureau / KYC / sanctions)

Some reads are not optional context — they are a **mandated check** a decision
must perform before it commits. A lending decision must pull the CIBIL score; a
payout must run a sanctions screen. Mark the dataset once and every decision app
that reads it inherits the obligation:

```json
{
  "id": "cibil.credit_report",
  "kind": "rest",
  "description": "Credit bureau report, looked up by PAN.",
  "input_schema": { "required": ["pan"], "properties": { "pan": { "type": "string" } } },
  "read_via": { "kind": "rest", "extra": { /* request/response mapping — see §5.1 */ } },
  "mandatory_when_used": true          // ← the check is REQUIRED, not optional
}
```

**What it does.** At app-build time the smart-app builder defaults the mcp read
tool wired to this dataset to `required: true`. At runtime the read-before-write
evidence gate then refuses to stage a write unless that lookup actually ran for
the case — and the agent's prompt carries a "MANDATORY CHECKS" line telling it to
run the lookup first. So the ontology declares the obligation; the platform both
**guides** the agent to it and **enforces** it.

**IT declares once, apps inherit.** Same flow as `column_kind` and
`fraud_screening`: you edit only `sources.json`; the field rides the catalogue
(`sources.json → MCP → data-discovery → builder`), and every consuming app picks
it up on its next publish.

**App-overridable.** A specific app can still opt out by explicitly authoring
`required: false` on that tool — the ontology only fills a value the builder left
unset, it never overrides a deliberate choice. (Absent/`false` ⇒ a normal
informational read; unchanged behavior.)

**Requirements.** Only a **bound** dataset lookup can be mandated — the tool must
resolve to a specific `dataset_id` (a keyed read), not a fuzzy semantic query, so
"the check ran" is unambiguous and auditable. The platform rejects a required
lookup that isn't bound.

---

## 12. Worked example — a complete structured source

```json
{
  "source_id": "billing",
  "dept_id": "billing_revenue",
  "org_id": "acme-power",
  "type": "structured",
  "is_active": true,
  "name": "Billing & Revenue — Consumer / Bill / Payment",
  "description": "Consumer master + monthly bills + payments. Drives Theft Triage, Recovery Tracker and CMD Daily Briefing apps.",
  "tags": ["billing", "revenue", "consumer"],
  "visibility": { "roles_allowed": ["user","dept_admin","org_admin","super_admin"], "public_within_org": false },
  "connection": { "type": "postgres", "env_prefix": "ACME_POWER_SQL" },
  "datasets": [
    {
      "id": "billing.consumers",
      "physical_name": "consumers",
      "name": "Consumer master",
      "kind": "sql",
      "description": "~10,000 consumers with tariff category, status and A+/…/D reliability rating.",
      "columns": [
        { "name": "consumer_id", "physical_name": "consumer_id", "type": "string", "is_primary_key": true, "description": "10-digit account number." },
        { "name": "status", "physical_name": "status", "type": "string",
          "distinct_values": ["active","temp_disconnect","perm_disconnect","theft_flagged"],
          "description": "Connection status." }
      ],
      "write_actions": [
        {
          "id": "update_consumer_status",
          "verb": "update",
          "description": "Set a consumer's connection status.",
          "sql_template": "UPDATE consumers SET status=:status WHERE consumer_id=:consumer_id",
          "key_fields": ["consumer_id"],
          "roles_allowed_write": ["dept_admin","org_admin"],
          "input_schema": {
            "type": "object",
            "required": ["consumer_id","status"],
            "properties": {
              "consumer_id": { "type": "string" },
              "status": { "type": "string", "enum": ["active","temp_disconnect","perm_disconnect"] }
            }
          }
        }
      ]
    }
  ],
  "query_timeout_seconds": 90
}
```

### Minimal read-only source via auto-discovery

```json
{
  "source_id": "warehouse",
  "dept_id": "ops",
  "org_id": "acme-power",
  "type": "structured",
  "name": "Ops Warehouse",
  "description": "Read-only ops warehouse.",
  "connection": { "type": "postgres", "env_prefix": "OPS_WH" },
  "options": { "tables": ["shipments", "inventory"] }
}
```
No `datasets[]` → the MCP exposes `warehouse.shipments` + `warehouse.inventory` (read-only,
columns introspected live). Omit `options.tables` to auto-discover **all** tables.

---

## 13. Composition checklist

1. **One source per backend-system-per-dept.** Set `org_id`/`dept_id` to match the MCP.
2. **Always set `type`** (`structured`/`mongodb`/`rest_api`/`bigquery`/`sap_rfc`/`duckdb`/`semantic`).
3. Point `connection.env_prefix` at env vars holding the creds — never inline secrets. For
   **mongodb** also set `connection.collection` (**required** — every read errors without it) and
   `connection.tenant_filter` if the collection is shared across tenants.
4. For **reads with control** (and for any **writes**), declare `datasets[]` with `columns[]`.
   For quick read-only onboarding, use `options.tables` (or let it auto-discover).
5. Add `write_actions[]` **with an `input_schema`** to any dataset that should be writable.
6. Mark PII / primary keys / FKs / enum `distinct_values` on columns — the builder uses them.
   **A declared `is_primary_key` is a hard precondition for fraud screening** — a dataset with
   `fraud_screening.applies: true` and annotated artifact columns but **no PK gets no screen at
   all**, with only a log warning (§10.2.1). For a **REST / parameterised** dataset, declare
   `input_schema` (the read param contract) and `read_via.extra.request`/`.response` (§5.1).
7. For a document corpus, use `type: "semantic"` + `rag.milvus_collection` + `taxonomy`.
8. Set `visibility.roles_allowed` (and `public_within_org` for org-wide corpora).
9. **Validate before you ship** — `python validate_sources.py sources.json` (§15). An invalid
   registry **refuses the boot**; the validator is how you find that out at your desk instead of
   in a deploy.
10. **Restart the MCP** (or set `SOURCES_REFRESH_SECONDS`) so it re-registers to discovery and
   data-discovery re-crawls the catalogue — only then does the change reach the builder.

## 14. Required MCP environment (companion to the file)

| Env | Purpose |
|---|---|
| `SOURCES_FILE` | Path to this file (e.g. `/app/sources.json`). Omit if using `SOURCES_JSON`. |
| `SOURCES_JSON` | Inline alternative to `SOURCES_FILE` for file-free hosts — this file's JSON payload as an env var. `SOURCES_FILE` wins if both set. |
| `ORG_ID`, `DEPT_IDS` | Filter which sources load (must match the file). |
| `SOURCES_STRICT` | Default **`true`** — a source that fails schema validation refuses the boot (§15.3). Set `false` ONLY as an emergency escape hatch: the MCP starts, **skipping** each invalid source with a loud error. They are absent, not repaired. |
| `{PFX}_HOST/_PORT/_DB/_USER/_PASS` (SQL), `{PFX}_URI/_DB` (mongo) | Per-source credentials referenced by `connection.env_prefix`. |
| `DISCOVERY_URL`, `MCP_API_KEY` | Register sources to discovery on boot. |
| `MCP_PUBLIC_BASE_URL` | Stable URL the MCP advertises (set under multiple replicas). |
| `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` | ✅ **Required for any structured (NL→SQL) query** — the planner calls this LLM on every `/query` (`planners/_llm.py:53-74`). **The defaults are placeholders** (`LLM_API_KEY=not-needed`, `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_MODEL=deepseek/deepseek-chat`), so an MCP that omits these **fails every structured query** with an auth error from the provider. Cloud: OpenRouter. On-prem: point at the self-hosted vLLM inference-service. |
| `LLM_EXTRA_BODY` | Provider knobs merged into every planner call. **For GLM-class reasoning models set `{"reasoning":{"exclude":true}}`** — else the model returns SQL in a reasoning field, `content` is null and the planner crashes. Default `{}`. |
| `JWT_SECRET` | Shared HS256 secret (Citra's — the same one user-service signs with) to verify `X-User-JWT`. **Mandatory whenever `AUTHZ_ENFORCE` is on:** unset ⇒ every call is rejected `503 "JWT_SECRET not configured"` (`auth.py:82-84`). |
| `AUTHZ_ENFORCE` | **Default `true` — fail-CLOSED** (`auth.py:42`). This is what makes `JWT_SECRET` mandatory: on, a missing token is `401` and a missing secret is `503`. Set `false` **only** for a local dev demo (JWT verification is then skipped entirely). |
| `EMBEDDING_*` | Used **beyond RAG**: also ranks which datasets are handed to the NL→SQL planner when a source has more tables than `NL_QUERY_MAX_DATASETS` (default 8) — cosine embedding + optional reranker (`config.py:145-147`). Must match the dim the Milvus collection was ingested at (platform standard: 768). |
| `RERANKER_URL` | Optional (e.g. `http://reranker-service:7302`) — reranks the dataset shortlist above (and semantic hits). Unset ⇒ embedding-cosine ranking alone. `RERANKER_TOP_K` (default 5) sizes the shortlist. |
| `MILVUS_URI` | Only if the MCP itself serves reads; RAG is answered platform-side. |
| `SMART_APP_SERVICE_URL` | Where audit records are POSTed. Unset ⇒ audit is buffered + logged but not shipped. |

---

*Source of truth for the fields above: `source-mcp-template/models.py` (`ColumnSpec`,
`WriteAction`, `DatasetSchema`, `DecisionHistory`, `FraudScreening`), `router.py` (`_flatten`,
loader), `config.py` (env), `catalogue.py:119-176` (dataset resolution),
`catalogue.py:506-542` (mongo connection), `connectors/sql_connector.py` (`env_prefix` →
`{PFX}_*`), `connectors/rest_connector.py` (the REST read contract), `registration.py`
(discovery payload), `smart-app-service/fraud_roles.py` (fraud auto-wiring). Real example:
`demo-data/tenants/acme-power/mcp/sources.json`.*


## 15. Validating the file

This file used to be read as free-form JSON. Nothing checked it, so a typo in a field **name**
silently did nothing: write `artifact_roles` instead of `artifact_role` and fraud screening on
that column is simply off — no error, no warning, and the source looks fine. Two artifacts now
close that, both generated from one definition (`registry_models.py`).

### 15.1 The validator (authoritative)

```bash
cd source-mcp-template
python validate_sources.py /path/to/sources.json          # exit 0 = valid, 1 = problems
python validate_sources.py 'tenants/*/mcp/sources.json'   # globs work on every OS
python validate_sources.py sources.json --json            # machine-readable, for CI
```

It names the path, the problem, and why it matters:

```
  FAIL sources.json  (3 problems)
       sources[0] (billing).type
         -> Field required
            required field.
       sources[0].datasets[0].columns[0].artifact_roles
         -> Extra inputs are not permitted
            unknown key — nothing reads it… `artifact_roles` disables fraud
            screening on that column with no warning.
       sources[1] (billing)
         -> duplicate source_id — also at sources[0]
```

Two things it checks that a plain JSON-Schema tool cannot, because they span fields:

| Rule | Why |
|---|---|
| `type: mongodb` ⇒ `connection.collection` | without it EVERY read errors and introspection returns zero columns |
| `type: semantic` ⇒ `rag.milvus_collection` | the platform reader has no corpus to read |
| `fraud_screening.applies: true` ⇒ a PK **and** an evidence/identity column | otherwise the autowire creates **no screen at all** (§10.2.1) |
| `artifact_role` ⇒ a media `column_kind` | an artifact column is resolved as media; a `plain` one fails at runtime |
| `decision_history.*_field` ⇒ names a declared column | otherwise the learning loop silently never learns |
| `source_id` unique | the registry is keyed by it — one silently shadows the other |

**Note:** a source's cross-field rules only run once its individual fields are valid, so a first
run's problem count is a floor, not a total. Re-run until clean.

### 15.2 The schema file (editor support)

`source-mcp-template/schema/sources.schema.json` is standard JSON Schema (draft 2020-12). Add
`$schema` to the top of your file and VS Code autocompletes fields and flags errors **as you
type** — including enums, so `evidance` is a squiggle rather than a log line nobody reads:

```json
{
  "$schema": "../../source-mcp-template/schema/sources.schema.json",
  "sources": [ ... ]
}
```

It is **generated** from `registry_models.py` — never hand-edit it; CI (`sources-contract.yml`)
regenerates and fails on drift. It covers structure, types, enums, required fields and unknown
keys; the cross-field rules above live in the validator.

### 15.3 What happens at boot

The MCP validates every source it is about to **serve** (after the org/dept/`is_active` filter —
another dept's malformed source in a shared registry is not yours to fail on).

- **Invalid ⇒ the MCP refuses to start**, listing every problem in every source at once. A
  registry is config: written deliberately, reviewed before deploy, and §15.1 exists so this
  never reaches a running MCP.
- `SOURCES_STRICT=false` is the **emergency escape hatch**: boot anyway, **skipping** the invalid
  sources with a loud error naming each. They are absent, not repaired — consumers routing to
  them will 404. Use it to get a live MCP back up, then fix the file.

**Under hot-reload (`SOURCES_REFRESH_SECONDS`) it is deliberately NOT symmetric.** A running MCP
does not fall over because someone saved a bad edit: the reload raises, the error is logged, and
the MCP **keeps serving the last-good registry** (`router._refresh_loop`). The consequence to know
about: your edit did **not** take effect, and the only signal is that log. If a hot-reloaded change
seems to have done nothing, check the MCP log before assuming the field is unwired — and prefer
`validate_sources.py` before saving, which is the whole point of §15.1.
