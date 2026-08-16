<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Anti-Hallucination Grounding — Operator Guide

This document summarizes the anti-hallucination ("grounding") controls now
active across the Citra-Service RAG surfaces.

## Affected features

- Main chat (`agentic_rag/orchestrator.py`, `query.py`, `streaming_response.py`)
- Quick chat (`api/quick_chat.py`)
- Diagrams (`api/diagram.py`)
- Mind maps / entity extraction (`api/entity_extraction.py`)
- Composer (`composer_context.py`)
- Presentations (`presentation_api.py`)
- Printable / reports (`printable/printable_api.py`)
- Knowledge-graph queries (`graph/query_service.py`)
- Action chat: runs inside the NeMo-Guardrails-wrapped sandbox; grounding is
  enforced at the sandbox boundary and is intentionally not duplicated
  in-process.

## Behaviours enabled

1. **Strict grounding prefix** is prepended (inside the persona's cached
   `static_section`) so xAI prefix caching is preserved while every LLM call
   is forced to stay inside provided context.
2. **Source-origin tagging** — every chunk is tagged
   (`vault` / `internet` / `enterprise` / `structured`) and rendered with
   typed prefixes (`[Vault Document]`, `[Internet Source]`, …) plus a
   `Source Origin:` line. Citations emitted by the model are tagged the same
   way (`[vault:doc_id]`, `[internet:hostname]`, `[structured:table_name]`).
3. **Retrieval guard** (`rag/retrieval_guard.py`) computes a
   `ContextQuality` score and decides whether to grant internet search this
   turn. Empty / low-signal contexts trigger a "no context" wrapper that
   forces an explicit IDK fallback instead of training-knowledge guesses.
4. **Profession-aware temperature** (`llm/llm_defaults.py`) — factual=0.2,
   structured=0.3, tool_agent=0.4, conversational=0.4, creative_layout=0.5,
   creative=0.6. High-stakes professions get the lowest temperature.
5. **Critic verification** (`verification/critic_agent.py`) runs after the
   main-chat / KG response and flags claims unsupported by the merged
   context. Findings are surfaced in `state.metadata.grounding`.
6. **Reranker default-on** with cross-encoder re-scoring + an optional
   post-rerank min-score floor (`MIN_CHUNK_SCORE`).
7. **Prometheus metrics** (`metrics.py`):
   `rag_context_empty_total`, `rag_chunks_used`, `rag_rerank_dropped_total`,
   `critic_unsupported_claims`, `critic_runs_total`,
   `llm_temperature_observed`, `internet_grounding_total`.

## Environment flags

| Flag                          | Default | Purpose                                                                                  |
|-------------------------------|---------|------------------------------------------------------------------------------------------|
| `GROUNDING_STRICT`            | `true`  | Enable strict-grounding prefix injection (UI may surface IDK fallbacks).                 |
| `RERANKER_REQUIRED`           | `true`  | Treat reranker as required path; falls back to similarity sort if unreachable.           |
| `ENABLE_RERANKER`             | `true`  | Master switch for cross-encoder reranking (consumed by `reranker.py`).                   |
| `MILVUS_ABSOLUTE_MIN_SCORE`   | `0.10`  | Hard floor applied to all Milvus search results.                                         |
| `MIN_CHUNK_SCORE`             | `0.0`   | Optional post-rerank min-score floor (set 0.30–0.40 for strict deployments).             |
| `MIN_VAULT_TRUST`             | `0.75`  | Vault-trust threshold used by the retrieval guard.                                       |
| `LOW_SIGNAL_SCORE`            | `0.45`  | Below this, retrieval guard treats the context as low-signal.                            |
| `CRITIC_ENABLED`              | `true`  | Run main-chat critic verifier after generation.                                          |
| `KG_CRITIC_ENABLED`           | `true`  | Run critic verifier for KG summary answers.                                              |
| `CRITIC_MAX_CONTEXT_TOKENS`   | `8000`  | Truncates context fed to the critic.                                                     |
| `EXPOSE_CITATIONS`            | `true`  | UI flag: include citation tags in surfaced response payloads.                            |
| `LLM_TEMPERATURE_OVERRIDE`    | _unset_ | When set, replaces all profession-derived temperatures (debugging only).                 |

## Recommended posture

- **High-stakes** (legal/medical/finance):
  `MIN_CHUNK_SCORE=0.35`, `MIN_VAULT_TRUST=0.80`,
  `CRITIC_ENABLED=true`, `KG_CRITIC_ENABLED=true`,
  internet only when explicitly enabled by the user.
- **General productivity**: defaults are appropriate.
- **Creative / brainstorming**: keep defaults; profession-aware temperature
  already raises the ceiling for `creative` profiles.
