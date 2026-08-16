---
name: citra-ui-panels
description: Canonical catalogue of panel types and detail-panel sections a Citra app can render
metadata:
  category: citra
  tools: [bash]
---

# Citra UI — Panels Catalogue

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift. Don't trust a remembered rule over the
> runtime you can read.


## Purpose

The single source of truth for **which panel types the runtime
(`citra-app-runtime`) can render**, and which **detail-panel sections** exist.
A page is a list of panels; each panel's `type` selects its renderer.

> **Contract rule.** The runtime **fails loud** on an unknown `panel.type`
> (visible error card + console error) — it never silently renders nothing.
> Only emit panel types listed here.

## When to use

- Phase 3 (`citra-app-ui-design`) — to place panels on pages in plain language.
- Phase 3.5 (`citra-app-spec`) — when emitting each `panels[]` entry.

## Panel types

| `type` | Use for | Key fields |
|---|---|---|
| `form` | **Officer-facing internal** data entry (binds to `agent.input_schema` or `schema_inline`). Never a citizen/public submission form. | `schema_ref` \| `schema_inline`, `on_submit` |
| `queue` | List existing instances, filter by status, drill in. | `data_source`, `columns`, `actions`, `view` |
| `detail` | Per-record drill-down. Linked to a queue. | `linked_to`, `sections[]` |
| `dashboard` | KPI cards (count / sum / avg / min / max / ratio). | `metrics[]` — see `citra-ui-charts` |
| `chart` | Visual chart over a tabular data source. | `chart_type`, `x`, `y` — see `citra-ui-charts` |
| `agent_chat` | Free-form chat with the root agent or a named sub-agent. | `agent_role`, `starter_prompts` |
| `document_view` | Browse a document library (`static` or RAG-backed). | `data_source`, `doc_types` |
| `markdown` | Static instructional content. | `content` |
| `notice` | **Static callout band** — info / warning / error / success. Surface an SLA caveat, a "what to check" note, or a procedural warning at the top of a page. No data binding. | `tone` (`info`\|`warn`\|`error`\|`success`), `content`, optional `title` |
| `calendar` | **Month-grid calendar** of records with a date column — due dates, inspections, shifts. Read-only. | `data_source`, `date_field`, `title_field`, optional `color_field`, `limit` |
| `map` | **Geospatial marker map** (Leaflet / OpenStreetMap) of records with lat/lng columns — meters, sites, incidents. Read-only. | `data_source`, `lat_field`, `lng_field`, optional `label_field`, `limit` |
| `filter_bar` | **Interactive page filter** — a strip of controls bound to page params. Picking a value re-queries *every* panel that references `{param.<name>}` in its data_source `filters`. Allowed on dashboard **and** standard pages. View-only — never writes. | `controls[]` — each `{param, label, options, default?, all_label?, control_type?}` |
| `notifications` | **Notification centre** — a read-only list of attention items aggregated from one or more builder-defined `feeds`. Each feed is ANY `data_source` + ANY `filters` condition (or the built-in `approvals` inbox), with its own label/tone and click-through. Good on a home/dashboard page. | `feeds[]` — each `{label, kind?, source?, filters?, title_field?, sub_field?, tone?, navigate?}` |
| `hero` | **Page-header band** — icon + headline + optional live metric + ≤2 NAVIGATION actions. One per page, FIRST panel; it suppresses the plain page title. Agent actions are rejected here (a hero is chrome, not a work surface). Allowed on dashboard pages. | `headline`, `subtitle?`, `icon?`, `metric?` (one `DashboardMetric`), `actions[]` (navigate-only) |
| `stat_strip` | **Compact KPI band** with delta arrows + sparklines — 2-6 metrics in one dense strip. Give metrics `compare`/`trend` or the strip is just small numbers. Allowed on dashboard pages; one per page reads best. | `metrics[]` (2-6, see `citra-ui-charts`) |
| `timeline` | **Vertical event feed** over any tabular source — case histories, decision ledgers, outage logs. Newest first by `date_field`. Read-only. | `data_source`, `date_field`, `title_field`, `subtitle_field?`, `icon_field?`, `badge_field?`+`badge_colors?`, `limit?` |

## Icons (closed vocabulary — publish rule I-01)

`page.icon`, `panel.icon`, `metric.icon`, `action.icon` and `section.icon` all
take a **kebab-case lucide name from the platform's closed set** (`ICON_NAMES`
in the runtime models — ~110 ops-relevant names such as `inbox`, `banknote`,
`shield-check`, `map-pin`, `clipboard-list`, `trending-up`, `zap`, `truck`,
`gauge`, `file-check`, `users`, `alert-triangle`, `timer`, `wallet`,
`plug-zap`, `siren`, `scale`, `landmark`). Publish REJECTS any other name —
never invent one. Give every nav page an icon; KPI tiles auto-pick a sensible
icon when you omit `metric.icon` (money → banknote, dates → calendar).

