<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Authoring over an API / REST source (e.g. a CIBIL credit screen)

Read this when the catalogue shows a dataset whose **`source_type` is `rest_api`**
(a live external API IT has registered on the dept-MCP — a credit bureau,
fraud registry, KYC/PAN lookup, weather/logistics API, …). It behaves like a
table you **parameterise**: you supply inputs, the MCP calls the API and returns
rows. You do **not** author the HTTP details — IT owns those.

> **⚠️ TWO MISTAKES THAT WILL BREAK A REST-SOURCE APP — avoid both, every time:**
>
> 1. **BIND THE PARAM ON THE RESULT PANEL'S DATA_SOURCE `filters`.** The panel that
>    shows the result MUST set its data_source's
>    `filters: {"<param>": "{param.<param>}"}` (form→navigate flow) or
>    `{"<param>": "{record.<field>}"}` (record flow) so the entered/navigated value
>    reaches the REST source. A data_source with **`filters: null`/absent → the
>    required input is missing → the panel returns 502.** This is the #1 REST-source
>    bug — a queue/detail over a `rest_api` dataset is useless without the filter.
> 2. **NEVER put a `chart` (or KPI / any aggregation) panel over a `rest_api`
>    dataset.** A chart aggregates rows (`GROUP BY`); a single-object REST response
>    can't be aggregated, so the page throws a **Server-Components render error**
>    ("Something went wrong"). Show the columns in a `detail` (or a 1-row
>    `table`/`queue`) **only**. Do not add charts/KPIs the BA didn't ask for.

## What the catalogue gives you

A `rest_api` dataset entry carries the same shape as any dataset **plus** a read
parameter contract:

- **`columns[]`** — the OUTPUT fields (flattened by IT), e.g. `credit_score`,
  `status`, `as_of`. Render these like any table/detail columns.
- **`input_schema`** — the JSON Schema of the params you MUST supply to invoke
  the read, e.g. `{"required":["pan"], "properties":{"pan":{"type":"string"},
  "loan_id":{"type":"string"}}}`. This is the read-side mirror of
  `write_actions[].input_schema`.
- `read_via` — the request/response mapping. **IT owns this; never author or
  edit it.**

## The pattern — supply params, render columns

An API read needs its `input_schema` params. Supply them as the data_source's
**`filters`** (the flat equality predicate the runtime forwards as the API
params). Two common shapes:

**A. Screen a specific record (detail).** The record already holds the key
(e.g. a case row with a `pan`). Bind a `detail` panel section (or a small
`table`) to the `mcp` dataset, passing the key via `filters`:
```jsonc
{ "id": "ds_cibil", "type": "mcp", "ref": "fraud_bureau.cibil",
  "filters": { "pan": "{record.pan}" } }          // {record.*} = the opened record's field
```
Render `columns` (`credit_score`, `status`) in the detail. Because a bureau call
returns **one object**, bind it to a **detail** (one row) — not a big queue.

**B. Look up on demand (form → result).** Collect the params with a `form` whose
fields match `input_schema`, then show the result:
```jsonc
// Page 1 — the form. on_submit navigates to the result page, passing the input
// as a page PARAM (params values reference the form's fields as {form.<field>}
// or bare {<field>}):
{ "type": "form", "id": "cibil_lookup", "title": "Credit check",
  "schema_inline": { "type":"object", "required":["pan"],
                     "properties": { "pan": { "type":"string", "title":"PAN" } } },
  "on_submit": { "navigate": { "page": "result", "params": { "pan": "{form.pan}" } } } }

// Page 2 — the result. The data_source MUST carry filters referencing the
// navigate param, and a detail/table renders the columns. THIS filter is what
// feeds the REST source — without it the read 502s:
"data_sources": [
  { "id": "ds_cibil", "type": "mcp", "ref": "fraud_bureau.cibil",
    "filters": { "pan": "{param.pan}" } }        // {param.*} = the navigate param — REQUIRED
],
// result page panels — a detail/table over ds_cibil (NO chart):
{ "type": "table", "id": "cibil_result", "title": "Credit report",
  "data_source": "ds_cibil", "fields": ["credit_score", "status"] }
```

## Rules

- **Every panel over a `rest_api` dataset MUST bind its data_source `filters`** to
  the param source (`{param.<x>}` or `{record.<x>}`) that supplies each `required`
  input. `filters: null`/absent = a broken panel (502). The MCP does **not** guess
  a missing param.
- **No aggregation panels over a `rest_api` dataset** — no `chart`, no KPI, no
  `aggregation`. A single-object/parameterised REST read can't be grouped; the
  page will error. Render columns in a `detail` or `table` only.
- **Declare the data_source as `type:"mcp"`** (dot-qualified `ref`
  `<source>.<dataset>`). There is no `rest_api` AppSpec type — the API-ness is
  the catalogue's `source_type`; the runtime + MCP handle it.
- **Render `columns`; don't invent fields.** The API returns exactly the
  catalogue `columns` as typed values.
- **Single-object APIs → detail panel; list APIs → table/queue.** Check the
  dataset: if it returns one record per call, don't bind it to a scrolling queue.
- **Reads are agent-callable too:** an `mcp` read tool over a `rest_api` dataset
  can be called by an agent, but the deterministic form/detail path above is the
  primary, most reliable way to "call the API and show the answer".

## Example — equipment-inspection app enriched with a bureau check

A fraud screen opens an inspection (`INS-2026-0013`) that carries the vendor's
`pan`. Add a detail section bound to `fraud_bureau.cibil` with
`filters:{"pan":"{record.pan}"}` → the officer sees the vendor's `credit_score`
and `status` inline, fetched live through the MCP, alongside the SoR fields.
