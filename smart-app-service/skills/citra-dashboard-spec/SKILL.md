---
name: citra-dashboard-spec
description: Author and validate the AppSpec dashboard PAGE + narrator AgentSpec for an AI-narrated dashboard (kind="app", page.kind="dashboard")
metadata:
  category: citra
  tools: [bash]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra Smart Dashboard Spec

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift. Don't trust a remembered rule over the
> runtime you can read.


## Purpose

Author a **dashboard PAGE** (`page.kind="dashboard"`) inside a `kind="app"`
AppSpec, plus the **narrator AgentSpec**. A dashboard is not a separate
artefact — it is an app whose primary page is a dashboard page. **Every
dashboard page is AI-narrated**: charts and KPIs answer the *what*; the
narrator answers the *why*, *what changed*, and *what to do*.

The dashboard page has two parts:

1. **Charts + KPI tiles** — bound to dept-MCP catalogue datasets, rendered
   **natively as ECharts** by the runtime (no Superset, no embed, no guest token).
2. **The hero-brief copilot** — an automatic full-width band the runtime
   renders at the top of any `page.kind="dashboard"` when the app declares an
   `agent_id`. It auto-briefs on load and expands to a full copilot on demand.
   The builder authors the narrator agent but **does not place or size this
   band** — the runtime owns it.

   > **Headline figures: the narrator computes them — do NOT hardcode them.** The
   > narrator is a free-form agent (it behaves like the main chat). For any
   > total/count/sum it runs ONE aggregate query (`COUNT`/`SUM`) with the right
   > filter and quotes that — never tallies the capped row sample its tools
   > return. To make its numbers match the KPI tiles *by construction*, give it a
   > short **canonical-definitions** list in `system_prompt` (e.g. `Active outages
   > = outages.status='active'` — the records table, NOT `feeders`). Keep the rest
   > of the prompt LEAN — role + interpretation (what changed, why, what to do) —
   > not 2k chars of SOP boilerplate (that buries the definitions and the model
   > drifts; defer methodology to the policy-library RAG tool).
   >
   > *(There was a runtime "authoritative figures" injection that fed the tile
   > aggregates into the brief turn. It is now **OFF by default**
   > (`DASHBOARD_GROUND_TRUTH_INJECT`) — it overrode the question's framing and
   > steered the narrator off its own definitions. Do NOT author prompts that
   > assume it; make the narrator self-sufficient via aggregates + definitions.)*

**Any number of pages, any mix.** An app can carry multiple dashboard pages
(e.g. "Operations", "Finance", "Compliance" — one `page.kind="dashboard"`
entry each, with its own `id`/`title`/panels) alongside standard pages
(queues, documents, forms). Each dashboard page gets its own page-scoped hero
brief; **one shared narrator** (`app_spec.agent_id`) powers all of them. Put
only KPI/chart/markdown panels on a dashboard page; everything else goes on a
standard page (authored via `citra-app-spec`).

## When to Use

- Phase 3.5 of an App build session whose env var
  `BUILD_PRIMARY_PAGE_KIND=dashboard`.
- Edit flows for an already-published app whose primary page is a dashboard
  page (`/apps/{slug}/edit`; the edit request carries
  `BUILD_PRIMARY_PAGE_KIND=dashboard`).
- For standard (non-dashboard) pages, use `citra-app-spec` instead. A
  multi-page app uses both: this skill for the dashboard page,
  `citra-app-spec` for the standard pages.

## Hard Rules

- The build produces `app_spec.json` (`kind="app"`, with a `pages[]` entry
  whose `kind="dashboard"`) AND `agent_spec.json` (the narrator).
