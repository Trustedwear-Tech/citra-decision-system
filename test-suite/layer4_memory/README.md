<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Layer 4 — Memory & learning

The learning loop turns **past decisions + their outcomes** into: few-shot
grounding for new cases, tightened judgment criteria, and down-weighting of
decisions that turned out wrong. You can't unit-test "did it learn?" with
equality — you test it **metamorphically**, at two levels:

```
test_learning_fixtures.py   the INVARIANTS, against a reference scorer
test_real_memory.py         the REAL smart-app-service memory modules, isolated
_memcells.py / conftest.py  per-cell emission -> memory.json (multi-file safe)
```

**Coverage: 15/15 cells (5 rubric signals × 3 outcome classes) — 100%, PASS.**

## The invariants (fixtures, reference scorer)
- **record-binding match** — a case matching a past record's binding grounds in
  that record first.
- **outcome down-weight** — bad < neutral < good; the corrected decision wins
  over the wrong one for the same binding.
- **threshold shift** — bad outcomes tighten the gate; nothing shifts without a
  real signal.
- **drift** — a bad-rate ≫ baseline flags; a healthy window never does
  (one-sided).

## The real implementations (test_real_memory.py)
Driven behind fakes, same isolate-behind-proxies doctrine as every layer:

- **`grounding_refresh.loop_decision_to_sample`** — the real few-shot builder:
  a good+approved decision becomes a positive sample; an officer **override is
  encoded contrastively** ("CORRECTED … 'Fail' → 'Repair'"); a **bad outcome
  becomes an ANTI-PATTERN** ("AVOID: …") sample; an **unsettled outcome is NOT
  prematurely down-weighted** (truth is slow — no anti-pattern before the
  outcome lands).
- **`item_records`** — the multimodal item ledger: an exact content-hash match
  on a *different* item surfaces the officer's past reject reason ("stock image
  reused") in the analysis prompt; items **inherit the parent case's outcome**
  via correlation_id.
- **`analysis_rubrics.append_correction`** — the SOP layer: a reject reason
  becomes a durable correction (with subject + item_id); an **empty reason is a
  no-op** — the rubric never moves without a real signal.

The full item-ledger behavioral suite (both-classes capture, latest-row
stamping, idempotent writes, index creation, broken-store-degrades-loudly)
lives with the service: `smart-app-service/tests/test_item_records.py`.

## Run
```bash
python -m pytest . -q     # emits ../.coverage-cells/memory.json (15 cells)
```
`test_real_memory.py` imports the service modules directly (needs `pydantic` +
`pydantic-settings`); it skips cleanly where they aren't installed.

## Why fixtures + fakes, not a live DB
The learning signal is the OUTCOME, which arrives days later in production. The
harness manufactures the full loop (decide → outcome → re-decide) in one run,
so you assert the loop's *direction* deterministically instead of waiting on
real feedback.
