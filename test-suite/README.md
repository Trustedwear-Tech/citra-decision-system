<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra test suite — proving the 4 layers work in production

The platform is 4 layers whose permutations are astronomically large, so we
**don't** test permutations. We test the **contract + vocabulary** of each layer
in isolation behind proxies, then compute coverage against a fixed denominator
(the vocabulary), and gate on per-layer targets. The doctrine is in
[`../docs/test-strategy.md`](../docs/test-strategy.md) — read that first.

```
vocabulary.py        the denominators — every panel/tool/source/validator/state we support
coverage_report.py   aggregates each layer's emitted cells → % vs target → GATE

layer1_mcp/          MCP contract matrix   (kind × op × state), real connector + mock upstream
layer2_builder/      builder eval          (BA goal → published spec passes validators + smoke)
layer3_ui/           UI rendering matrix   (panel × state) in a real browser vs a mock API
layer4_memory/       learning invariants   (record binding, outcome down-weight, threshold, drift)
fuzzer/              spec fuzzer           (random valid specs stay valid; each violation is caught)
```

## The core idea
- **Denominator = vocabulary, not code.** Coverage is *cells of the contract
  exercised ÷ legal cells*, computed in `coverage_report.py` from `vocabulary.py`
  (UI 78 legal cells = 62 render + 16 interaction, MCP 100, memory 15).
  Code-coverage only proves a line *ran*; cell-coverage proves a **behavior ×
  state** was actually observed. The vocabulary must track the real contracts —
  each UI run corrected drift (a non-existent `table` panel, wrong `columns`
  shape, `override` being a queue not detail affordance) by failing against the
  live runtime.
- **Isolate behind proxies.** Each layer runs against a replica of its real
  neighbors: MCP against a mock HTTP upstream, UI against a mock API keyed off
  the app slug, memory against synthetic DecisionRecords, builder against a
  recorded/live session. No layer needs the whole stack to be measured.
- **Negative cells are the point.** `source_error`, `unauthorized`,
  `non_columnar`, `missing_param`, `upstream_error` all assert the layer **fails
  loud** (visible error, never a silent blank) — RULE #1, enforced as tests.
- **Fuzz the invariants.** 500 random valid specs must stay valid; each seeded
  violation (unknown panel, dangling datasource, chart-over-REST, REST-without-
  filter, dangling navigate) must be caught. Catches gaps the fixed corpus misses.

## Run

### Offline (no stack — CI-friendly, runs today)
```bash
pip install pytest httpx
python -m pytest layer1_mcp layer4_memory fuzzer -q   # emit mcp/memory/fuzz cells
python layer2_builder/eval_runner.py --report          # emit builder corpus cell
python coverage_report.py                              # aggregate + gate
```

### Stack-bound (the layers that need a running system)
```bash
# UI matrix — needs citra-app-runtime + the mock API (see layer3_ui/README.md)
cd layer3_ui && npm install && npx playwright install chromium && npm test

# builder live — needs smart-app-service + a builder JWT
SAS_BASE_URL=… SAS_JWT=… python layer2_builder/eval_runner.py --live

python coverage_report.py    # now includes ui.json + the live builder pass-rate
```

## Reading the gate
`coverage_report.py` prints per-layer `covered / pct / target / gate` and an
overall PASS/FAIL. **FAIL is the honest default** until the stack-bound layers
have run *and* their matrices are complete. The UI matrix is complete and green
against the live runtime — **78/78 = 100%** (62 render + 16 interaction cells),
every fail-loud negative and every affordance (row-click/navigate, filter/sort/
paginate, approve/override/reject, media-open, form submit, chat, filter-bar,
notifications) driven and asserted. MCP is **21/100** — all 9 kinds proven to
fail loud through `query_engine.execute`, plus real sql/duckdb happy+empty; the
rest need live backends (see layer1_mcp/README.md). Memory is **15/15 (100%)**
— the learning invariants against a reference scorer PLUS the real service
modules (grounding write-back polarity, item-ledger precedents, rubric
corrections) driven in isolation (see layer4_memory/README.md). Builder remains
an offline corpus. Production-acceptable = **every layer at target with the
stack-bound layers actually executed**, plus the fuzzer clean.
Targets live in `coverage_report.py::TARGETS` (mcp .90, ui .95,
builder_validators 1.0, builder_tool_kinds .90, memory .90).

## Performance / SLOs
Coverage proves *correct*; SLOs prove *fast enough*. Per-layer budgets
(p95 MCP query, builder publish, panel first-paint, recommend latency) are
defined in `../docs/test-strategy.md` — wire them as assertions in the same
harnesses (each layer already has a real call site to time).

## Extending
Grow a denominator in `vocabulary.py` → add the cell-emitting test in that
layer → re-run `coverage_report.py`. The gate tells you what's still uncovered.