## Queue + detail presentation upgrades

- `queue.view: "split"` — master-detail two-pane (list left, full record +
  actions right). The modern triage layout; pick it when officers work rows
  one-by-one without leaving the page.
- `queue.badge_colors` — SEMANTIC colors for `badge_column` values, e.g.
  `{"pending": "amber", "recovered": "green", "written_off": "red"}`. Allowed
  values: `green | amber | red | blue | slate` — never hex. Keep the meaning
  consistent app-wide (green = good/terminal-good, amber = waiting, red =
  breach/terminal-bad).
- `queue.secondary_columns` — metadata rendered small + muted after the main
  fields (reference numbers, timestamps) so it's present but not competing.
- `queue.column_formats` — per-column display: `status_pill` (colored pill via
  badge_colors), `currency` (theme-locale money), `relative_time` ("2 days
  ago"), `progress` (0-1 or 0-100 bar), `grade` (the scorecard chip — only on a
  `workflow_staging` queue in an app that declares `factor_set`; the rows carry
  flat `grade` / `score_percent` / `gated` columns. An empty value renders
  "gated" rather than blank, because a case that failed a hard policy gate has
  no grade and must not read as one still being scored. Ranking a queue this way
  needs PRECOMPUTED rows — a grade that only exists once someone opens the case
  cannot sort a portfolio. See `citra-app-spec` → `references/factor-set.md`).
- `detail.layout: "profile"` — tops the record page with a header card:
  `header_fields` (2-5 key facts, rendered large) + `status_field` with
  `status_colors` (same semantic names). Use for case/consumer records where
  the officer needs the vitals before the sections.

### `notifications` panel

A `notifications` panel surfaces what an officer must act on, in one place —
without hunting through queues. It is **fully generic**: there is no fixed kind
of notification. You declare one or more `feeds`, and each feed becomes a list of
attention items. A feed is either:

- **a data_source feed** (`kind: "data_source"`, the default): rows of `source`
  matching `filters` — overdue, flagged, high-value, awaiting-review, anything.
- **the approvals feed** (`kind: "approvals"`): the dept's pending
  recommendations the caller can act on (the reviewer inbox). No `source`/
  `filters` needed — it reads the platform inbox.

**Derive every field from the dataset catalogue — never hardcode column names.**
There are NO standard/reserved columns; bind each to the dataset's ACTUAL columns:
- `source` → the id of a `data_source` you declared.
- `filters` → any predicate selecting the notable rows, columns from the
  catalogue. Mongo-style operators work (`{col:{"$lt":x}}`, `{"$gte"}`,
  `{"$in":[…]}`, bare equality). For **time-relative** conditions use the tokens
  `{now}`, `{now-<N><unit>}`, `{now+<N><unit>}` (unit = `s|m|h|d|w`) — e.g.
  overdue = `{<due col>: {"$lt": "{now}"}}`, stale > 48h =
  `{<opened col>: {"$lt": "{now-48h}"}}`, due within 7d =
  `{<due col>: {"$lt": "{now+7d}"}}`.
- `title_field` / `sub_field` → catalogue columns for each item's title / subline.
- `tone` → `info | success | warning | danger | neutral` (badge colour).
- `navigate.params` → templated per item via `{row.<column>}` (a real column).

Example — every id and column below is a **placeholder**; replace with real ones
from your data_sources and the dataset catalogue:

```json
{ "id": "inbox", "type": "notifications", "title": "Needs attention",
  "feeds": [
    { "label": "Overdue", "tone": "danger",
      "source": "<a data_source id>",
      "filters": { "<a due date column>": { "$lt": "{now}" } },
      "title_field": "<a record id/reference column>",
      "sub_field": "<a column to show as the subline>",
      "navigate": { "page": "<a detail page id>",
                    "params": { "id": "{row.<the record id column>}" } } },
    { "label": "Approvals", "kind": "approvals", "tone": "warning",
      "navigate": { "page": "<a review page id>",
                    "params": { "id": "{row.case_natural_key}" } } }
  ] }
```

### `filter_bar` panel

Use a `filter_bar` to make a dashboard (or any page) interactively filterable —
e.g. "show me Patna" on a multi-district command dashboard, without a separate
form page. It is the declarative way to wire a control to the whole page; **do
not** ask for custom JavaScript for this.

**How the wiring works (two halves — both required):**

1. The `filter_bar` control sets a URL query param on change (`?district=Patna`),
   which re-renders the page so dependent panels re-query.
