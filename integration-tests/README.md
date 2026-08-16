<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra-AI integration tests

End-to-end test harness for the Smart App + Smart Dashboard pipeline.
Stubs the dept-MCPs (SAP / Salesforce / SQL / policies) and the LLM so
tests are deterministic and free of external dependencies.

## What's tested

| # | Scenario | File |
|---|---|---|
| 01 | Smart App build → publish (skill, agent, workflows persisted) | `scenarios/test_01_smartapp_build_to_publish.py` |
| 02 | wf_refresh_history pulls SAP claims, indexes Mongo + Milvus | `scenarios/test_02_refresh_from_history.py` |
| 03 | Few-shot pre-injection at runtime (1 LLM call vs 3) | `scenarios/test_03_few_shot_pre_injection.py` |
| 04 | NeighborSamplesTool canonical + neighbors modes | `scenarios/test_04_neighbor_samples_tool.py` |
| 05 | Preprocess workflow + watermark monotonicity | `scenarios/test_05_preprocess_workflow_watermark.py` |
| 06 | smart_app_invoke node concurrency cap | `scenarios/test_06_smart_app_invoke_concurrency.py` |
| 07-09 | Dashboard narrator (brief / why / nl-filter / anomaly) | `scenarios/test_07_*.py` |
| 10 | Publish-time guards (typo'd source_id, missing config) | `scenarios/test_10_publish_guards.py` |
| 11 | Cold-start graceful degradation | `scenarios/test_11_cold_start.py` |
| 12 | Cross-tenant isolation | `scenarios/test_12_cross_tenant_isolation.py` |
| 13 | Flagship E2E motor-claims happy path | `scenarios/test_13_e2e_motor_claims.py` |

## Stubs

```
fake-mcp-server/        FastAPI server registering 4 sources:
                          - sap_claims    (structured, supports_history=true)
                          - salesforce    (soql)
                          - sql_server    (sql)
                          - policies_rag  (semantic)
                        Implements POST /query and POST /datasets so
                        discovery + dept-MCP probes work the same as prod.

fake-llm/               OpenAI-compatible /v1/chat/completions endpoint.
                        Returns canned responses keyed by prompt patterns
                        (claims_amount, narrator, brief, etc.).
                        prompt_patterns.yaml is the dispatch table.

fake-customer-notify/   Receives wf_post_decision webhooks.
                        Stores them in-memory; tests assert on count + payload.
```

## Layout

```
integration-tests/
  conftest.py                       pytest session hooks, shared fixtures
  docker-compose.test.yml           one-shot stack (Mongo + Milvus + Redis + 3 stubs)

  fake-mcp-server/                  see above
  fake-llm/
  fake-customer-notify/

  fixtures/
    tenants.json, users.json
    motor_claims.json               5,000 rows
    policies/*.txt                  ~50 docs

  helpers/
    db_reset.py                     drop test DB at session start
    jwt_mint.py                     mint test JWTs / X-User-Id headers
    seed.py                         load fixtures into Mongo
    builder_session.py              spawn build session + tail SSE
    assert_helpers.py               rich Mongo assertions

  scenarios/                        the actual test cases (one file each)
```

## Running

```bash
# 1. spin up the stack (Mongo, Milvus, Redis, stubs)
docker-compose -f docker-compose.test.yml up -d

# 2. seed test fixtures
python helpers/seed.py

# 3. run the suite
python -m pytest scenarios/ -v

# 4. teardown
docker-compose -f docker-compose.test.yml down -v
```

## Environment guarantees

- Test database name: `citra_integration_test` (dropped at session start).
- Milvus collection prefix: `itest_<run_id>_` (cleaned at session end).
- Redis DB: 15.
- Mock LLM is the default; flip `LLM_MODE=real` to run nightly with OpenAI.

## Adding a new test

1. Add a scenario under `scenarios/test_NN_name.py`.
2. Use `helpers.seed.reset_and_seed()` in your fixture.
3. Use `helpers.builder_session.run_build()` to drive a build.
4. Assert on Mongo state via `helpers.assert_helpers`.
