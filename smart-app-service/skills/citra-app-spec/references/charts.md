<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# citra-app-spec — Auto-injected charts (shape selection & placement)

The **core rule** (always apply it) lives in `SKILL.md`: inject ≥1 `chart`
panel whenever the data is numeric. This file is the detail on *which* chart
shape to pick and *where* to place it. See also `citra-ui-charts` for the full
chart-type + KPI-metric catalogue.

## When you MUST inject a chart
Inject at least one `chart` panel — even if the BA never asks — whenever any of these are true:

- The app has a `queue` panel whose `columns` include a numeric field (amount, revenue, count, score, duration, latency, qty, price, ratio, percentage, …).
- The app has a `dashboard` panel — pair every dashboard with a chart that visualises the same metric over time or category.
- A `data_source` describes time-series, transactions, claims, tickets, orders, events, requests, runs, jobs, or anything that obviously accumulates over time.
- The agent's actions output structured rows with at least one numeric field.

## Choosing the chart shape

| Signal in the data | `chart_type` | `x` / `y` |
|---|---|---|
| Time field (date, week, month, day, timestamp) + numeric | `line` (or `area` if cumulative / volume) | `x = time field`, `y = numeric field(s)` |
| Categorical field (region, status, dept, product) + numeric | `bar` | `x = category`, `y = numeric` (set `stacked: true` if multiple series should add up) |
| One categorical breakdown of a single total | `pie` | `x = category`, `y = single numeric field` |
| Two or more numeric fields you want to compare | `line` or `bar` with `y: ["a", "b"]` | multi-series |

Default to `line` when in doubt and a time field exists; otherwise `bar`.

## Placement rules

- Place the chart **immediately after** the `dashboard` it visualises, or at the top of the page if there's no dashboard.
- One chart panel is enough for most apps. Add a second only when two distinct numeric stories exist (e.g. revenue trend + status mix).
- Do **not** ask the BA before adding the chart. Mention it in the plain-language summary as "…and a chart of <metric> over <axis>."
- Prefer binding the chart to the **same `data_source`** the queue uses, so they stay consistent.

## Example — adding a chart to a claims app

If the queue has `columns: ["claim_id", "insured", "amount", "status", "sla_due"]` and the dashboard tracks `throughput_24h`, inject:

```jsonc
{
  "id": "trend",
  "type": "chart",
  "title": "Claims volume",
  "chart_type": "line",
  "data_source": "claims_db",
  "x": "sla_due",
  "y": "amount"
}
```

If the queue is naturally categorical (e.g. status mix), inject a bar chart instead:

```jsonc
{
  "id": "by_status",
  "type": "chart",
  "chart_type": "bar",
  "data_source": "claims_db",
  "x": "status",
  "y": "amount"
}
```
