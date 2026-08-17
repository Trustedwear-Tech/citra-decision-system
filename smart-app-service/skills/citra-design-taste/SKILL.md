---
name: citra-design-taste
description: Opinionated design rules for composing GOOD-LOOKING Decision App pages — hierarchy, icons, badge semantics, theme tokens; read after citra-ui-panels
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

# Citra Design Taste — compose designed pages, not component dumps

> Vocabulary lives in `citra-ui-panels` / `citra-ui-fields` / `citra-ui-charts`;
> the enforced contract lives in `citra-system` → `runtime-reference/`. THIS
> skill is about **taste**: given the vocabulary, what does a page a designer
> wouldn't wince at look like? None of this blocks publish (except the icon
> vocabulary, rule I-01) — but a page that ignores it reads as a form dump,
> and the demo is the product.

## The one-sentence test

Before authoring a page, finish this sentence: *"The person opening this page
needs to ______ in under ten seconds."* Every panel that doesn't serve that
sentence moves to another page or gets cut. A page is a decision surface, not
a data inventory.

## Hierarchy rules

1. **One hero moment per page.** Either a `hero` panel (icon + headline +
   THE number that page exists for) or a single dominant KPI strip — never
   both, never two heroes. The hero's metric is the page's headline metric,
   not a random count.
2. **≤ 4 tiles in a `dashboard` KPI row, 2-6 in a `stat_strip`.** More
   numbers = less meaning. If the BA lists 8 KPIs, the top 4 go on tiles and
   the rest go in a chart or a drill-down page.
3. **Give deltas or don't bother.** A stat_strip without `compare`/`trend` is
   small static numbers — add the date_field-based compare so arrows and
   sparklines render, or use a plain dashboard row.
4. **Queues lead with the deciding field.** `columns` order = attention
   order: identifier, the field the officer decides on (amount / severity /
   due date), then status. Push reference metadata to `secondary_columns`.
5. **Detail pages open with vitals.** Use `layout: "profile"` with 3-4
   `header_fields` (who, how much, since when) + `status_field`. The flat
   label:value wall is what we're moving away from.
6. **Timelines for anything that HAPPENS over time.** Case histories, decision
   ledgers, outage logs — a `timeline` beats a table of timestamped rows.

## Icons

- **Every nav page gets an icon** (`page.icon`) — a sidebar without icons
  reads as a prototype. Match the noun: queues → `inbox`, money →
  `banknote`/`wallet`, field ops → `map-pin`/`truck`, compliance →
  `shield-check`, analytics → `trending-up`/`gauge`, settings → `settings`.
- Panel icons (`panel.icon`) only when they ADD meaning — don't icon every
  panel on a busy page; two or three well-chosen ones beat ten.
- NEVER invent an icon name — the vocabulary is closed (publish rule I-01
  rejects unknown names). When unsure, omit: KPI tiles auto-pick sensible
  icons by semantics.

## Badge + color semantics (app-wide consistency)

- One meaning per color, everywhere in the app: **green** = good/terminal-good
  (recovered, resolved, compliant), **amber** = waiting on someone (pending,
  under review), **red** = breach/terminal-bad (overdue, rejected,
  written_off), **blue** = in progress, **slate** = neutral/closed-neutral.
- Declare `badge_colors` explicitly for the statuses that matter; don't rely
  on auto-detection for the app's central status column.
- Never two different colors for the same status value on different pages.

## Theme tokens (set once, per app tone)

Pick ONE tone and set the theme accordingly — don't mix:

| Tone | Who it's for | theme |
|---|---|---|
| **calm ops** (default) | officers working queues all day | `radius: soft`, `surface: flat` or default, `chart_palette: calm` |
| **bold exec** | leadership dashboards, demos | `surface: elevated`, `radius: round`, `chart_palette: brand`, hero panels on every page |
| **dense analyst** | reconciliation / audit tables | `density: compact`, `radius: sharp`, `view: "table"` queues, `chart_palette: mono` |

- `company_name` / `logo_url` / `primary` are INHERITED from the ontology's
  `organization` block — leave them unset unless the BA explicitly overrides.
- `mode: "dark"` is BETA — preview before publishing a dark app.

## Whitespace over boxes

- Prefer fewer, larger panels to many small ones; merge two tiny related
  panels into one.
- A `notice` band is for genuine caveats (SLA rules, data freshness) — not
  decoration. One per page, at most.
- Don't wrap a single chart in its own page — pages need a reason to exist.

## Anti-patterns (instant "AI-generated" tells)

- ❌ Page title + hero headline saying the same words twice.
- ❌ Eight KPI tiles of raw counts with no deltas.
- ❌ Every panel iconed, every status rainbow-colored.
- ❌ A queue whose first three columns are ids and foreign keys.
- ❌ `chart_palette: vivid` on a claims/fraud app (serious money = calm
  palettes; vivid is for consumer-ish domains).
- ❌ Two panels answering the same question with different numbers — bind
  both to the same metric or delete one (and money ALWAYS comes from the
  decision_ledger, per citra-app-spec).
