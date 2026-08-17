<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# citra-dashboard-spec — Rich KPI tiles (filter / compare / trend / label)

Read this when authoring KPI `dashboard`-panel metrics. The core SKILL.md holds
the hard rules (always set `label`; LABEL-HONESTY — filter to match the label;
add `trend` to count/sum KPIs; never invent column names). This file is the
field-shape detail + relative-date tokens + a worked example.

A KPI metric is computed by a **source-side aggregate** over the WHOLE table
(true `COUNT(*)`/`SUM`, never a capped row count). Optional fields on the metric:

- **`filter`** — a predicate so the number matches the label. A tile titled "Active outages" MUST carry `"filter": {"status": "active"}`, or it counts every row and the label lies. Same shape as `ChartPanel.query`: `{col: value}`, `{col: {"$in": [...]}}`, `{col: {"$ne": x}}`, `{col: {"$gt": n}}`. **Always filter a "open/active/pending" KPI.**
  - **LABEL-HONESTY (hard rule).** If a tile's name qualifies the count — *urgent, breached, overdue, unacknowledged, pending, today, this-week, high-priority, at-risk* — the metric MUST carry the predicate that makes the number that subset. **Never reuse another tile's `data_source` for a narrower label without adding the distinguishing `filter`** — otherwise two differently-labelled tiles show the identical number (a "made-up number" the BA will catch). Concretely:
    - **"… breaches / overdue / past SLA"** → the date comparison, e.g. `{"status": {"$ne": "resolved"}, "sla_due_at": {"$lt": "now"}}`; add the severity field too if the label says *urgent* (`"priority": "urgent"`).
    - **"unacknowledged / unconfirmed / un-reviewed …"** → the boolean flag, e.g. `{"acknowledged": false}`. A bare `tamper_events` count is *all* events, not the unacknowledged ones.
    - **"… (today / this week / last 24h)"** → a time predicate on the event date (see relative-date tokens below).
  - **Relative-date tokens** are resolved DB-side to the real calendar, so a "today"/window tile tracks live data instead of going null. Usable as the value inside a range operator (`$gte/$lte/$gt/$lt`) on a date/time column: `today`, `now`, `yesterday`, `this_week`, `this_month`, `this_year`, or a window like `24h`, `7d`, `30d`, `12m`. Example — consumers affected by outages that started today: data_source filter `{"status": "active", "start_time": {"$gte": "today"}}`. Do NOT hard-code a calendar date for a "today"/rolling-window tile.
- **`compare`** — `{"date_field": "<date col>", "grain": "day|week|month", "periods": 1}` → the runtime computes the **latest period present in the data** (anchored to `MAX(date_field)`, NOT the wall clock) vs `periods` grains earlier and renders a real ▲/▼ delta chip. Because it anchors to the data's latest period, the delta stays correct even when the data lags "today". Use for **flow** metrics (assessed latest-day vs prior-day, registered this week vs last). Skip it for pure stock counts — the delta is only shown when a prior baseline exists.
- **`trend`** — `{"date_field": "<date col>", "grain": "day", "points": 14}` → a real grouped-by-time series rendered as the tile sparkline (hover shows *date: value*). Bind it to the natural event date (start_time, registered_at, detection_date). **DEFAULT: add `trend` to EVERY `count`/`sum` KPI whose dataset has a usable date column.** Without it the tile is a bare number — a sparkline turns it into a glanceable mini-history at no extra cost. Only omit `trend` when the dataset genuinely has no date/timestamp column to bucket by (then say so in `requirements_unmet`). `avg`/`min`/`max`/`ratio` tiles may skip it — a time-bucketed sparkline of an average is rarely meaningful.
- **`label`** — a short clean subtitle (e.g. "Currently active", "Pending recovery"). Without it the tile shows the raw agg + field id, which reads technical. **Always set `label`.**

Example — every column name and value below is from ONE app's catalogue (a
theft-recovery domain); replace each (`case_id`, `recovery_status`, the states,
`detection_date`, the data_source id) with YOUR dataset's actual columns/values
from the catalogue. There are no standard column names:
```jsonc
{
  "name": "Open theft cases", "agg": "count", "field": "case_id",
  "data_source": "ds_theft", "label": "Pending recovery",
  "filter":  { "recovery_status": { "$in": ["pending", "under_recovery"] } },
  "compare": { "date_field": "detection_date", "grain": "week", "periods": 1 },
  "trend":   { "date_field": "detection_date", "grain": "day", "points": 14 }
}
```
These need **SQL-family** catalogue datasets (the aggregate is pushed to the source). For non-SQL sources the runtime falls back to an unfiltered count — so prefer SQL sources for KPI tiles, and pick `filter`/`compare`/`trend` columns that actually exist on the table (check the catalogue schema).
