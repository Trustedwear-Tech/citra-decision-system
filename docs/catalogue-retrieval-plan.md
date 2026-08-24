<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Catalogue Retrieval Plan — MCP (`dept_sources`) vs Builder (`data_catalogue`)

_Last updated: 2026-06-01_

## Problem

The catalogue is read in two places, and both must scale to a large org (1000s of
tables) without flooding prompts or hammering stores:

1. **MCP NL→query** (`source-mcp-template`): when `/query` carries no `dataset_id`/
   `source_id` (or a `source_id` whose source has many tables),
   `catalogue_index._vector_search` embeds **every** dataset signature **in-process,
   per query**. O(all tables) per call, no persistent index.
2. **SmartApp builder** (`/builder/catalogue`): fetches **all** tenant datasets
   (cap 500, then silently truncates) and cross-encoder-reranks the whole list.
   O(all tables) per interview.

A prior iteration had the **MCP read `data_catalogue`** for column enrichment. That
is architecturally wrong (see Decision) and is to be reverted.

## Verification — does `data_catalogue` have table schema?

**Yes.** `CatalogueEntry`/`CatalogueColumn` carry full columns (`name`,
`physical_name`, `type`, `semantic_type`, `nullable`, `pii`, `distinct_values`,
`range`, `description`, PK/FK), plus `relationships`, `read_via`, `write_actions`.
It is a **superset** of `dept_sources` — crawled from the MCP `/datasets` + enriched.
Caveat: a pure-semantic source with no declared columns stores `columns=[]`.

But it is a **derived copy built by crawling the MCP**, so the MCP must not read it
back.

## Decision

- **MCP uses its LOCAL `dept_sources` ONLY.** Reasons: (1) it owns execution truth
  (`read_via`/connection); (2) it's always present (loaded at startup) — `data_catalogue`
  can be empty (pre-crawl), stale, or unreachable; (3) avoids the circular dependency
  (the crawl builds `data_catalogue` *from* the MCP); (4) decouples the NL hot path.
- **Builder uses `data_catalogue`** via **query-driven vector search**, fetched
  **on-demand by the BA's query** — the cross-source discovery/recall layer. Never
  fetch-all.
- **Column enrichment (`distinct_values`/`range`) reaches the MCP via INGESTION**
  (Dept Data Flow workflow profiling writes them into `dept_sources`), NOT by the MCP
  reading `data_catalogue`.

```
                 ┌───────────────── dept_sources (per-MCP, local, authoritative) ──────────────┐
ingestion ──────▶│ schema + read_via + (NEW) distinct_values/range + (NEW) signature embeddings │──▶ MCP NL→query (self-contained)
(Dept Data Flow) └──────────────────────────────────────────────────────────────────────────────┘
        │ crawl (reads MCP /datasets)
        ▼
   data_catalogue (enterprise, enriched, VECTOR-INDEXED) ──▶ Builder query-driven search (/catalogue/search) ──▶ rerank ──▶ prompt
```

## Track A — MCP (`dept_sources` only, self-contained)

- **A0. Revert the `data_catalogue` dependency.** Remove `catalogue_enrichment.py`,
  `Settings.data_catalogue_*`, the `enrich_datasets` call in
  `catalogue_index.select_datasets`, and the utility compose/.env `DATA_CATALOGUE_*`.
  **Keep** `nl_query_max_datasets`, the `_vector_search(candidates=…)` param, and the
  within-source ranking — those operate on `dept_sources` (local), which is correct.
- **A1. `distinct_values`/`range` INTO `dept_sources` at ingestion.** Add a profiling
  step to the Dept Data Flow workflow (`SQLSourceNode`/`CatalogueSinkNode`): for
  low-cardinality columns compute `distinct_values`; for numerics/dates compute
  `range`; write onto `dept_sources.catalogue.datasets[].columns[]`. `nl_to_sql`
  already renders these. (Demo `sources.json` can hand-author them in the interim —
  it already encodes enums in column descriptions.)
- **A2. Table matching at scale — LOCAL embedding cache.** Persist a `signature`
  embedding per dataset on the `dept_sources` doc at ingestion (same step as A1). The
  MCP loads them ready-made at startup (no per-query embed-all). `_vector_search`
  cosine-ranks against the cached vectors (+ optional reranker) for the no-`source_id`
  and large-source paths. If embeddings absent → current in-process embed (fallback).