2. Each panel you want filtered must reference that param in **its
   `data_source.filters`** as `{param.<name>}`. An *unselected* control (the
   "All" option) **drops** that filter condition — you see every row, never an
   empty match.

Control `options` reuse the same contract as a form combo (`OptionsSource`):
a `static` list, or live DISTINCT values from a `data_source`
(`{kind:"data_source", data_source, value_column, label_column?}`).

```json
// panel — put this at the top of the dashboard page
{ "id": "filters", "type": "filter_bar", "controls": [
  { "param": "district", "label": "District", "all_label": "All districts",
    "options": { "kind": "data_source", "data_source": "ds_consumers",
                 "value_column": "district" } }
]}

// a data_source the charts/KPIs read — the {param.district} makes it reactive
{ "id": "ds_consumers", "type": "mcp", "ref": "field_operations",
  "filters": { "district": "{param.district}" } }
```

Only `dropdown` (the default `control_type`) is fully live today; `daterange`
renders a date input that sets the param, and `segment` renders as a dropdown.

### Edit-an-existing-record form (`form` with `mode: "edit"`)

A plain `form` creates a NEW record. To let a user EDIT an existing one — load
its current values, change a few, save back — set `mode: "edit"` and bind it to
the record:

- `prefill_source` — the data_source id to read the current record from.
- `key_field` — the record's identifier column, **derived from the dataset
  catalogue** (whatever the key column is called there — there is no fixed name).

The form's `properties` are likewise the dataset's real editable columns from
the catalogue, not invented names. Place the edit form on a page reached with
`?id=<record key>` (typically an "Edit" `tool_button` / `navigate` from a queue
row or detail page). The runtime fetches the record by `key_field == ?id`, seeds
every field's value, and on submit re-sends `key_field` so `on_submit` writes an
**UPDATE** to that record (governed: validated + audited like any write), not a
new row. Use this for basic CRUD edits the officer makes directly, outside the
agent recommend→approve loop.

Example — every id and column below is a **placeholder**; replace with the real
data_source id, key column, editable columns, and write tool from your spec and
the dataset catalogue:

```json
{ "id": "edit_record", "type": "form", "mode": "edit",
  "prefill_source": "<your data_source id>",
  "key_field": "<the record key column from the catalogue>",
  "schema_inline": { "type": "object",
    "properties": { "<an editable column>": {"type":"string"},
                    "<another editable column>": {"type":"string"} } },
  "on_submit": { "tool_name": "<your update write tool>" } }
```

### `notice` panel

A `notice` is the right tool whenever the design calls for a banner, callout,
alert, or instructional strip that isn't a full markdown block. It carries no
data — for a **live count** ("3 cases breach SLA") use a `dashboard` KPI tile
instead. `content` is plain text / inline markdown (rendered safely; no raw
HTML). Example:

```json
{ "id": "sla_warn", "type": "notice", "tone": "warn",
  "title": "Check before approving",
  "content": "Verify the meter reading photo matches the claimed units." }
```

### `queue` as a kanban board

A `queue` panel set to `view: "kanban"` groups its rows into board columns by a
`group_by` column (e.g. status). `group_by` is required for kanban. The same
cards render; the board is one of the three queue views (cards / table /
kanban) the officer can toggle.

```json
{ "id": "cases", "type": "queue", "data_source": "ds",
  "view": "kanban", "group_by": "status",
  "columns": ["case_id", "applicant", "status"] }
```

## Detail-panel sections

A `detail` panel's `sections[]` each have a `type`:

| section `type` | renders |
|---|---|
| `fields` | the record's field values. Omit `fields` to show every column, or set `fields: [...]` for a subset. |
| `attachment` | file/blob columns on the record — inline image preview for images, download link otherwise. Set `fields: [...]` to pick which file columns. |
| `markdown` | static guidance — set `content`. |
| `agent_timeline` | the audit trail of agent runs on this record (decision, reasoning, model). Read-only. |
| `comments` | human notes officers add to this record — a free-text thread (oldest-first) with an "Add note" box. App-local overlay, never the SoR; distinct from the read-only `agent_timeline`. No config needed. |
| `documents` | reference documents — set `data_source`. |
| `approval` | pending-approval runs with Approve / Reject / Cancel — set `roles` to approver role ids. |
| `agent_chat` | a chat with the app's agent — optionally set `agent_role`. |

Any section can set `"collapsible": true` (and `"collapsed": true` to start
closed) to render inside an accordion disclosure — useful to tuck secondary
sections away on a long detail page.

## Deliberately excluded (will not be added)

Raw-HTML embed panels and arbitrary iframe panels are out of scope (XSS +
governance). Surface such needs in `requirements_unmet`.
