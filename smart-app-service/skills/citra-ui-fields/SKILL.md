---
name: citra-ui-fields
description: Canonical catalogue of form input controls a Citra app can render — the JSON-Schema hint that produces each control
metadata:
  category: citra
  tools: [bash]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra UI — Form Fields Catalogue

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift. Don't trust a remembered rule over the
> runtime you can read.


## Purpose

The single source of truth for **which form input controls the runtime
(`citra-app-runtime`) can render, and the exact JSON-Schema hint that produces
each one.** A `form` panel is rendered purely from JSON-Schema — the runtime's
`fieldControl()` inspects `type` / `format` / `enum` / `options_source` on each
property and picks the control. There is **no separate "control" key** on a form
field; emit the schema hint below and the right widget appears.

> **Contract rule.** Only emit hints listed here. The runtime **fails loud** on
> a panel type it doesn't know (visible error card + console error), so never
> invent a control the catalogue doesn't list — it will not silently degrade.

## When to use

- Phase 3 (`citra-app-ui-design`) — to speak about fields in plain language.
- Phase 3.5 (`citra-app-spec`) — when authoring a `FormPanel`'s `schema_inline`
  (or the agent `input_schema` a form binds to via `schema_ref`).

## The control catalogue

| BA wants | JSON-Schema on the property | Renders as |
|---|---|---|
| text box | `{"type": "string"}` | single-line text input |
| multi-line note | `{"type": "string", "format": "textarea"}` | textarea |
| number | `{"type": "number"}` or `{"type": "integer"}` (+ `minimum`/`maximum`) | number input |
| **money / currency** | `{"type": "number", "format": "currency"}` (+ `minimum`/`maximum`) | numeric input, decimal step, money keypad |
| checkbox (boolean) | `{"type": "boolean"}` | checkbox |
| date | `{"type": "string", "format": "date"}` | date picker |
| date + time | `{"type": "string", "format": "date-time"}` | datetime picker |
| **time of day** | `{"type": "string", "format": "time"}` | time picker |
| static dropdown (combo box) | `{"type": "string", "enum": ["a","b"]}` | `<select>` |
| radio group | `{"type": "string", "enum": [...], "format": "radio"}` | radio buttons |
| multi-select (list box) | `{"type": "array", "items": {"enum": [...]}}` | multi-select list |
| **dynamic dropdown (MCP-backed)** | `{"type": "string", "options_source": {"kind":"data_source","data_source":"<ds id>","value_column":"...","label_column":"...","filter":{...},"limit":50}}` | live `<select>` fetched from `/field-options` |
| **typeahead / autocomplete** | same as dynamic dropdown + `"search": true` on `options_source` | search-as-you-type combobox (debounced `?q=` to `/field-options`) — for high-cardinality dimensions |
| **read-only display** | `{"type": "string", "format": "readonly", "default": "<value>"}` | static text (still submits its `default`) |
| anything else | — | single-line text |

> **No file uploads / media columns.** `format:"file"` is **disabled** — it uploaded bytes into Citra's own storage (a Mongo-overlay media column), which publish now **rejects (rule F-01)**. SoR media (photos, PDFs, scans) lives in the **source system** and is read via an `mcp` data_source: the dept-MCP resolves the reference and **streams the bytes** to the app (browser never touches storage). To show a record's photo/document, put the column in a `detail` panel `attachment` section bound to the record's `mcp` data_source — do **not** add a file-upload field.

### Multi-step (wizard) forms

For a long intake form, group fields into ordered steps on the `FormPanel`:

```json
{ "id": "intake", "type": "form", "schema_inline": { ... },
  "steps": [
    { "title": "Applicant", "fields": ["name", "phone"] },
    { "title": "Details", "fields": ["category", "amount"] }
  ],
  "on_submit": { "tool_name": "create_case" } }
```

The runtime paginates with Back / Next and submits on the last step. Any field
not named in a step renders on the final step (nothing is dropped). Required
fields are validated on submit and the form jumps to the first missing one.

Add `"title"`, `"description"`, `"default"` on any property for label, help
text, and prefill. List a property name in the schema's `"required": [...]` to
mark it mandatory.

### Notes

- **Currency vs number.** `format:"currency"` is for monetary amounts — it
  renders a numeric input with a 0.01 step and a decimal keypad. The *display*
  side (KPI tiles, tables) formats money from `theme.currency`; the input only
  collects the number. Use plain `number` for counts/quantities.
- **Dynamic dropdown** is the right choice for an MCP dimension with a small-to-
  moderate option set (status, category, officer). For a **high-cardinality**
  dimension (every consumer id, every meter number) use **typeahead**
  (`options_source.search: true`) so the officer searches instead of scrolling
  thousands of options.

## Deliberately excluded (will not be added)

Rich-text / WYSIWYG editors, raw-HTML embeds, and color pickers are **out of
scope by design** — they add XSS surface and don't serve internal officer
tools. Do not request them; surface the need in `requirements_unmet` instead.
