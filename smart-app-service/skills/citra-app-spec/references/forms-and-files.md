<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# citra-app-spec — Form field types & SoR media (view/download)

Read this when authoring a `form` panel or when a record's media (photo / PDF)
must be shown. Full control catalogue: `citra-ui-fields`.

> ## ⛔ FILE UPLOAD / CITRA MEDIA COLUMNS ARE DISABLED
> `format:"file"` (uploading bytes into Citra's own storage / a Mongo-overlay
> media column) is **turned off** — publish **rejects it (rule F-01)**. Do NOT
> author a file-upload field, `accepted_types`, `multiple`, or any
> `semantic_type:"file"` write input. Everything below about *storing* an
> uploaded blob, the Citra S3 bucket, `smartapp-blob://`, and the presign
> read-back **no longer applies**.
>
> **SoR-record media is READ-ONLY and comes from the source system, streamed
> through the dept-MCP.** To show a record's photo/document, add a `detail`
> panel `attachment` section with `fields:["<media_column>"]` bound (via the
> panel's linked queue) to the record's **`mcp` data_source**. The column value
> is an **opaque identifier** — the runtime passes `(key_field, key, column)` to
> a same-origin `/api/media` route, smart-app forwards to the dept-MCP `/media`,
> and the MCP resolves the ref + **streams the bytes**. The browser never
> touches S3 / the source storage. Content-type (image vs PDF) picks the viewer
> automatically; a Download button is always shown. No `format:"file"`, no
> presign, no upload.

## Form field types

A `form` panel renders one control per JSON-Schema property; the runtime picks the control from the property. Map the BA's request to the right shape:

| BA wants | Schema on the property | Renders as |
|---|---|---|
| text box | `type: "string"` | single-line text |
| multi-line note | `format: "textarea"` | textarea — **set this** on notes/justifications/descriptions (a plain string is a cramped one-liner) |
| number | `type: "number"` / `"integer"` (+ `minimum`/`maximum`) | number input |
| **money / currency** | `type: "number"` + `format: "currency"` (+ `minimum`/`maximum`) | numeric input, decimal step, money keypad |
| checkbox | `type: "boolean"` | checkbox |
| date / datetime | `format: "date"` / `"date-time"` | date picker |
| **time of day** | `format: "time"` | time picker |
| **static dropdown** | `type: "string"` + `enum: [...]` | `<select>` from the fixed list |
| **radio group** | `enum: [...]` + `format: "radio"` | radio buttons |
| **multi-select** | `type: "array"` + `items.enum: [...]` | multi-select (value is an array) |
| **dynamic dropdown** (live list from a data source) | `options_source: {kind:"data_source", data_source:"<ds id>", value_column, label_column?, filter?, limit?}` | `<select>` populated at render from `/field-options` (live DISTINCT values) |
| **file upload** (store a document/photo) | `format:"file"` (+ `accepted_types`, `multiple`) | file picker → base64 into the write payload (see **Files & documents**) — only when a file column exists |
| (anything else) | — | single-line text |

**Dynamic dropdown** is how you give a data-entry form a picker backed by live data — e.g. *department* from the HR `departments` table, *manager* from `employees`. Declare a `data_sources[]` entry (`type:"mcp"`, dot-qualified ref) and point the field's `options_source.data_source` at its `id`; the server resolves the choices (the client can't request an arbitrary column). Use a static `enum` only for small fixed lists (e.g. employment type).

Property `title` overrides the field label; `description` renders as an inline hint.

**New-employee / data-entry form pattern:** structured fields (text + number + date + dropdowns) on a `form` panel, with `on_submit.tool_name` bound to the source's `create_*` write — the submit commits a new record directly (no LLM) and is audited. Dynamic dropdowns pull dept/manager from the live tables.

> **Still not rendered on forms:** lookup, hidden, toggle (use text / checkbox instead). (`currency` and `time` **do** render — see the table above and `citra-ui-fields`.)

## Files & documents — upload, view, download

