<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Decision API — Endpoint Reference

Base: your smart-app-service origin, e.g. `https://apps.citra-ai.com/api`.
Auth: **every** request carries `Authorization: Bearer <end-user-jwt>` unless
noted. `{slug}` is the app's slug; `{cid}` a run's `correlation_id`.

Errors are `4xx/5xx` with `{"detail": <string | object>}`. The SDKs raise
`DecisionApiError(status, detail, path)`.

> **Start here:** `GET /apps/{slug}/decision-contract` returns a self-describing
> contract (actions, request schema, endpoints, governance) for that app. Read
> it once and drive the integration from it instead of hard-coding.

## Discovery

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/apps` | — | `{ apps: [{app_id, slug, title, …}], total }` |
| GET | `/apps/{slug}` | — | `{ app_spec, agent_spec }` (full spec) |
| GET | `/apps/{slug}/decision-contract` | — | `{ slug, headless, item_review_gate: hard\|soft\|none, run_actions[], write_actions[], required_evidence[], endpoints{}, request_schema, response_shape, approve_request, example, auth, governance }` |

## The governed decision loop

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/apps/{slug}/run` | `{ action, inputs, correlation_id?, mode? }` | `RunResult` |
| POST | `/apps/{slug}/run/{cid}/approve` | `{ decision, overrides?, expected_plan_hash?, decision_reason?, note? }` | `RunResult` |
| POST | `/apps/{slug}/tool/{tool_name}` | `{ arguments, panel_id? }` | `{ correlation_id, tool_name, panel_id (null when headless), result }` |
| POST | `/apps/{slug}/chat` | `{ message, … }` | `{ reply (markdown), blocks?, tool_calls? }` |

- **`action`** is one of `decision-contract.run_actions`. **`inputs`** conforms to
  `decision-contract.request_schema`. `mode` labels the caller origin
  (`queue_action` default | `chat`). *(Earlier docs described a `surface` field —
  the server never had one; it was ignored.)*
- **`RunResult`** = `{ correlation_id, status: "completed"|"pending_approval"|"failed",
  outputs, timeline, error?, decision?, reasoning?, planned_writes?[], plan_hash?,
  item_findings?[], citations? }`.
  `pending_approval` ⇒ show `decision`/`reasoning`/`planned_writes`, then `approve`.
  Echo `plan_hash` back as `expected_plan_hash` on approve — if the staged plan
  changed since display, the server rejects with **409** instead of committing
  different values (the display==commit guard).
- **`item_findings`** are per-item review cards — `{ item_id, item_type, modality:
  image|document|api|case, subject?, fields, recommendation?, confidence, rationale,
  artifact_flags? }`. Disposition each via `/items/{item_id}/feedback`; with
  `item_review_gate: "hard"` every non-`case` finding **must** be dispositioned first —
  **server-enforced**: `approve` returns **409** listing the unreviewed items.
  A `case` (fraud) item is **evidence only** — never gates, never rejects.
- **`required_evidence`** (on the contract) lists what the agent MUST read before a write
  is staged — the anchor `record`, bound `image_analyze`/`doc_extract` media, and
  policy-required `lookup`s (a bureau/KYC/CIBIL check marked `required`).
- **`decision`** = `approve | reject | cancel`. **`overrides`** is an array aligned
  to `planned_writes`; `overrides[i] = { field: value }` edits that write's
  **editable fields** (governed override; value allow-list enforced server-side).
- **`/tool/{tool_name}`** = a human decision **without** the AI (`direct`). Same
  governed write path. Omit `panel_id` for headless apps; when present it's
  enforced against that panel's tool allow-list.

## Data — render your own UI

| Method | Path | Notes | Returns |
|---|---|---|---|
| GET | `/apps/{slug}/data/{panel_id}` | query params = predicates | `{ rows[], columns?, total?, truncated?, note? }` |
| GET | `/apps/{slug}/detail/{panel_id}?id={record_id}` | one record + sections | `{ panel_id, record, record_columns, sections[], note? }` |
| POST | `/apps/{slug}/field-options` | dropdown/typeahead choices | options list |
| GET | `/apps/{slug}/media/{ds_id}?key_field=&key=&col=` | **streams bytes** (photo/PDF) via the MCP — never a storage URL. `Content-Type`/`Content-Disposition` set. | binary |
| GET | `/apps/{slug}/notifications/{panel_id}` | pending approvals / SLA | notifications |

## Governance · audit · learning

| Method | Path | Returns |
|---|---|---|
| GET | `/apps/{slug}/runs?limit=&offset=` | `{ slug, total, limit, offset, runs[] }` |
| GET | `/apps/{slug}/runs/{cid}/audit` | full audit for a decision |
| GET | `/apps/{slug}/loop-metrics` | decision-loop metrics |
| GET | `/apps/{slug}/self-learning` | current learning config/state |
| POST | `/apps/{slug}/self-learning` | update learning config |
| POST | `/apps/{slug}/items/{item_id}/feedback` | disposition an item finding: `{ modality: image\|document\|api\|case, task_type, decision: accept\|reject\|cancel, reason?, subject? }` → learning loop. **`reason` is REQUIRED on `reject`** (422 `reason_required`) and capped at **500 chars** (422 `reason_too_long`) — it becomes a learned rubric criterion. 409 `fraud_not_enabled` for a `case` item when fraud is off |
| POST | `/apps/{slug}/fraud-calibration` | read-only calibration report (`per_signal_hit_rate` — signal × officer-reject rate over past decisions); changes nothing. 409 `fraud_not_enabled` if fraud off |

## Auth

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/apps/{slug}/runtime/token` | `{ … }` | `{ token, … }` — short-lived scoped launch token |

**Access model:** a published app is readable/runnable by callers in its
**audience** (owner-SA / team / dept / org); drafts only by editors. A missing or
invalid JWT gets nothing (fail-closed). All the same checks the web runtime uses.
