---
name: citra-ui-charts
description: Canonical catalogue of chart types and dashboard KPI metrics a Citra app can render
metadata:
  category: citra
  tools: [bash]
---

# Citra UI — Charts & KPIs Catalogue

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift. Don't trust a remembered rule over the
> runtime you can read.


## Purpose

The single source of truth for **which chart shapes and KPI aggregations the
runtime can render**. Used alongside `citra-dashboard-spec` (which covers
dashboard-page composition + the narrator copilot); this skill is the precise
field-level vocabulary for `chart` panels and `dashboard` metrics.

> **Contract rule.** The runtime renders charts via one shared ECharts mapper.
> Only emit a `chart_type` listed here — an unknown type renders nothing.

## When to use

- Phase 3.5, whenever you emit a `chart` panel or a `dashboard` panel's
  `metrics[]`. Also when `citra-app-spec` auto-injects a chart over numeric data.

## Chart types (`chart.chart_type`)

| BA goal phrase | `chart_type` | Fields to set |
|---|---|---|
| "trend over time", "daily / weekly / monthly" | `line` | `x = <time column>`, `y = <numeric>`, `time_grain` |
| "compare X across categories" | `bar` | `x = <category>`, `y = <numeric>`, `aggregation = "sum"` |
| "stacked over time / by category" | `area` (or `bar`) + `stacked=true` | `x`, `y`, `group_by = <category>` |
| "share of", "breakdown by" | `pie` | `x = <category>`, `y = <numeric>` |
| **"pipeline", "stage conversion", "drop-off"** | `funnel` | `x = <stage label column>`, `y = <value column>` (auto-sorted descending) |
| **"correlation", "X vs Y", "scatter"** | `scatter` | `x = <numeric column>`, `y = <numeric column>`, optional `group_by` for series |

Common chart fields: `data_source`, `query` (filter predicate), `x`, `y`
(string or array of strings), `group_by`, `stacked`, `limit`, `time_grain`
(`minute`…`year`), `aggregation` (`count`/`sum`/`avg`/`min`/`max`).

> **`x` and `y` MUST be different columns.** `x` is the category/time axis; `y`
> is the column the `aggregation` rolls up. For `count`, `y` is the column being
> counted — use the **row id / primary key**, never the same column as `x`.
> Publish **rejects** a chart where `x == y` (rule **V-CHART-01**) — it renders a
> degenerate `y = x` diagonal. ⚠️ Easy to get wrong on a **count-over-time** chart:
> author it `x=<date>, y=<row id>, aggregation=count, time_grain=day` — NOT
> `x=<date>, y=<date>`.

> **Mind axis cardinality — a chart shows at most ~100 buckets.** The runtime caps
> a GROUP-BY chart at 100 groups (time-series keeps the most recent; categorical the
> top by value; the rest is truncated and the panel says so). So **never `x`/`group_by`
> a HIGH-CARDINALITY column** (an id, meter/account number, free-text, any per-record
> key) — the chart would show an arbitrary top-100 of thousands. Bin a big dimension
> instead: a `time_grain` for dates, a category roll-up, or a range bucket; and filter
> a large table first. Use the dataset's `row_count` from the catalogue to decide.

### `funnel`
`x` is the stage label, `y` the count/value at that stage. The renderer sorts
stages widest-to-narrowest automatically — row order doesn't matter.

### `scatter`
Both `x` and `y` are **numeric** columns; each row is a point. Use `group_by` to
split into colored series (e.g. by region). Good for "is amount correlated with
processing time?".

## Dashboard KPI metrics (`dashboard.metrics[]`)

Each metric: `name`, `agg` ∈ `count` / `sum` / `avg` / `min` / `max` / `ratio`,
optional `field`, `data_source`, `filter`, `label`, `window`, plus:

> **`ratio` — use it for any RATE / PERCENTAGE KPI** ("recovery rate", "approval %",
> "SLA-met %"). Do **not** substitute a `sum`/`count` and call it a rate — that ships
> a ₹/number tile under a "%" label. A ratio metric = `agg:"ratio"` + a `filter`
> selecting the **numerator** subset; the runtime computes *(rows matching `filter`) /
> (all rows in the `data_source`)* and the tile renders it as a **percent** (the value
> is a 0..1 fraction). Example — recovery rate = recovered cases / all cases:
> ```json
> { "name": "recovery_rate", "agg": "ratio", "data_source": "ds_theft_cases",
>   "filter": { "recovery_status": "recovered" }, "label": "Recovery Rate" }
> ```
> Every metric needs a `name` (the runtime keys tiles by it) and a `data_source`.

- `compare: {date_field, grain, periods}` — prior-period ▲/▼ delta chip.
- `trend: {date_field, grain, points}` — sparkline series.
- **`target`** (number) + optional **`thresholds`** (ascending fractions of
  target, e.g. `[0.5, 0.8]`) — renders a **progress-to-target bar** under the
  tile, banded red / amber / green. Use for SLA quotas, collection goals, etc.

```json
{ "name": "collected", "agg": "sum", "field": "amount", "label": "Recovered",
  "target": 5000000, "thresholds": [0.5, 0.8] }
```

## Not yet renderable — do NOT emit

On the roadmap; the runtime cannot render these yet:

- heatmap, treemap, radar charts

## Deliberately excluded

3D charts and arbitrary custom ECharts option pass-through are out of scope —
the spec stays declarative and safe, not a code-injection surface.