**The canonical blob signal (one language across every layer).** A column holds a file **iff** its catalogue/`dept_sources` entry is **`semantic_type: "file"`**, and the write that accepts it declares that input as **`{"type":"string","format":"file"}`** in its `write_actions[].input_schema`. These two are mirrors: `semantic_type:"file"` (read/display side) ≡ `format:"file"` (write-input + AppSpec form-field side). The workflow builder sets both when it sees a file/blob/document column; `dept_sources` and `data_catalogue` carry them; the builder, gate and runtime all key off them.

**RULE — the platform stores every uploaded file; wire the picker whenever the write has a column to hold the reference.**
On submit, the platform (Citra-Service) stores the uploaded blob in the Citra bucket and writes a durable **ref string** into the bound write column — for **both** `format:"file"` write inputs and plain `string` columns. The dept-MCP always receives a **string** (the ref/URL), **never a blob object**: generic SQL/REST write actions take a `type:"string"` column, so a raw blob descriptor (a JSON object) would fail their input validation. The runtime resolves the ref back to a presigned URL on the detail page. So a `format:"file"` form field is valid against any write column that can hold a string — the submit does **not** 422; the platform bridges it.

So: **wire the file picker whenever the bound write has either a `format:"file"` input or a string field to hold the reference** (almost always true). Only fall back to **`requirements_unmet`** if the write has **no field at all** that could store even a reference — *"the `<action>` write exposes no column to record the file"* — or if the platform fallback is disabled (`CITRA_SERVICE_URL` unset). **Do not** tell the BA to "ask IT for a file-capable column" just because the column is a `string` — that case is handled.

