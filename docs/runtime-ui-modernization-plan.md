<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Runtime UI Modernization — proposal

**Status: PROPOSAL — not built.** Goal: Decision App runtime pages go from
"corporate but lackluster" to genuinely designed — modern information display,
icons, richer Next.js components, real design levers for the builder, and the
customer's company identity flowing from the ontology — **without** giving up
the declarative spec, the 3-layer contract, or publish-time governance.

## 1. Diagnosis (measured, not vibes)

| Fact (today) | Consequence |
|---|---|
| **3 inline SVGs** in the 5,000-line `PanelRenderer.tsx`; no icon library | Pages are walls of text; nav, tiles, statuses and actions carry no scannable visual meaning |
| `Theme` = `primary` + `accent` hex, `logo_url`, `dark_mode`, locale/currency | Every app looks like the same template with a different accent — no density, type, radius, or surface control |
| Server already computes KPI **delta (`MetricCompare`) + sparkline (`MetricTrend`)** | Barely surfaced — tiles render as static numbers when the interesting part (movement) is already in the payload |
| Detail pages = label:value lists; queues = table/cards/kanban | No hierarchy primitives: no hero band, no stat strip, no timeline, no profile header, no designed empty/loading states |
| Builder levers = page `layout: grid\|stack` + panel order + `ui_design.md` freeze | The builder cannot *design*, only arrange |
| Good bones: token CSS (`--citra-*` in `globals.css`, 1,740 lines), ECharts, Next 14 | Everything below can be built on the existing token system — **no framework rewrite needed** |

## 2. Non-negotiable constraints (carried from standing decisions)

1. **3-layer contract stays law**: `models.py` → `gen_schemas.py` (generated,
   never hand-edited) → `PanelRenderer.tsx` → skill teaching. Runtime fails
   loud on unknown constructs; never teach the builder what the runtime can't
   draw. Re-vendor `runtime-reference` + rebuild `citra-app-builder` on change.
2. **No raw HTML/CSS/JSX injection from the builder** (XSS + governance —
   rich-text/raw-HTML were excluded by design in the 2026-06-03 expansion and
   stay excluded). Design flexibility = **curated tokens + variants**, not freeform.
3. **All new spec fields optional** — every stored app renders unchanged.
4. **Legibility first**: these are officer tools. "Modern" = hierarchy, motion
   restraint, and meaning-carrying color/icons — not decoration.