- `agent_id` is **required** at the top of the AppSpec — points at the
  narrator AgentSpec. The runtime turns this `agent_id` into the automatic
  hero-brief copilot band on the dashboard page; you don't author a panel
  for it. (The hero brief runs the agent in read-only chat_mode, so a
  read-only narrator is ideal but the app's action agent also works.)
- Allowed panel types **on the dashboard page**: **`chart`**, **`dashboard`**
  (KPIs), and **`markdown`** (a written brief). No `agent_chat` panel — the
  copilot is the automatic hero-brief band, not a panel you place. No
  `form` / `queue` / `detail` / `document_view` — put those on a **standard**
  page (`citra-app-spec`).
- **Spec/render separation:** chart panels render natively as ECharts in
  the runtime. The builder emits `chart_type` + data fields only; it MUST
  NEVER emit colours, palettes, sizes, or styling — the runtime's
  executive theme owns all of that.
- A data source ref must point at a `data_sources[]` entry of `type="mcp"` whose
  `ref` is the catalogue `dataset_id` verbatim — **dot-qualified
  `source_id.dataset_id`**, never a slash (a `/` is rejected at publish as
  `E_MALFORMED_DATASET_REF`).
- **WHERE the data source ref goes differs by panel type:** a `chart` panel has a
  **panel-level** `data_source`. A `dashboard` (KPI) panel does **NOT** — it has
  `additionalProperties:false` and **no** panel-level `data_source`; put the ref
  on **each metric** instead (`metrics[].data_source`), since different tiles can
  read different datasets. A `data_source` at the dashboard-panel level is
  rejected by the schema ("not valid under any of the given schemas").
- Datasets must be **SQL-backed** (`read_via.kind == "sql"` in the
  catalogue). Non-SQL sources go in `requirements_unmet[]` with a note.
- **No personal-vault data**, no user-uploaded files, no `static`
  data sources. Tenant catalogue only.
- The narrator agent's `tools_v2[]` must include **at least one `mcp`
  tool** for each catalogue dataset bound to a chart panel — so the
  narrator can re-query live data for "why" / drill-down questions.
- Validate against `app_spec.schema.json` AND `agent_spec.schema.json`
  (both seeded at `/workspace/.openclaw/workspace/schemas/`) before saving.

## Safety rules (citations)

- **D-02** — A dashboard page requires the app to declare `agent_id` (the hero-brief narrator). The publish validator rejects a `page.kind="dashboard"` with no narrator. Read-only safety is enforced at runtime: the hero brief always runs the agent in chat_mode (write tools stripped), so a dashboard page is safe even when the app's agent carries write tools for its action pages. **Keep the narrator read-only where you can** — a dashboard reads, it does not write.
- **D-03** — `data_sources` cannot reference personal vault, user-uploaded files. Tenant catalogue datasets only; the narrator answers *why* against the same numbers the BA sees on the chart. (D-01 = dashboard data sources must be read-only `mcp`/`rag`.)
- **D-04** — When a referenced dataset has no RAG binding (no semantic source), the narrator cannot ground its "why" answers in commentary. Surface this in `requirements_unmet` so the BA understands the narrator will be metric-only for that tile.

Refer to [citra-safety-rules](../citra-safety-rules/SKILL.md) for the canonical rule list.

## Step 0 — Load capabilities

Pass the BA's goal as `question` so the catalogue is reranked by relevance:

```
GET {SMART_APP_SERVICE_URL}/builder/catalogue?question=<URL-encoded BA goal>
GET {SMART_APP_SERVICE_URL}/capabilities?question=<...>
```

`/builder/catalogue` returns at most 50 datasets, RBAC-filtered then
reranked. If `needs_scope=true`, ask the BA to narrow by `source_id`
before continuing.

## Step 1 — Pick datasets

From the catalogue response:
1. Filter to entries where `read_via.kind == "sql"`. Reject non-SQL into
   `requirements_unmet[]` with a clear note.
2. Pick **4–8 datasets** that match the goal. Bias toward those with
   time columns, numeric measures, and clear dimensions
   (region/status/category).
3. For each picked dataset, add a `data_sources[]` entry:

```jsonc
{
  "id": "claims",
  "type": "mcp",
  "ref": "<source_id>.<dataset_id>",
  "filters": {}
}
```

## Step 2 — Design tiles (charts + KPIs)

Aim for **4–10 chart panels** plus optional KPI cards.

A `chart` panel carries **data fields only** — `chart_type`, `data_source`, `query`,
`x`, `y`, `group_by`, `stacked`, `limit`, `time_grain`, `aggregation` (the full chart
catalogue with `chart_type` values and per-type field rules lives in **`citra-ui-charts`**).
There is no `time_range` field (use `time_grain` for bucketing + `query` to scope rows),
and you NEVER emit styling (`color`/`palette`/`width`/`height`) — the runtime's executive
theme owns the look.

**ALWAYS set `aggregation` on a chart.** The chart is **pure display** — it does
NO math. The aggregate is computed at the **source** (a real `GROUP BY` pushed to
SQL, exactly like a KPI metric): `aggregation` (`count`/`sum`/`avg`/`min`/`max`)
rolls up `y` per `x` (and per `group_by`). Without it, the chart would plot raw
capped rows and be silently wrong. So: "complaints per day by category" →
`x=registered_at, y=complaint_id, aggregation=count, group_by=category`. The
COUNT/SUM is the y-value the chart plots.
- **`time_grain` is for REAL date/timestamp `x` columns only** (`registered_at`,
  `start_time` → `date_trunc(grain, x)`). Do **NOT** set `time_grain` on a column
  that is ALREADY a period string (e.g. `billing_period = "2026-01"`) — bucketing a
  string fails and the chart comes back empty. For a pre-bucketed period column,
  leave `time_grain` unset and just group on the column as-is.

| BA goal phrase | Chart shape | Fields to set |
|---|---|---|
| "trend over time", "daily / weekly / monthly" | `chart_type="line"` | `x = <time column>`, `y = <numeric>`, `time_grain` |
| "compare X across categories" | `chart_type="bar"` | `x = <category>`, `y = <numeric>`, `aggregation = "sum"` |
| "share of", "breakdown by" | `chart_type="pie"` | `x = <category>`, `y = <numeric>` |
| "stacked over time" | `chart_type="area"` + `stacked=true` | line + `group_by = <category>` |
| "pipeline", "stage conversion", "drop-off" | `chart_type="funnel"` | `x = <stage label>`, `y = <value>` (auto-sorted) |
| "correlation", "X vs Y" | `chart_type="scatter"` | `x = <numeric>`, `y = <numeric>`, optional `group_by` |
| "total / count / average of X" | `dashboard` panel KPI | `metrics[]` with `agg` and `field` |

Place a `dashboard` panel of 3–5 KPI cards at the top, then chart panels
below. Avoid more than one `dashboard` panel.

### Rich KPI tiles — `filter` / `compare` / `trend` / `label`

A KPI metric is a **source-side aggregate** over the WHOLE table (true `COUNT(*)`/`SUM`, never a capped row count). Hard rules for every tile:
- **Always set `label`** (a short clean subtitle) — without it the tile shows the raw agg + field id.
- **LABEL-HONESTY** — if the tile name qualifies the count (*urgent / breached / overdue / unacknowledged / pending / today / this-week / at-risk*), the metric MUST carry a `filter` predicate that makes the number that subset. **Never reuse another tile's `data_source` for a narrower label without adding the distinguishing `filter`** — two differently-labelled tiles showing the same number is a "made-up number" the BA will catch. Always filter a "open/active/pending" KPI.
- **Add `trend` to EVERY `count`/`sum` KPI** whose dataset has a usable date column (a sparkline at no extra cost); omit only when there's genuinely no date column (note it in `requirements_unmet`).

The full field shapes (`filter` operators, `compare`, `trend`), the DB-side **relative-date tokens** (`today`/`this_week`/`24h`/`7d`…), and a worked example are in **`references/kpi-tiles.md`** — read it when wiring these fields.

- **NEVER invent a column name.** Every `field`, `filter` key, `compare`/
  `trend` `date_field`, chart `x`/`y`/`group_by`, and queue column MUST be an **exact**
  column from that dataset's `/builder/catalogue` entry — copied verbatim, not
  guessed or paraphrased (e.g. don't write `previous_arrears_balance` when the
  catalogue says `arrears_carried`). **Publish now hard-rejects** any panel
  column not in the catalogue (`E_UNKNOWN_PANEL_COLUMN`), so a fabricated
  column blocks the whole app — confirm the real name first.
- **Do not add a tile/metric on a dataset whose columns you haven't fetched.**
  If you want a KPI from a source you haven't inspected, pull its catalogue
  entry first and bind to a real column — or leave the tile out.

## Step 3 — ALWAYS author a narrator agent (the canonical patterns)

This is the part that makes a dashboard SMART. The narrator surfaces as the runtime's automatic **hero-brief copilot** (top band, briefs on open, expands to chat) — you author the agent, the runtime renders the band. Author an `AgentSpec` that handles the canonical use cases; every dashboard ships with some subset, but **at minimum implement Pattern 1 (Brief me)**.

The five canonical patterns are: **(1) "Brief me"** executive narrative *(always)*, **(2) "Why did X drop/spike?"** Q&A *(always)*, **(3) anomaly auto-tagging** *(time columns)*, **(4) NL filter / drill-down** *("show me only X")*, **(5) "Show me a chart"** *(inline ` ```chart ` blocks in the copilot)*. Each pattern's **system-prompt extract** plus the full **AgentSpec shape** (`model_tier`, one `mcp` tool per bound dataset, optional `code_exec`) are in **`references/narrator-patterns.md`** — read it when writing the narrator's `system_prompt` and `tools_v2`. Spec/render rule applies to Pattern 5 too: the narrator picks `chart_type` + fields only, never colours/sizes.

## Step 4 — Do NOT author a copilot panel — the hero-brief is automatic

The copilot is **not** a panel you place. Because the AppSpec declares an
`agent_id` (the narrator), the runtime automatically renders a full-width
**hero-brief band** at the top of the dashboard: it auto-briefs the BA on
load and expands into the full conversational copilot on demand. You do
not place it, size it, or add an `agent_chat` panel — the runtime owns the
band.

So the dashboard's `panels[]` are just the **KPI `dashboard` panel + the
`chart` panels** from Step 2. Author the narrator agent (Step 3), set
`app_spec.agent_id` to it, and stop there. The starter questions and
auto-brief behaviour come from the narrator's system prompt, not from a
panel.

## Step 5 — Confirm with the BA in plain language

Translate the dashboard into ONE BA-facing paragraph. Mention the
narrator explicitly. Example:

> "I'll build a dashboard with three KPI cards (open claims, average
> amount, approval rate), a line chart of daily claim volume, a bar
> chart of claim count by branch, and a pie of claims by status. The
> narrator agent at the top will brief you on what changed each time
> you open the dashboard, answer 'why did X drop?' questions, and let
> you filter with plain English. Want anything added or removed?"

## Step 6 — Write both spec files

Save to `~/workspace/build/`:
- `app_spec.json` — kind="app" with a dashboard page, agent_id → narrator
- `agent_spec.json` — the narrator AgentSpec

Required `app_spec.json` shape (a pure dashboard = one dashboard page):

```jsonc
{
  "spec_version": "v0",
  "kind": "app",
  "agent_id": "<slug>-narrator",          // REQUIRED (powers the hero brief)
  "slug": "<lowercase-hyphenated>",
  "title": "<BA-facing title>",
  "description": "<one-line summary>",
  "owner": "<from CITRA_USER_ID>",
  "data_sources": [ /* Step 1 entries */ ],
  "pages": [
    {
      "id": "overview",
      "path": "/",
      "title": "<page title>",
      "kind": "dashboard",                 // ← the executive treatment
      "panels": [
        /* dashboard (KPI) panel from Step 2, FIRST */
        /* chart panels from Step 2, after */
        /* optional markdown brief; NO agent_chat — hero brief is automatic */
      ]
    }
    /* For a multi-page info app, add more pages here with kind:"standard"
       (queues / documents / forms / assistant) authored via citra-app-spec. */
  ],
  "permissions": { "view": ["user"] },
  "requirements_unmet": [ /* non-SQL datasets, etc. */ ]
}
```

## Step 7 — Validate the spec (`citra_spec_validate`)

The pod can't `import validators`/`models` (neither ships into the sandbox), so call
the **`citra_spec_validate` tool** — it runs the **exact same JSON-Schema + Pydantic
two-layer check `/publish` runs** (catching cross-references JSON Schema alone can't:
dashboard page missing its `agent_id` narrator, dangling `navigate.page`, etc.)
without persisting anything:

```json
{"tool": "citra_spec_validate",
 "args": {"app_spec": <the AppSpec object>, "agent_spec": <the AgentSpec object>}}
```

`{"passed": true}` → publish won't reject on spec-shape grounds. `{"passed": false,
"errors": …}` is the same failure `/publish` would return — fix the spec and
re-validate now. **Never proceed to Step 8 without `passed:true` here.**

## Step 8 — Publish

Hand over to `citra-app-publish`. The publish endpoint will:
1. Re-validate both specs.
2. Persist the AgentSpec for the narrator.
3. Return the dashboard URL — relay it to the BA.

The runtime renders the KPI + chart panels natively as **ECharts** under
its executive theme — no Superset, no embed, no guest tokens. It also
renders the automatic hero-brief copilot band from the `agent_id`. The
narrator answers copilot questions and emits inline ` ```chart ` blocks
(Pattern 5) that the runtime parses and draws as ECharts inside the
copilot.

## What you DO NOT do

- **Do not author a data job / ETL / nightly aggregation.** Computation happens at
  narration time via the agent's `mcp` + `code_exec` tools. You cannot write computed
  data back to source systems (SAP/CRM are read-only); a nightly ETL or ML-scoring job
  is an IT/platform concern, not part of the SmartApp build.
- **Do not skip the narrator.** Every dashboard gets one. If the BA
  insists on a "just charts" dashboard, you may give it the minimal
  narrator (Pattern 1 only — brief me) but you MUST author one.

## Edits

`/apps/{slug}/edit` re-spawns this skill with `SEED_APP_SPEC` and
`SEED_AGENT_SPEC` set. Make the BA-requested change, re-validate,
re-publish — the AppSpec/AgentSpec are replaced in place.
