<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# UI Component Expansion Plan â€” scalable, spec-driven, skill-loaded

**Status:** in progress (started 2026-06-03)

## Progress (2026-06-03) â€” COMPLETE

All buckets shipped & verified: runtime `tsc --noEmit` clean + `next build` green; `models.py`/`main.py`/`panel_data.py` import; schema valid both ways (jsonschema + Pydantic); drift test + 17 positive round-trip tests green; **zero regressions** (full suite baseline 25-fail == post-change 25-fail â€” the 25 are pre-existing branch/e2e failures).

**Stage 0 / Stage 1 / M-A** â€” as previously listed, all done.

**M-B** â€” `notice` callout panel; `funnel` + `scatter` charts; gauge / progress-to-target KPI (`DashboardMetric.target`+`thresholds` â†’ `KpiProgress`); typeahead (`options_source.search` + `/field-options?q=` LIKE-pushdown + `TypeaheadFormSelect`); `calendar` month-grid panel.

**M-C** â€” kanban (`QueuePanel.view:"kanban"` + `group_by`); stepper/wizard form (`FormPanel.steps`); accordion detail sections (`collapsible`/`collapsed`); read-only fields (`format:"readonly"`); `map` panel (Leaflet `react-leaflet@4` + `LeafletMap.tsx`, ssr:false).

**Stage 5** â€” `test_schema_model_drift.py` (panel types + chart_type + detail-section types) and `test_new_ui_components.py` (17 publish-validation round-trips). Safety exclusions documented in the catalogue skills. The catalogue skills' "Not yet renderable" lists were trimmed to only the genuinely-deferred items (heatmap/treemap/radar charts).

### Drift bugs fixed en route (runtime supported these but schema/model rejected them)
- `DetailSection` enum was missing `attachment` (documented + rendered, but JSON schema rejected it).
- Form `on_submit` JSON schema rejected the `tool_name` direct-write path.
- `QueuePanel` presentation fields (`view`/`page_size`/`filters`/`badge_column`/â€¦) existed in the runtime but neither `models.py` (extra="forbid") nor the JSON schema allowed them.

### Follow-ups (optional, not blocking)
- `react-leaflet@5` needs React 19; pinned to `@4` for React 18. Revisit on a React upgrade.
- Per-component column-existence validators were intentionally NOT added â€” charts have none either; the publish smoke-gate covers bad column refs at the same bar.

**Goal:** Enrich the set of UI components a SmartApp can render, exposed to the OpenClaw
builder as **skills** so the system scales â€” OpenClaw composes any operational app and the
runtime can render it.

## Core principle â€” every component is a 3-layer contract

A skill alone adds nothing renderable. Each component moves three layers together:

```
SCHEMA (models.py â†’ app_spec.schema.json)  â†’  RUNTIME (PanelRenderer/chartToEcharts/components)  â†’  SKILL (SKILL.md + AGENTS.md)
        grammar + publish validation               rendering                                          builder vocabulary
```

The scalability lever: keep the **runtime renderer generic** and the **catalogue skills the
single declarative source**, so a new widget = one schema enum/model + one render branch + one
row in a catalogue skill. OpenClaw picks it up automatically.

## Key files

- Schema: `smart-app-service/models.py` (Panel models ~1008â€“1245, `ControlType` 139, `ChartPanel` 1181, `DashboardMetric`).
- Generated schema: `smart-app-service/schemas/app_spec.schema.json`.
- Validators: `smart-app-service/publish_validators.py`.
- Runtime types: `citra-app-runtime/src/types/spec.ts`.
- Runtime render: `citra-app-runtime/src/components/PanelRenderer.tsx` (`fieldControl()` ~342), `src/lib/chartToEcharts.ts`.
- Field-options endpoint: `smart-app-service` (`/apps/{slug}/field-options`).
- Skills: `smart-app-service/skills/citra-*/SKILL.md`; index `smart-app-service/builder-workspace/AGENTS.md`.
- Builder image: `smart-app-service/builder-sandbox/Dockerfile` (per-skill COPY 61â€“76).

## Stage 0 â€” Foundation (pluggability)

- **0.1** Wildcard skill COPY in the builder Dockerfile (replace 18 explicit COPY lines).
- **0.2** `scripts/gen_schemas.py` â€” regenerate `app_spec.schema.json` from `models.py`.
- **0.3** Fail-loud renderer: unknown `panel.type` / `control` / `chart_type` â†’ visible error
  placeholder + `console.error`, never silent degrade (RULE #1).
- **0.4** Stand up the 3 catalogue skills (Stage 1).

## Stage 1 â€” Skill taxonomy (declarative layer)

Three read-on-demand catalogue skills (peers of `citra-tool-catalogue`):

- **`citra-ui-fields`** â€” form control â†’ JSON-Schema hint mapping (extracted from `citra-app-spec`).
- **`citra-ui-panels`** â€” panel types + detail-section types (extracted from `citra-app-spec`).
- **`citra-ui-charts`** â€” chart types + KPI/gauge metrics (extends `citra-dashboard-spec`).

`citra-app-spec` becomes a thin translator that defers component vocabulary to these. Register
all three in `AGENTS.md` Available-skills + Phase 3/3.5 rows.

## Capability backlog (all buckets)

### M-A â€” Free wins
- `attachment` detail section â€” exists in schema+runtime; document in `citra-ui-panels`.
- `currency` / `time` form controls â€” add `fieldControl()` branches; doc in `citra-ui-fields`.
- `lookup` â†’ typeahead (M-B); `toggle` â†’ switch render. Resolve dead `ControlType` values.

### M-B â€” High value
- **Typeahead / autocomplete** (real `lookup`): `options_source.search`, debounced `?q=` on
  `/field-options`, `<Combobox>` runtime component.
- **Notice / banner panel**: `NoticePanel` (`tone: info|warn|error|success`, optional count source).
- **Gauge / progress-to-target KPI**: `DashboardMetric.target` + `thresholds`, ECharts gauge.
- **Charts**: add `stacked bar`, `funnel`, `scatter` to `ChartPanel.chart_type` + `chartToEcharts`.
- **Calendar / schedule panel**: `CalendarPanel` (date field, title, color).

### M-C â€” Situational
- **Kanban board**: `QueuePanel.view:"kanban"` + `group_by`, column grouping render.
- **Stepper / wizard form**: `FormPanel.steps[]`, multi-step render.
- **Accordion detail sections**: `DetailSection.collapsible`.
- **Read-only / computed fields**: `format:"readonly"` / `compute`.
- **Map / geo panel**: `MapPanel` (lat/lng columns) â€” heaviest; map lib.

## Stage 5 â€” Validation & safety (per capability)
- A `publish_validators.py` rule per new construct (enum bounds, required backing columns) â€”
  fail at publish, not render.
- Explicitly **exclude** rich-text/WYSIWYG, raw HTML embed, color picker (XSS + scope).
- Each capability ships a `citra-self-test` case; smoke-gate renders against live data.

## Sequencing
1. Stage 0 + Stage 1 (foundation + catalogues; verify a build still works).
2. M-A â†’ M-B â†’ M-C, each as schema â†’ runtime â†’ skill â†’ validator â†’ self-test slices.
