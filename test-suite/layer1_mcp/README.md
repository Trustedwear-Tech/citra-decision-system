<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Layer 1 — MCP contract

The MCP boundary must, for every source kind, (a) return correct rows on the
happy path and (b) **fail loud** on any bad state — never swallow, never crash
the shared execution thread (RULE #1). Coverage is the `(kind, op, state)` matrix
from `../vocabulary.py` (100 legal cells across 9 kinds).

```
test_mcp_contract_matrix.py   REST, in depth — real rest_connector × httpx.MockTransport
                              across happy/empty/missing_param/upstream_error/non_json/ssrf
test_mcp_kinds_contract.py    ALL kinds — the shared query_engine.execute path
_emit.py / conftest.py        per-cell emission -> mcp.json (multi-file safe)
```

## Two tiers (test_mcp_kinds_contract.py)
- **Surfacing sweep** — every kind (`sql, duckdb, odata, soql, rest, bigquery,
  sap_rfc, semantic`), given a bad/absent backend, must return a clean error
  (`rows==[]`, `error` set) and **never raise**. A misconfigured source must not
  crash the shared MCP thread.
- **Real embedded backends** — `sql` (sqlite tempfile) and `duckdb` (csv
  tempfile) run genuine queries and return real rows (happy + empty). These need
  `pip install SQLAlchemy duckdb`.

## A real bug this surfaced
The sweep caught that `query_engine._run_bigquery` / `_run_sql` let a config
error **escape** `execute()` (crashing the worker thread), while the async
branches (`_run_odata/_run_soql/_run_rest`) explicitly wrapped and surfaced the
same class of error. Fixed by routing all synchronous branches through a single
`_sync_guarded()` that logs + returns `result.error` — uniform fail-loud across
all 9 kinds. (See the `query_engine.py` change committed alongside.)

## What isn't reachable in isolation (honest gaps)
happy/empty/describe/list for `odata, soql, bigquery, sap_rfc` need their live
services; `mongodb`'s query path runs through `catalogue._run_mongo`, which pulls
the full service context (`fastapi`, `motor`) and can't run as a unit here;
`semantic` needs a Milvus + embeddings. Those cells are covered by service-level
integration, NOT claimed here — which is why MCP sits well below the 0.90 gate
(21/100). Raising it means a compose-backed integration lane with real/seeded
backends, or dept-specific connector stubs.
