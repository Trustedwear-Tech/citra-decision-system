<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Performance parity — extend the SQL optimizations to every connector

`source-mcp-template` is the shared data plane — it's called by many apps, so it
is a HIGH-PERFORMANCE path. The smart-performance work below was done for the
**SQL / DuckDB** path. **When we light up SAP, Salesforce, REST, BigQuery, or any
data-broker connector for real workloads, give it the SAME treatment.** This file
is the checklist; treat the SQL path as the reference implementation.

## What was done for SQL/DuckDB (the reference)
- **Connection/engine pooling** — engines memoized & reused per connection config
  (no per-query engine build); `connectors/sql_connector.py:_get_engine`.
- **Mongo client pooling** + bounded `run_aggregation` (`$limit` + `maxTimeMS`);
  `connectors/mongo_connector.py`.
- **Plan cache** (NL→SQL) — `plan_cache.py`, 24h **sliding** TTL: a repeat question
  reuses the generated query and skips the planner LLM entirely.
- **Count-probe cache** — `plan_cache.get_count/set_count`, 24h fixed TTL, busted on
  any write via per-source `data_version`. Caches ONLY the size-estimate `count(*)`
  probe (list-vs-aggregate routing), never row/aggregate data — data stays live.
  Wired at the SQL probe sites: `agentic_sql_planner.py` (`probe_count`,
  `answer_rows` guard) and `query_planner.py` (count-first).
- **Schema/metadata caching** — catalogue introspection TTL cache; OData `$metadata`
  cache.
- **Bounded results + statement timeouts**, and **fail-loud** on connector errors
  (`query_engine.py` wraps connector calls into `ExecutionResult.error`).

## Parity gaps to close per connector (when used for real)
For OData/SAP (`odata_connector`, `sap_rfc_connector`), Salesforce
(`soql_connector` + `planners/nl_to_soql`), REST (`rest_connector`), BigQuery
(`bigquery_connector`), GCS/file (`gcs_connector`, `file_connector`):

1. **Client/session reuse** — these create a fresh `httpx`/client/session per call
   today (deferred in the perf pass). Memoize a pooled client per connection
   (mirror `sql_connector._get_engine` / `mongo._get_mongo_client`), with a TTL'd
   auth-token cache where the backend uses OAuth/CSRF (OData already token-caches).
2. **Plan cache for the extension query shapes** — the plan cache currently keys on
   the generated SQL string; the extension kinds (`odata`/`soql`/`rest`) flow
   through `_run_extension` and their NL→query planners (`nl_to_odata`,
   `nl_to_soql`) without the same plan-reuse. Wire plan-cache reuse for the
   generated OData query dict / SOQL string so a repeat NL question skips the
   planner LLM there too.
3. **Count-probe cache** — the count-cache is wired only on the SQL/DuckDB probe
   sites. SAP/SOQL/REST have their own "how big is this result" mechanisms
   (`$count`, `SELECT COUNT()`, paged totals). Cache those estimates the same way
   (per-source `data_version`, write-bust) so the list-vs-aggregate routing is
   reused. Keep the rule: cache the ESTIMATE, never the data.
4. **Metadata caching** — OData `$metadata` is cached; do the same for Salesforce
   `describe`/global describe, BigQuery `INFORMATION_SCHEMA`, and SAP RFC metadata
   so describe/introspection isn't re-fetched per query.
5. **Bounded results + timeouts** — enforce per-call row caps + request timeouts on
   every external connector (OData `$top` paging already capped; audit REST/SOQL/
   BigQuery for unbounded pulls and missing timeouts).
6. **Fail-loud** — ensure broker errors propagate as `ExecutionResult.error`
   (started in `query_engine.py`), not swallowed to empty.

## Cross-cutting (any backend, when traffic grows)
- **Embedding cache** for query-text vectors (dataset selection) — deterministic per
  model, high reuse.
- **Dataset-selection cache** — `(scope, normalized_question) -> dataset_ids`.
- **Cache-stampede single-flight** on cold hot keys.
- Move the in-process introspection cache to Redis so all MCP replicas share it.

> Principle to preserve everywhere: cache PLANS and size ESTIMATES (reusable),
> never row/aggregate DATA (always re-executed live); scope every cache key by
> source/tenant; invalidate on write via `data_version`; fail-open on cache errors.