- **A3. Fail-open preserved:** no embeddings → enumeration order; source ≤ cap → no
  ranking.

## Embedding homes (recall stage) — the asymmetry

Both consumers do **recall (embeddings) → precision (reranker-service)**. Where the
embeddings live differs by scale:

| | Builder / `data_catalogue` | MCP / `dept_sources` |
|---|---|---|
| Scope | enterprise-wide (1000s) | one dept-MCP (10s–100s) |
| Recall index | **Milvus** (dedicated collection) | **in-memory cosine** over precomputed vectors |
| Embed at | crawl (data-discovery) | ingestion (Dept Data Flow) → stored on `dept_sources` |
| Reranker | yes | yes |
| Uses Milvus? | yes (catalogue) | no (Milvus only for its RAG docs) |

**Decision: a DEDICATED Milvus collection for the catalogue** (separate from
`mcp_<dept>_<source>` RAG collections). The MCP stays in-memory (its set is bounded);
only the enterprise builder needs an ANN index.

## Track B — Builder (`data_catalogue` query-driven search)

- **B0. Correctness now:** remove the silent 500/2000 truncation; log when capped.
  **(DONE 2026-06-01.)**
- **B1. Vector index on `data_catalogue` — DONE 2026-06-01.** Crawl embeds the dataset
  signature (`name + description + columns`) and upserts into a **dedicated Milvus
  collection** (`catalogue_milvus_collection`, fields: pk/embedding/tenant_id/dept_id/
  source_id/dataset_id, COSINE/AUTOINDEX). New `catalogue_vectors.py`
  (embed + ensure/upsert/search), config `catalogue_vector_enabled` + `milvus_*` +
  `embedding_*`, crawler hook (`index_entries` after Mongo upsert), `pymilvus` dep.
  **Fail-open** (disabled/unavailable → no-op + Mongo fallback). `dept_id` field is
  stored but not yet filtered (reserved for B4 RBAC pass).
- **B2. `GET /catalogue/search?q&top_k&source_id?` — DONE 2026-06-01.** Embed `q` →
  Milvus ANN filtered by JWT `tenant_id` (+ `source_id`) → hydrate ranked
  `dataset_id`s from Mongo in rank order. Falls back to a bounded tenant list when
  vectoring is off. **TODO:** add `dept_id` filtering once B4 stamps it.
- **B3. Builder fetches ON-DEMAND by query:** the discovery skill / `/builder/catalogue`
  calls `/catalogue/search` with the BA's goal **when it needs datasets** → ANN recall
  (~top 150) → cross-encoder rerank → top-50 → prompt. No fetch-all. `needs_scope`
  becomes a soft hint, not a gate.
- **B4. dept-scope the builder palette** (filter by caller `dept_ids`) — reduces the
  set and closes the RBAC gap (catalogue currently filters by tenant only).

## Shared concerns / risks

- **Embedding parity (Builder/Track B):** the crawl (writes) and `/catalogue/search`
  (query) must use the **same** embedding model/dim; store `embedding_model` per doc;
  on mismatch, fall back. The **MCP (Track A) has no parity issue** — it owns its own
  local embeddings end-to-end and never reads `data_catalogue`.
- **Atlas tier:** confirm `$vectorSearch` is available; else use Milvus for the
  catalogue index.
