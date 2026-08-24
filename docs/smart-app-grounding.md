<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Smart App grounding — few-shot from history

**Status:** Implemented (v1, no workflow engine).
**Last updated:** 2026-06-04

Ground a Smart App agent in the tenant's **own historical decisions** so it
handles new cases the way the team actually did — in-context few-shot, **no
model training, no LoRA, no workflow engine**.

> History note: an earlier implementation built the sample corpus with a
> `wf_refresh_history` Citra **workflow** (nodes `dept_mcp_historical_pull`,
> `sample_packager`, `fewshot_selector`, `sample_refresh_guard`,
> `sample_store_sink`, `sample_vector_sink`). When the workflow engine was
> retired from Smart Apps, that pipeline was re-homed into smart-app-service as
> the deterministic operation described below. The runtime tool and Gate A were
> unchanged.

## It is grounding, not training

No model weights change. No LoRA. No GPU. "Grounding" means: collect the team's
past decisions, curate the best ones, and inject them into the agent's prompt
at request time so it decides like the historical record, not the LLM's prior.

## How it works at runtime

The agent's `neighbor_samples` tool (`AgentSpec.tools_v2`, `models.py`) reads a
per-agent Milvus collection `samples_<agent_id>` and stitches two blocks into
the prompt:

- **canonical** — a curated, always-loaded set (~5–15) covering the decision
  classes;
- **neighbors** — the nearest past cases to the current input (vector search),
  optionally filtered by decision/severity.

The runtime no-ops safely on an empty collection (the agent falls back to its
base prompt), so a missing/half-built corpus never breaks a run
(`runtime.py::_prefetch_few_shot_blocks` / `tools_v2_dispatch::_query_neighbor_samples`).

## The corpus is built server-side (no workflow)

`smart-app-service/grounding_refresh.py`:

```
pull (dept-MCP /run_query, paginated)
  → package  (map to {input, output, decision, severity, reasoning}; PII scrub; dedupe)
  → select   (canonical few-shots: round-robin across decision classes, per_decision_min, target_count)
  → GUARD    (Gate B — reject a degraded refresh BEFORE any swap)
  → embed + atomic swap into samples_<agent_id>
             (build a fresh physical collection, then re-point the read alias)
```

Invoked **manually only**, and **asynchronously**:

- **Start** — `POST /apps/{slug}/grounding/refresh` creates a job and returns a
  `run_id` immediately (no blocking; a multi-thousand-row pull + embed can take
  minutes). One refresh at a time per app.
- **Poll** — `GET /apps/{slug}/grounding/refresh/status` returns
  `{status: running|complete|failed, phase, progress 0–100, counts, result|error}`.
  The UI ("Refresh grounding" button, shown when the app's list item has
  `grounded: true`) polls this and shows live progress + a completion event.

Publish does **not** auto-populate: a newly published grounded app starts with
an empty collection (runtime falls back to the base prompt) and surfaces a
`grounding_refresh_required` note telling the BA to **run Refresh grounding
before testing**. Grounding always pulls the **real (prod) domain history** —
never the ephemeral test plane — so the agent grounds on actual past decisions
even while the BA is still testing. No scheduled refresh in v1.

### Stores (dedicated, single per agent — not env-split)

| Store | Holds | Read by |
|---|---|---|
| Milvus `Historical_Refresh` (ONE shared collection for ALL agents; rows isolated by an `agent_id` field) | embedded vectors of the curated samples | the runtime `neighbor_samples` tool (filters by `agent_id`) |
| Mongo `smartapp_grounding_samples` | the curated samples (source-of-truth / audit copy, keyed by `agent_id`) | inspection / re-vectorisation |
| Mongo `smartapp_grounding_runs` | one doc per refresh job: status, phase, progress, counts, result/error | the UI progress poll |

A single Milvus collection backs every agent's grounding (rows carry `agent_id`;
the writer replaces an agent's rows with delete-by-`agent_id` + insert, and the
reader filters by `agent_id`). This keeps grounding to one collection no matter
how many agents are grounded — important on capped Milvus deployments (e.g.
Zilliz free tier's 5-collection limit). `NeighborSamplesTool.collection` is
`Historical_Refresh` for all agents (enforced by publish rule G-01).

## Two gates — safe + non-bypassable

- **Gate A — is the history good enough?** `GET /builder/history-quality`
  (build time, `main.py`). Deterministic hard gates (row count, ≥2 decision
  classes, decision column present, input columns present) + builder judgment;
  emits the `suggested_contract` + `signals` baseline. Fail → do not ground.
- **Gate B — is THIS refresh safe to swap in?** `grounding_refresh.evaluate_guard`
  (every refresh). Rejects an empty / below-floor / class-missing / shrunk /
  low-fill pull and leaves live samples untouched — the new corpus is built in
  a fresh physical collection and the alias is only re-pointed after the guard
  passes.
- **Publish enforcement (rule G-01, `publish_validators.validate_grounding_contract`).**
  `/publish` rejects a `neighbor_samples` agent lacking a `grounding` contract
  with Gate A evidence (`source_profile_baseline` + `evaluation_verdict`), or
  whose tool `collection` ≠ `samples_<agent_id>`. Grounding can only ship after
  the data was vetted, and always writes where the runtime reads.

## The contract (`AgentSpec.grounding` — `models.py::GroundingContract`)

What to pull (`source_id`, `dataset_id`, `filters`, `max_results`); field
mapping (`source_id_field`, `input_fields`, `output_fields`, `decision_field`,
`reasoning_field`); selection (`target_count`, `per_decision_min`); Gate B
thresholds (`min_samples`, `min_canonical`, `shrink_floor`,
`required_decision_classes`, `min_decision_fill_rate`); Gate A evidence
(`source_profile_baseline`, `evaluation_verdict`). The builder skill
`skills/citra-fewshot-from-history/SKILL.md` authors it in build Phase 1.5.

## Source-system prerequisites

- The dataset's catalogue entry declares `decision_history`
  (`is_decision_record: true` + decision/timestamp/reasoning columns) — set by
  the MCP operator per dataset in the MCP's `sources.json`.
- The source serves a bulk/date-range read (the historical pull).
- smart-app-service has `MILVUS_URI` (+ token) and an embedding endpoint
  (`EMBEDDING_API_BASE`/`EMBEDDING_API_KEY`/`EMBEDDING_MODEL`) — the same config
  the `neighbor_samples` runtime already requires (publish gate enforces it).

## v1 limitations

- Test and prod share one collection per agent (`samples_<agent_id>` is not
  env-prefixed): the runtime reads a fixed collection name and `promote` reuses
  the same `agent_id`. Fine when test/prod share the MCP; env-separating the
  corpus would require the runtime reader to be env-aware too.
- Refresh is **manual only** (the UI "Refresh grounding" button), async with
  progress polling. No publish-time auto-population and no cron — both are
  deliberate v1 choices. A future increment could fire it from a scheduled app
  trigger via `trigger_runner`.
- Progress polling is client-driven (the UI polls the status endpoint); there
  is no server push. The background job runs to completion even if the BA
  closes the progress modal.
