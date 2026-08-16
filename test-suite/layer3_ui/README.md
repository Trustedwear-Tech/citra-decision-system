<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Layer 3 — UI rendering matrix

**How UI coverage is actually measured.** Not runtime code-coverage (that only
proves code *ran*) — a matrix over the render **vocabulary × data states**, each
cell rendered in a real browser against a **mock API** and screenshot-diffed.

```
cell = (panel type) × (data state) [× interaction]
     e.g. (queue, empty), (detail, single_row), (chart, source_error)
```

Coverage = cells exercised ÷ legal cells (denominator from `../vocabulary.py`,
currently **78** — 62 render cells (13 real panel types; data panels carry all 8
states, static panels carry one) + 16 interaction cells). Emitted per-cell to
`../.coverage-cells/ui/` and folded into `../.coverage-cells/ui.json` by
`globalTeardown`; `../coverage_report.py` turns it into the UI %.

**Proven run (against the live `citra-app-runtime`): 78/78 = 100%.** Two specs:
- `rendering-matrix.spec.mjs` — 62 render cells: every panel × every legal data
  state, incl. the fail-loud negatives (a 502/403/non-columnar must show a
  visible error, never a silent-blank panel).
- `interactions.spec.mjs` — 16 interaction cells, each DRIVING the affordance and
  asserting the result: queue click_row→navigate, filter (client search), sort,
  paginate, override (the RunResultModal edit-then-apply); detail approve / reject
  / media_open; form submit→navigate; agent_chat send→grounded reply;
  filter_bar select→URL param; notifications click→navigate.

### Calibrate the test to the system (every run so far corrected the fixtures)
The harness is only honest if its model of the UI matches the runtime's actual
contract. Each run against the live runtime corrected test-side drift — the
runtime was correct every time:
- **No `table` panel.** `queue` *is* the tabular/list panel (`view:"table"` is a
  queue option). A `table` fixture hit the runtime's unknown-panel error.
- **`QueuePanel.columns` is `string[]`** (names), not `{name,type}` — objects
  crashed the client render on populated states.
- **Data-error states only apply to data panels.** `non_columnar` is rejected
  server-side by `panel_data.py`, so the fixture returns that rejection (422).
- **`override` is a queue affordance, not a detail one.** The detail approval
  section is read-only approve/reject; editing a recommendation before applying
  lives in the queue's `RunResultModal` (a card carrying `_recommendation`).
- **Interaction affordances are exact.** Row-click needs `actions[].is_row_click`;
  sort needs table view; the agent_chat reply rides `readAgentStream`'s non-SSE
  JSON fallback. Selectors are pinned to `PanelRenderer.tsx`.
Keep fixtures pinned to `citra-app-runtime/src/types/spec.ts` + `PanelRenderer`.

## Why a mock API (not the real backend)
You must control the data state deterministically — `loading`, `empty`,
`truncated`, `source_error`, `unauthorized`, `non_columnar`. You can't reliably
produce those against a live backend, and the **negative cells are the point**:
they prove the runtime **fails loud** (visible error, never a silent-blank panel).

The runtime is Next.js (server-side fetch), so the state can't ride a browser
header — it rides the **slug**: `fx-<panel>-<state>`. The mock parses the slug and
returns the matching fixture spec + data (`fixtures.mjs`, `mock-api.mjs`).

## Run

```bash
npm install
npx playwright install chromium

# 1. Start the runtime pointed at the mock API (separate terminal). No auth
#    bypass needed — the mock returns 200 regardless and the runtime's SSR sends
#    no token (bearer(null) -> {}). `next dev` is simplest (no prebuild); if
#    another runtime already owns .next, isolate with NEXT_DIST_DIR:
cd ../../citra-app-runtime
SMART_APP_SERVICE_URL=http://localhost:8899 npx next dev -p 3200
#   (a fresh dir avoids clobbering a concurrent dev server: add
#    NEXT_DIST_DIR=.next-mock and `distDir: process.env.NEXT_DIST_DIR || ".next"`)

# 2. Run the matrix (starts mock-api.mjs automatically via webServer).
#    First run records screenshot baselines; coverage is emitted either way
#    because the cell is recorded BEFORE the screenshot assertion.
cd -                                                 # back to layer3_ui
RUNTIME_URL=http://localhost:3200 npm test -- --update-snapshots   # first time
RUNTIME_URL=http://localhost:3200 npm test                         # thereafter
```

Render cells assert: (1) the cell is **recorded** for coverage; (2) it rendered
(not the app error boundary); (3) negative states show a **visible error**
(fail-loud — `role=alert`/`.panel-error` or the upstream detail text); (4) a
screenshot baseline. Recording before the screenshot means a baseline diff never
erases coverage. Interaction cells drive the affordance and assert the resulting
state (URL, modal, badge, reply). Coverage is emitted **per-cell** to
`.coverage-cells/ui/` and folded into `ui.json` by `globalTeardown`, so both spec
files contribute without clobbering. Add `@axe-core/playwright` for a11y per cell.

## Files
- `fixtures.mjs` — one valid AppSpec per panel type + the `PanelDataResponse`
  per state (incl. fail-loud negatives + the `rec` recommendation variant).
- `mock-api.mjs` — dependency-free Node server; serves `/apps/{slug}` (+ `/data`,
  `/detail`, `/document`, `/field-options`, `/media`, `/tool`, `/chat`,
  `/notifications`, `/run`, `/approve`) keyed off the `fx-<panel>-<state>` slug.
- `rendering-matrix.spec.mjs` — the 62 render cells.
- `interactions.spec.mjs` — the 16 interaction cells (real DOM drives).
- `_coverage.mjs` / `_globalSetup.mjs` / `_globalTeardown.mjs` — per-cell
  emission + aggregation into `ui.json`.

## Extending
Add a panel type to `vocabulary.PANEL_TYPES` + a `fixtureSpec` case, or a state to
`dataForState`. Interaction cells (click/submit/navigate/approve/override) follow
the same shape — drive the action, assert the resulting state.
