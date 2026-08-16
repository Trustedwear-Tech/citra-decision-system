<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra Platform — Test Strategy

How we get from "we tested some apps" to "we can trust the platform for **any**
app it can build." The permutation space (any app × any source × any data × any
decision path) is effectively infinite; this doctrine makes it tractable.

## The core idea: test the *contract* and the *vocabulary*, not the permutations

An app is a **composition** of a small, finite vocabulary:

| Vocabulary | Count (approx) |
|---|---|
| panel types (`queue`, `detail`, `form`, `chart`, …) | ~14 |
| detail-section types (`fields`, `attachment`, `approval`, …) | ~8 |
| tool kinds (`mcp`, `mcp_action`, `fraud_synthesis`, …) | ~12 |
| source kinds (`sql`, `rest`, `odata`, `mongodb`, `semantic`, …) | ~9 |
| publish validators (F-01, E-03, W-06, …) | ~15 |
| data states (loading, empty, many, truncated, source-error, non-columnar, …) | ~8 |
| decision-loop transitions (recommend, approve, override, reject, direct) | ~7 |

The single source of truth for these is [`test-suite/vocabulary.py`](../test-suite/vocabulary.py).
We don't test every app — we test **every vocabulary element once**, the
**pairwise combinations** that interact, and the **contract at each layer
boundary**. Vocabulary covered + boundaries frozen ⇒ any composition works. This
collapses N×M×K into ~N+M+K.

## The four layers, each isolated behind a proxy

Every layer is tested against **frozen fakes of its neighbors**, never the real
ones — that's what decouples them and lets each run fast and deterministically.

### Layer 1 — MCP (source connectors)
- **Proxy:** mock the upstream source (`httpx.MockTransport` for REST, a fixture
  DB for SQL, a fake S3 for media) + stub the planner LLM.
- **Contract:** `run_query` / `execute_action` / `media` / discovery, per **kind**.
- **Coverage:** (source-kind × op × {happy, empty, error, missing-param, SSRF,
  non-JSON, timeout}) cells. Connector line-coverage is secondary.
- **Reference implementation:** `source-mcp-template/tests/test_rest_connector.py`
  (15 tests, no network) + `test_rest_integration.py`.

### Layer 2 — Builder (LLM authoring)
- **Proxy:** **record/replay** real LLM sessions; mock `/builder/catalogue`,
  `/builder/validate`, `/publish`. Live runs are gated (cost + pod).
- **Contract:** BA goal → a spec that **passes all validators + the smoke gate**.
- **Coverage:** eval **pass-rate** over a goal corpus that is chosen to exercise
  every panel/tool kind (vocabulary coverage of the corpus).
- **The LLM is nondeterministic** → assert **metamorphic** relations, not
  equality: "same goal → an equivalent valid spec"; "add a source → ≥1 more
  tool"; "REST source → param-bound filter, no chart". Score a corpus, not one case.

### Layer 3 — Runtime UI
- **Proxy:** a **mock smart-app API** serving fixture responses per data state.
- **Contract:** AppSpec + data state → correct render + interaction.
- **Coverage — the rendering matrix (NOT code coverage):**
  ```
  cells = {panel types} × {data states} × {interactions}
  ```
  Each cell = a Playwright test that loads the runtime with a fixture spec, the
  mock API returns that state, and asserts (1) structure (a11y snapshot),
  (2) **visual regression** (screenshot diff), (3) accessibility (axe). Include
  the **negative cells** (unknown panel → error card; source-failure → error not
  blank) — these are the fail-loud guarantees.
- Code coverage of the runtime proves code *ran*, not that it *rendered right*.
  The matrix is the real number.

### Layer 4 — Memory & learning (rubric / DecisionRecord / outcome loop)
- **Proxy:** seed synthetic `DecisionRecord`s + outcomes; feed the
  grounding/rubric pipeline.
- **Contract:** past decisions + outcomes → expected few-shot grounding /
  thresholds / recommendation shift.
- **Coverage:** (rubric rule × outcome class {good/bad/neutral} × drift) cells.
- Metamorphic again: "an override on a decision → grounding shifts toward the
  corrected value"; "a bad-outcome poll → the pattern is down-weighted".

## Taming the permutation explosion — two force multipliers

1. **Consumer-driven contracts.** The fake the UI (or builder) tests against is
   *generated from* the MCP's own contract tests. A drift breaks a test, not
   prod. This turns N×M into N+M.
2. **Spec fuzzer.** Generate random **valid** AppSpecs and assert **invariants**
   (renders without throwing; publish validators are self-consistent; every data
   path fails loud; every `data_source.ref` is declared; every `navigate.page`
   exists). The fuzzer finds the combinations no one hand-writes — this is how
   "huge permutations" get covered cheaply. See [`test-suite/fuzzer/`](../test-suite/fuzzer/).

## How the layers wire into production confidence

```
per-commit :  unit + contract (fast, isolated, per layer)      → vocabulary + boundaries
per-PR     :  integration per layer (mock neighbors) + UI matrix + fuzzer
per-release:  a SMALL set of golden E2E through the REAL stack   → the seams mocks hide
              (fraud screen, directory-lookup)
prod       :  canary + smoke + observability (SLOs, error budget)→ the unknowns
```

**Exit criteria — "production-acceptable":**
- contract suites cover **100% of the vocabulary**;
- UI matrix ≥ **95%** of legal cells (visual + a11y green);
- golden E2E green in **both** test and prod;
- **zero fail-loud violations** (RULE #1 — no silent empty/default anywhere);
- performance SLOs hold at target concurrency;
- a defined **error budget** in prod observability that, if burned, blocks release.

## Performance parameters (SLOs per layer)

Load-test each layer behind its proxy (k6/Locust); soak + concurrency-ramp to find
the knee; gate on "SLOs hold at target concurrency."

| Layer | Track |
|---|---|
| **MCP** (the shared bottleneck — every read + fraud check) | `run_query` p50/p95/p99 **per kind** (SQL vs REST vs semantic differ 10–100×), qps, max concurrency, pool saturation, timeout rate; for REST also upstream-API p99 + rate-limit |
| **Builder** | time-to-publish (turns × LLM latency), tokens/session (10M cap), pod spawn time, max concurrent builds, $/build |
| **Runtime UI** | SSR/TTFB, per-panel data latency (bounded by MCP), bundle size, Core Web Vitals (LCP/INP/CLS) |
| **Memory/learning** | grounding-pull latency, rubric match time, outcome-poll lag, index freshness |

The MCP is the scaling risk: every data read and fraud tier flows through it, and
REST/API sources add a live third-party call in the hot path.

## The suite

`test-suite/` implements this doctrine. See its [README](../test-suite/README.md)
for what runs offline today vs what needs the live stack, and how to run each
layer + the coverage report.