5. **Lean deps**: `lucide-react` (tree-shaken, MIT) is the only new runtime
   dependency. No MUI/Chakra/antd (they fight the token system), no Tailwind
   migration (churn, zero user-visible gain), no framer-motion (CSS
   transitions + ECharts' own animations suffice).

## 3. Track A — Theme v2 (design tokens the builder can set)

Extend `Theme` (all optional; defaults reproduce today's look):

```jsonc
"theme": {
  "primary": "#0f6b3f", "accent": "#d97706", "logo_url": "…",
  "locale": "en-US", "currency": "USD",
  // NEW ↓
  "company_name": "Acme Power",        // default from ontology (Track E)
  "font": "inter",                      // inter | source-sans | ibm-plex | system (bundled, no external fetch)
  "radius": "soft",                     // sharp | soft | round      → --citra-radius
  "density": "comfortable",             // comfortable | compact     → spacing scale
  "surface": "elevated",                // flat | elevated | glass   → card shadow/backdrop tokens
  "mode": "light",                      // light | dark | auto (supersedes dark_mode bool, back-compat kept)
  "chart_palette": "calm"               // calm | vivid | mono | brand (derived from primary)
}
```

Implementation is almost entirely CSS-variable mapping in one place
(`AppShell` sets tokens on `.app-shell`, exactly like `theme.primary` today).
ECharts palettes map in `chartToEcharts.ts`. Closed enums throughout —
publish rejects unknown values (fail loud, schema-validated in the pod).

## 4. Track B — Icon vocabulary

- Add `lucide-react`; expose a **closed, curated enum of ~120 icon names**
  (ops-relevant: money, meter, truck, shield, file-check, map-pin, clock…).
  Publish rejects unknown names — no stringly-typed drift.
- New optional `icon` slots: `Page.icon` (exists — actually *render* it in
  nav + page header), `_PanelBase.icon`, `DashboardMetric.icon`,
  `QueueAction.icon`, `DetailSection.icon`, `NoticePanel` severity icons.
- **Auto-icon defaults by semantics** (renderer-side, zero spec change):
  currency column → banknote, date → calendar, status → activity, location →
  map-pin. Un-touched stored apps get better instantly.

## 5. Track C — New + upgraded panels (the "awesome display" set)

Each is a full 3-layer contract; new renderer code goes in **separate
component files** (PanelRenderer dispatches) — stop growing the 5,000-line file.

| # | Component | What it gives |
|---|---|---|
| C1 | `hero` panel | Page header band: icon + headline metric (a `DashboardMetric`) + subtitle + up to 2 actions. Kills the "title floating over a grid" look |
| C2 | `stat_strip` panel | Compact horizontal KPI band **with delta arrows + sparklines** — finally surfaces `MetricCompare`/`MetricTrend` everywhere |
| C3 | `timeline` panel | Vertical event feed bound to any dataset (`date_field`, `title_field`, optional `icon_field`/`badge_field`). Case histories, decision ledgers, outage logs |
| C4 | Queue `view:"split"` | Master-detail two-pane (list left, record right) — the modern triage layout, no page hop |
| C5 | Queue card polish | `badge_colors` (value→semantic color map), avatar/initials column, `secondary_columns` for de-emphasized metadata |
| C6 | Detail `layout:"profile"` | Header card (key facts + status pill + icon) over tabbed sections — replaces the flat label:value wall |
| C7 | Field display formats | `status_pill`, `currency`, `relative_time`, `progress` chips on queue columns + detail fields (closed enum) |
| C8 | Designed states (runtime-only) | Skeleton loaders, designed empty states ("No pending cases — all clear ✓"), designed error cards. No spec change |
| C9 | Micro-interactions (runtime-only) | CSS hover lifts, KPI count-up, ECharts entrance animation config. Restraint: nothing loops, nothing bounces |

## 6. Track D — Builder design flexibility + taste

- **`ui_design.md` gains a frozen "Design language" section**: tone
  (calm-ops / bold-exec / dense-analyst), Theme-v2 token choices, per-page
  icons, hero/stat-strip placement. Frozen like layout — the spec step obeys it.
- **Catalogue skills updated** (`citra-ui-panels` / `citra-ui-fields` /
  `citra-ui-charts`) with the new vocabulary + GOOD/BAD examples.
- **New `citra-design-taste` skill** — opinionated rules so LLM builders
  produce *designed* pages, not component dumps: one hero metric per page;
  ≤4 KPI tiles per strip; every nav page has an icon; badge colors are
  semantic (green=good state, amber=waiting, red=breach) and consistent
  app-wide; whitespace over boxes; dashboards lead with the decision the
  viewer must make.
- Publish gates stay mechanical only (unknown icon/enum → reject); taste
  stays advisory in skills — matches "platform primitives, not features".

## 7. Track E — Company identity in the ontology

New optional **envelope-level** block in `sources.json` (registry +
`sources-file.md` §2.x + schema + catalogue carry, same pipeline as `domain`):

```jsonc
"organization": {
  "name": "Acme Power & Utilities Co.",   // required if block present
  "short_name": "Acme Power",             // headers/nav; defaults to name
  "logo_url": "…",                         // optional
  "brand_color": "#0f6b3f"                 // optional seed for theme.primary
}
```

- **Why the ontology**: authored once by IT at source-connection time; every
  app, agent prompt, and report inherits it. This is *display identity* —
  auth `org_id`/tenant scoping is untouched (single-tenant posture stands).
- **Flow**: sources.json → catalogue → builder env (spec step defaults
  `theme.company_name` / `logo_url` / `primary` from it; BA may override) →
  publish stamps resolved values → runtime `AppShell` header renders
  "Acme Power · Recovery Tracker", browser title, agent system prompt
  ("Acme Power's recovery assistant"), Money-impact card ("Value recovered
  for Acme Power").
- Conflicting blocks across a tenant's sources → builder warning, first
  connected source wins (deterministic, visible).

## 8. Phasing

| Phase | Scope | Est |
|---|---|---|
| **U1** | Theme v2 tokens + `organization` ontology block + AppShell header/identity polish | ~2 d |
| **U2** | Icon system (lucide + closed enum + slots + auto-defaults) | ~2 d |
| **U3** | C1–C7 components (hero, stat_strip, timeline, split view, card polish, profile detail, display formats) | ~4–5 d |
| **U4** | Skills: catalogue updates + `citra-design-taste` + ui_design "Design language" freeze; then a **live builder authoring session** to prove the LLM actually uses the vocabulary well | ~2 d |
| **U5** | Designed states + micro-interactions + **visual regression in the render gate** (the Playwright render service already screenshots pages — turn those into golden-image diffs) | ~2 d |

Order rationale: U1+U2 change how *every existing app* looks for near-zero
spec churn (highest leverage first); U3 adds vocabulary; U4 makes the builder
use it tastefully; U5 locks quality in CI.

## 9. Non-goals / guardrails

- No freeform HTML/CSS/JS from the builder — ever (governance + XSS).
- No per-panel arbitrary colors — semantic + theme-derived only, so apps
  can't become circus posters.
- No new UI framework, no Tailwind migration, no renderer rewrite — evolve
  the token system that already works.
- Consumer-visible **behaviour** unchanged: same panels, same data plane,
  same approval flows — this is presentation + vocabulary only.

## 10. Risks

- `PanelRenderer.tsx` is a 5,000-line monolith → new components in separate
  files; only the dispatch switch grows.
- Schema churn cadence: every U-phase = `gen_schemas.py` + re-vendor
  runtime-reference + builder image rebuild (established pipeline; the cost
  is known, not novel).
- Taste is subjective → the live authoring session in U4 is the acceptance
  gate: if a fresh builder session produces a page a designer wouldn't wince
  at, ship; if not, tighten `citra-design-taste` and rerun.
