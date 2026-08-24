<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-app-spec — Detail panels & document panels

Read this when wiring a record drill-down (`detail` panel) or a document
library (`document_view` panel / `documents` detail-section).

## Detail panels — record drill-down

A `detail` panel is the second half of the **queue → detail** pattern: a queue lists records, the officer clicks one, a detail page shows that single record in full. Wire it in three parts:

1. **The queue** gets a row-click action that navigates to the detail page, passing the record id:
   ```jsonc
   {"label": "Open record", "is_row_click": true,
    "navigate": {"page": "muster_detail", "params": {"id": "{row.muster_id}"}}}
   ```
2. **The detail page** is a `hide_in_nav: true` page declaring the id param:
   ```jsonc
   {"id": "muster_detail", "title": "Muster Record", "hide_in_nav": true,
    "params": [{"name": "id", "required": true}],
    "panels": [ ...the detail panel... ]}
   ```
3. **The detail panel** sets `linked_to` (the queue panel id) and `id_field` (the column on that queue the `?id=` param matches — e.g. `muster_id`). The runtime fetches the record from the queue's data source by that id. If `id_field` is omitted the runtime auto-detects a column named `id` / `*_id` / `record_id` — set it explicitly when the primary key has an unusual name.

### Detail sections
`sections[]` is an ordered list; each section is one block in the record view:

| section `type` | renders |
|---|---|
| `fields` | the record's field values. Omit `fields` to show every column, or set `fields: [...]` to pick a subset. |
| `attachment` | file/blob columns on the record — inline image preview for images, download link otherwise. Set `fields: [...]` to pick which file columns. |
| `markdown` | static guidance — set `content`. Good for a "what to check" note. |
| `agent_timeline` | the audit trail of agent runs on this record (decision, reasoning, model). Read-only — no config. |
| `documents` | reference documents — set `data_source` to a data source id (see **Document panels**). |
| `approval` | pending-approval runs with Approve / Reject buttons — set `roles` to the approver role ids. |
| `agent_chat` | a chat with the app's agent — optionally set `agent_role` for a named sub-agent. |

```jsonc
{"id": "muster_view", "type": "detail", "linked_to": "suspect_musters",
 "id_field": "muster_id",
 "sections": [
   {"type": "fields", "title": "Muster record"},
   {"type": "markdown", "title": "What to check", "content": "Confirm ..."},
   {"type": "agent_timeline", "title": "Audit history"},
   {"type": "documents", "title": "Relevant guidelines", "data_source": "ds_policy"},
   {"type": "agent_chat", "title": "Ask the co-pilot"}
 ]}
```

## Document panels & document data sources

A `document_view` panel browses a library of reference documents (policies, guidelines, SOPs). It binds a `data_source`; the runtime renders each row as a document card, picking up `title`, `summary` (or `text` / `snippet` / `content`), `doc_type` and an optional `url`. `doc_types: [...]` on the panel are shown as chips. The `documents` detail-section reads a data source the same way.

Two ways to back a document panel:
- **`type: "static"`** — embed the documents inline in `data_source.filters.rows`, one object per document with `title` / `doc_type` / `summary`. Deterministic and dependency-free — **prefer this for a fixed reference library** (a department's governing policy set).
- **`type: "rag"`** — a semantic dept-MCP corpus (e.g. `policy_library`), when `citra-mcp-discover` shows a `source_type: "semantic"` source and live search is desired. The `ref` is the bare `source_id`. **Not** `type: "mcp"` — `mcp` is for structured queries; `rag` is for semantic retrieval.

```jsonc
// Static: deterministic, ships with the app
{"id": "ds_policy", "type": "static", "ref": "policy_library",
 "filters": {"rows": [
   {"title": "MGNREGS Operational Guidelines", "doc_type": "guideline",
    "summary": "Governs muster-roll recording and wage payment ..."}
 ]}}

// RAG: live semantic search against the dept-MCP's vector index
{"id": "ds_policy", "type": "rag", "ref": "bsphcl_policy_library"}
```

### Critical for RAG-backed document panels — read this

Runtime forwards two retrieval hints to the MCP **from the panel definition itself** (the data_source carries no query of its own by default). Both come straight off the published AppSpec:

1. **`doc_types`** on the panel scopes the corpus to specific document categories. The runtime sends `body.doc_types = panel.doc_types` to the MCP `/query`. Without this, the MCP returns chunks of any category — your SOP panel gets templates, charters, circulars, the lot.

   Discover the available categories from the source's advertised `taxonomy.doc_types` in `citra-mcp-discover`, then **pick the subset that matches the panel's topic**:
   - A panel titled *"DT failure response & SAIDI policy"* → `doc_types: ["sop", "template"]` (procedures + reporting templates).
   - A panel titled *"Theft inspection circulars"* → `doc_types: ["circular", "sop"]`.
   - A panel titled *"Recovery drive guidelines"* → `doc_types: ["guideline", "charter"]`.

   The chips that render under the search box come from this list — if you don't set it, the chips are missing and the panel returns mixed-category noise.

2. **`title`** on the panel doubles as the **implicit semantic query** when the data_source has no explicit `filters.query`. Milvus + the embedding model produce strong relevance scores (~0.9+) for domain phrases like *"DT failure response & SAIDI policy"*; a generic `"*"` falls back to near-zero relevance and surfaces random chunks. **Write panel titles like a search phrase a person would type**, not as generic labels like *"Policy"* or *"Documents"*.

   The resolution order at runtime: `ds.filters.query` (explicit override, rarely needed) → `panel.title` (default) → `"*"` (last-resort fallback that returns noise).

   If a panel's *display* title is unavoidably generic, set `ds.filters.query` explicitly:
   ```jsonc
   {"id": "ds_policy", "type": "rag", "ref": "bsphcl_policy_library",
    "filters": {"query": "distribution transformer maintenance and failure SOP"}}
   ```

(Classification scoping is **not** a `document_view` panel field — the schema forbids it. To bound classification, filter through the `data_source` instead.)

The runtime smart-fetch is in the runtime's panel_data.py `_resolve_mcp_rows` (semantic branch). The contract is one-way: the AppSpec is frozen at publish, the runtime trusts it. If you set the fields well, the panel surfaces the right docs. If you don't, you get random low-relevance chunks — and the publish validator can't catch this for you because these fields are syntactically optional.
