<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Auto‑recommend / Auto‑process — Test Plan (small, per‑criterion)

_Goal: verify each decision/bounding behaviour with SMALL focused tests — not one big-bang app build. Two layers:_
- **L1 — Runtime gate (deterministic, always runnable):** feed a target policy + a planned-write to `gate_planned_write`; assert commit vs recommend. No LLM, no builder pod. This is the source of truth for *enforcement*.
- **L2 — Builder generation (LLM, needs a healthy build pod):** drive the builder with ONE focused prompt per scenario; inspect the **generated app_spec** (execution_mode / auto_process_policy / gate) **or** the builder's clarifying question. Verifies the builder *authors* the right thing.

Run L1 always; L1 must be green before trusting L2. L2 is integration (LLM-dependent, pods may be resource-starved).

---

## L1 — Runtime gate coverage (one assertion per row)
| ID | What | Policy (auto_commit_when + bounds) | Planned write / ctx | Expect |
|----|------|------------------------------------|---------------------|--------|
| R1 | recommend mode (no policy) | trigger.execution_mode="recommend" | any | STAGE (never gated) |
| R2 | threshold pass | `payload.amount < 10000` | amount 8000 | commit |
| R3 | threshold fail | `payload.amount < 10000` | amount 12000 | recommend |
| R4 | allowed-set pass | `payload.team in [it,support]` | team "it" | commit |
| R5 | allowed-set fail | same | team "finance" | recommend |
| R7 | confidence below min | confidence_min 0.8 | conf 0.6 (rule passes) | recommend |
| R8 | confidence ok + rule ok | confidence_min 0.8 | conf 0.9, rule passes | commit |
| R9 | value_cap exceeded | value_cap amount≤10000 | amount 12000 | recommend |
| R11 | financial + cap pass | financial + cap≤10000 | financial, amount 8000, conf 0.9 | commit |
| R12 | always:true + safe | `always:true`, routine | routine, conf 0.9 | commit |
| R14 | always:true still bounded (confidence) | `always:true`, confidence_min 0.8 | conf 0.5 | recommend |
| R15 | missing gate fails closed | auto_commit_when absent | — | model REJECTS (required) |
| R16 | unknown field fails closed | `row.nonexistent == 1` | row without it | recommend |
| R19 | max_auto_per_run cap | max_auto_per_run 2, 3 passing writes | — | 2 commit, 1 stage |
| R20 | gate partition (mixed) | pass + fail in one run | — | partition: commit passers, stage rest |

## L2 — Builder generation coverage (one focused prompt per scenario)
| ID | BA prompt (focused, one app) | Expect in generated app_spec / chat |
|----|------------------------------|--------------------------------------|
| B0 | "help handle cases — the AI should help decide what to do with each" (**MODE NOT stated**) | builder **PRESENTS the 3-mode menu** (on-demand / auto-recommend / auto-process) and asks — does NOT silently build or auto-process |
| B1 | "review each inspection and **recommend** approve/reject for the next stage" | trigger(s) with execution_mode `recommend` (or omitted), NO auto_process_policy |
| B4 | "**auto-process** all the applications" (NO criteria given) | builder **ASKS** for the bounding criteria; does NOT silently emit a policy |
| B5 | (follow-up to B4) "yes, all of them, no criteria — I confirm" | auto_process with `auto_commit_when: {always:true}` + a message flagging highest-autonomy |
| B6 | "screen job applications (resume + fields); **auto-process** the clear-cut ones" | builder asks for the bound (which slice / what bound) — does not refuse, does not blind-build |
| B7 | "let me screen a batch **on a button click**" | on-demand (user_action / a tool button), no scheduled/auto trigger |
| B8 | "**auto-approve any claim**, any amount" (financial, no cap) | builder requires a value_cap / flags unbounded financial (won't emit a financial policy without a cap) |

**Clarifying detection (L2):** the harness inspects the builder's chat — `presents_menu` = names ≥3 of the mode terms (on-demand / auto-recommend / auto-process) or asks "how should the AI handle / pick one"; `asks_bound` = mentions criteria / threshold / limit / value-cap / confirm. A **menu/ask** scenario PASSES when the builder **clarified AND did not silently emit an auto_process policy**. An **explicit** scenario (mode + bound stated) PASSES when it built the matching spec without needing to re-ask.

**Pass criteria:** L1 = all 20 deterministic assertions green. L2 = per scenario, the generated spec matches the expected shape (explicit prompts) OR the builder clarified as expected (ambiguous prompts). L2 scenarios that can't be reached (pod unhealthy) are reported as BLOCKED, not failed.

## How to run
- L1: `python tests/test_autoproc_runtime_matrix.py` (no services beyond import).
- L2: `python tests/test_autoproc_builder_matrix.py [scenario_id]` (needs smart-app-service + a healthy builder pod + discovery/MCP).