- **No silent drops** anywhere — log every cap/fallback (RULE #1).
- **Live introspection dropped declared column semantics (fixed 2026-06-01):**
  for an introspectable SQL source whose backend is reachable (the demo case),
  `source-mcp-template/catalogue._live_introspect_full` *replaced* the declared
  `columns[]` with physically-introspected columns + PK/FK — but Postgres carries
  no column descriptions/enums (no `COMMENT ON`), so every hand-authored column
  description + `distinct_values` was LOST on the way to `data_catalogue`. The
  builder consumes exactly these (`distinct_values` → decision classes;
  description/semantic_type → rerank + binding). Fix: introspection still owns
  PHYSICAL truth (type/nullable/PK/FK) but now OVERLAYS the declared semantic
  layer (description, distinct_values, range, sensitivity, semantic_type) onto
  the introspected columns where the DB left a gap. Table-level description was
  always preserved. Test: `test_live_introspection_preserves_declared_semantics`.
- **One-source-one-tool vs crawl granularity (fixed 2026-06-01):** discovery
  registers each `dept_source` as its OWN tool (so the routing LLM picks a data
  domain, not a coarse MCP) — `source-mcp-template/registration.py`. But an MCP's
  `/datasets` is **server-wide**. So `crawl_all` now **dedupes registered
  source-tools to distinct MCPs by base URL** (`_resolve_base_url`) and crawls
  each MCP once; otherwise an N-source MCP got re-crawled + **re-embedded** N
  times (utility: 5×). Per-source `dept_id`/visibility is unaffected (applied
  per-dataset from Mongo `dept_sources` by `source_id`, not from the tool record).

## State as of 2026-06-01

- **DONE — A0 (revert):** removed `catalogue_enrichment.py`, `Settings.data_catalogue_*`,
  the `enrich_datasets` call, utility compose/.env `DATA_CATALOGUE_*`,
  `test_catalogue_enrichment.py`. MCP has zero `data_catalogue` coupling.
- **DONE — B0 (truncation loud):** data-discovery `/catalogue` counts true total +
  logs `TRUNCATED`; smart-app `catalogue_client` + `/builder/catalogue` log it.
- **DONE — B1+B2 (vector recall):** `data-discovery/catalogue_vectors.py` (dedicated
  Milvus collection, embed + upsert + ANN search), config + crawler hook +
  `GET /catalogue/search` + `pymilvus` + `tests/test_catalogue_vectors.py`. Fail-open.
- **KEEP:** `nl_query_max_datasets`, `_vector_search(candidates=)` within-source ranking
  (`source-mcp-template/tests/test_catalogue_select.py`), NL→SQL self-correction + GLM-safe planner.
- **DONE — B3:** `catalogue_client.fetch_catalogue_search()` + `/builder/catalogue` now
  does recall (`/catalogue/search`, top-150) → cross-encoder rerank → top-50 when a goal
  is present, else the plain list. **No silent fallback** between the two paths
  (RULE: fail loud) — a `DiscoveryError` raises rather than quietly switching paths.
- **FAIL-LOUD (corrected):** `catalogue_vectors.search` returns `None` ONLY when vectoring
  is *disabled* (a configured mode); any enabled-state failure RAISES. `/catalogue/search`:
  disabled → bounded Mongo list (configured); enabled+failure → 503; enabled+empty-but-
  `count_documents>0` → **503 "index not populated, run /crawl/run"** (don't mask a missing
  crawl as an empty/degraded palette); genuinely empty → `[]`. Crawl `index_entries` stays
  non-aborting but logs ERROR. (Earlier draft had an empty-recall→list silent fallback —
  removed; it masked an unpopulated index.)
- **DONE — B4:** `CatalogueEntry.dept_id` + `public_within_org`; crawler stamps them from
  `dept_sources`; `catalogue_vectors` stores `public` + dept-filters in `search()`;
  `/catalogue` + `/catalogue/search` apply `_dept_visibility_filter` (org_admin/super
  bypass; dept-overlap OR public OR unstamped fail-open).
- **DONE — A2-MCP:** `catalogue_index._vector_search` uses a dataset's precomputed
  `embedding` when its dim matches the query vector, else embeds on the fly.
- **DONE — A1+A2-ingest:** citra-workflow `SQLSourceNode` derives `distinct_values`/`range`
  from sample rows (no extra queries); `CatalogueSinkNode` precomputes a per-dataset
  signature `embedding` (fail-open) — both flow to `dept_sources.catalogue.datasets[]`.

## Operational notes to enable B1/B2 (per env)

Set on data-discovery: `CATALOGUE_VECTOR_ENABLED=true`, `MILVUS_URI`, `MILVUS_TOKEN`,
`CATALOGUE_MILVUS_COLLECTION`, `EMBEDDING_BASE_URL/API_KEY/MODEL`, `EMBEDDING_DIMENSION`
(must match the builder's query-embed dim; 768 for the demo). Then run `/crawl/run` to
populate both Mongo `data_catalogue` and the Milvus collection. Disabled by default →
`/catalogue/search` falls back to the bounded Mongo list.

## Recommended order (remaining)

1. **B3** — flip `/builder/catalogue` (and the discovery skill) to `/catalogue/search`
   (recall→rerank). The actual scale win for the builder.
2. **B4** — stamp `dept_id`/visibility into `data_catalogue` + filter (RBAC + smaller set).
3. **A1+A2** — ingestion profiling + persisted local embeddings → MCP in-memory recall.