- **Upload (store a file on a record)** — a form field with `format: "file"` (+ optional `accepted_types: [...]`, `multiple: true`). The runtime renders a file picker; on submit the file is base64-encoded into the write payload (`{filename, content_type, data}`) and the bound `mcp_action` / `on_submit.tool_name` write commits it to the file column. Audited like any write. Use for "attach the offer letter / ID copy / signed form".
- **View / download** — a `detail` panel section of `type: "attachment"` with `fields: ["<file column>", …]`. The runtime reads the column (a URL, or `{url|data, content_type, filename}` the MCP serves) and renders the right viewer **automatically by content type**: an **image preview** (photos), an **inline `<video>` player** (video/*), an **inline `<audio>` player** (audio/*), an **Open document** link (PDFs), plus a **Download** button always.
- **Media types** — `format:"file"` accepts any type via `accepted_types` (e.g. `["image/*"]`, `["audio/*","video/*"]`, `["application/pdf"]`). Photos / audio / video / docs all store to the Citra S3 bucket and resolve to a presigned URL on view (same path as photos). **Caveat — size:** the upload is base64-inline in the submit payload, fine for photos / short clips / modest docs; for **large** audio/video use a pre-signed-URL upload MCP action instead (the inline path is not for big media).

> **CRITICAL — name the file field to MATCH the write column, and (for agent writes) PASS THE BLOB, never "set a URL".** The uploaded blob is carried only to the write input **whose name matches the form field**. So if the write column is `evidence_photo_url`, name the form field **`evidence_photo_url`** (use `title:"Meter Photo"` for the label) — NOT a different name like `meter_photo`, or the blob never reaches the column and the file isn't stored. This holds for both `on_submit.tool_name` (direct) and `on_submit.agent_action` writes.
>
> For an **agent‑mediated** write (`on_submit.agent_action`), the agent constructs the write payload — so its `system_prompt` MUST tell it to **pass the uploaded file straight through to the write column**, NEVER to "set the column to the uploaded file's URL." The agent has **no real URL** for the upload; told to set one it will *fabricate* it (e.g. `s3://…`) and the stored value won't download. The platform stores the blob (the `format:"file"`/S3‑fallback path) and fills the downloadable reference. Write the prompt as: *"Pass the uploaded `<file_field>` straight through to `<write_column>` unchanged — the platform stores it and records the link. Do NOT construct or set a URL."* This is the #1 cause of "upload works but download is broken / shows a weird `s3://` link."

```jsonc
// New-employee form that stores a document on submit:
{ "type": "form", "id": "new_employee", "title": "New employee",
  "schema_inline": { "type": "object", "required": ["full_name", "department"],
    "properties": {
      "full_name":  { "type": "string" },
      "department": { "type": "string", "options_source": { "kind": "data_source", "data_source": "ds_depts", "value_column": "code", "label_column": "name" } },
      "offer_letter": { "type": "string", "format": "file", "accepted_types": ["application/pdf"], "title": "Offer letter" }
    } },
  "on_submit": { "tool_name": "create_employee" } }

// Detail page section to view/download the stored document:
{ "type": "attachment", "title": "Documents", "fields": ["offer_letter", "photo"] }
```

Large files: prefer a pre-signed-URL MCP action over the inline base64 path when the source advertises one (the base64 path is for modest documents).

**Separately — uploading a file for the AGENT to *read* (OCR), not store:** that is the `accepts_files` + `vision_ocr` path (LLM reads a claim photo / scanned form). It is panel-level and a different mechanism from the `format:"file"` store-to-column field above; see `citra-ocr/SKILL.md`.

## App-owned overlay — track your own data alongside the system of record

When the BA wants to track something the **source system doesn't model** — a private note, a review/sign-off, a routing rationale, an operational status/tag, a checklist — store it in an **app-owned overlay**, NOT by inventing a source field. The overlay is the team's *operational layer* on top of the untouched golden record.

**THE FIELD-ROUTING RULE — decide per field:**
- A **source-system column exists** for it → it's a **governed write** (`mcp_action`, recommend→approve→MCP). The golden record changes.
- **No source column** for it → it's an **app-owned overlay write** (app-local, audited, lower-stakes). The golden record is untouched.
- **Never invent an app field where a SoR field exists** (don't shadow), and **never change SoR business state through the overlay** (don't launder — e.g. an overlay "routed to Ravi" note ≠ reassigning the case owner in Salesforce; that owner change, if wanted, is its own governed write).

**ANCHOR RULE — the overlay is ALWAYS correlated to a SoR record.** Every overlay row carries `record_id` = the id of the **source-of-record row it annotates**. There are **no standalone overlays** — if the BA asks for data with no underlying system, decline: *"I build operational layers over your systems of record, not standalone databases — which system holds the records?"*

**Declare it** as a `data_source` of type `smart_app_records`, where `ref` is the overlay *kind*:
```jsonc
{ "id": "ds_inspection_overlay", "type": "smart_app_records", "ref": "inspection_review" }
```

**Write it** — the agent calls the standard write tool targeting that data_source id, with `record_id` = the SoR row and the app-owned **structured** fields as the payload (to store a *file* on a record, use the `format:"file"` write-column path above — files do not go in the overlay):
```
perform_action(dataset_id="ds_inspection_overlay", action_id="save_record",
               payload={ "record_id": "<SoR record id>",
                         "reviewer_note": "...", "review_status": "approved" })
```
The platform writes it to the app-owned store (env-routed to the `test_` store), merges fields per record, stamps ownership + audits — **no governed-MCP write, no DDL.** In the agent's `system_prompt`, route overlay fields here and SoR fields to the `mcp_action` write; pass `record_id` through from the record being worked.

**Read/render it** with a `smart_app_records` data_source filtered to the record (e.g. in the detail panel beside the golden fields), and **show provenance** — label golden (from the source system) vs app-owned (entered by people) distinctly so an officer/auditor never confuses the two.

**Two shapes — choose with `mode`:**
- **`mode:"merge"`** (default) — **one row per record**; an overlay write *merges* fields (e.g. status / assignee / latest note on the case). Read is filtered by `record_id`.
- **`mode:"thread"`** — a comment/review **HISTORY**: each write *appends* a new row anchored to the SoR record, the platform stamping author + timestamp per row. A read returns the **list** (newest first) — render it in a `queue`/`table` panel (the comment history) or a detail section. Write the same way (payload `record_id` = the SoR record + the comment fields).
```jsonc
{ "id": "ds_case_comments", "type": "smart_app_records", "ref": "comment", "mode": "thread",
  "filters": { "record_id": "{record.case_id}" } }   // read = every comment on this case
```
Use **merge** for per-record state (status/assignee/flags), **thread** for an append-only history (comments, reviews, routing decisions over time).
