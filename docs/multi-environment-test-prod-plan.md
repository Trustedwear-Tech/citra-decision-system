<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Multi-environment (test → prod) for SmartApps — design + plan

**Status:** Phases 0–6 implemented (smart-app-service compile + suite at baseline
25 fail/241 pass/4 err, zero regressions; citra-workflow dept-MCP tests pass; Citra-UI edited,
not runtime-verified here). NOT integration-tested on a live stack. Workflow-backed test apps
only partially isolated (see Remaining gaps).
**⚠️ On the acme demo the test MCP plane is NOT provisioned — the code is correct but
`TEST_DISCOVERY_SERVICE_URL` points at prod discovery, so test `mcp_action` writes commit to the
PROD source system. See §10 (TODO).**
**Date:** 2026-06-03

## 1. Problem

An LLM-authored write (a `staging_writer`'s `planned_writes`, or a direct `mcp_action`) is
**untrusted until executed** — `dry_run` validates shape, not effect, and you can't execute a
real write-test in prod. So a write-capable app is **built + tested against a non-prod
environment, then promoted to prod**.

## 2. The model (final — collection-prefix isolation)

Three facts make this small:

1. **The builder is ALWAYS test.** Every build / preview / BA-test runs against the test
   environment. smart-app-service **injects the test MCP-plane discovery URL** into the builder
   pod and resolves the build path to `test`. The builder is **environment-unaware** — it builds
   against whatever it's pointed at. (The data catalogue is read-only and shared — always prod.)
2. **smart-app-service owns environment resolution — never the UI, never the builder.**
   A request runs in one `environment` ("test" | "prod"), held in a request-scoped
   `contextvar` (`env_context.py`). It drives BOTH the Mongo collection routing AND the MCP-plane
   discovery URL the source resolver uses. The catalogue plane is NOT switched (shared, prod).
3. **Isolation is by COLLECTION NAME, not a separate db.** When `environment == "test"`, every
   smartapp collection routes to its `test_`-prefixed sibling **in the same db**
   (`smartapp_apps` → `test_smartapp_apps`). A test app's definition AND its operational data
   (queue/audit/pending_runs/trigger_state/records) live only there. **Promote = copy the spec
   docs from the `test_` collections to the prod collections.** No second db, no schema diff —
   **IT owns schema parity** (they own the MCPs + apply migrations to both).

The spec is environment-agnostic (it names `source_id`/`dataset`/`action_id`; the environment
decides which MCP + which collection those resolve to). The **runtime never changes** — it
calls smart-app-service `/api`, and resolution is server-side.

### Why collection-prefix (fail-closed)

A test app exists **only** in `test_smartapp_apps`. The env defaults to `prod`. So if any
handler forgets to bind the env, `get_apps_col()` reads the **prod** collection, the test app
isn't found → **404** — it can never run a test app against prod MCPs. A missed env-bind is a
safe 404, never a prod-MCP write. (A `preview_mode`-flag-in-one-collection model would instead
run the test app against prod on a miss — dangerous. A separate test **db** is equally
fail-closed but needs provisioning + index management; the prefix needs neither.)

## 3. How environment is resolved (server-side, deterministic)

| Context | Environment | How smart-app-service decides |
|---|---|---|
| Build / preview / builder-pod spawn / probe / sample / publish | **test** | `_bind_build_env()` — hardcoded test when a test env is configured (else prod, legacy). The builder pod's mid-conversation discovery/MCP calls bind test in `internal_routes._require_internal_claims` (kind=="builder"); the catalogue read is shared-prod regardless. |
| Run / read / write of a published app | **by store** | `_bind_app_env(slug)` → `resolve_app_environment(slug)`: present in the prod apps collection → `prod`; else present in `test_smartapp_apps` → `test`; else `prod`. RAW lookup (not the routed accessors). |

A not-yet-promoted app exists only in the `test_` collections → all access is test (the BA
tests it). A promoted app exists in the prod collections → officers run it as prod. To iterate
after promote, the BA `edit`s it (loads the source by store, spawns a fresh **test** build).

## 4. Config (Phase 0)

`.env` — **only ONE new key**: the test MCP-plane discovery URL. The data catalogue
(`data_discovery_service_url`) is read-only schema/metadata, identical across environments, so
it is NOT split — the builder always reads the prod catalogue. No test-db key either.

| Prod | Test |
|---|---|
| `DISCOVERY_SERVICE_URL` (MCP plane) | `TEST_DISCOVERY_SERVICE_URL` |
| `DATA_DISCOVERY_SERVICE_URL` (catalogue) | *(shared — no test variant)* |

`Settings` helpers: `discovery_url_for(env)` (default `prod`, fail-loud if test requested but
unset) and `test_environment_available` (true ⇔ `test_discovery_service_url` set — the only
gate; the catalogue is shared and the `test_` collections need no config).

## 5. What's implemented (Phases 1–3)

- **`env_context.py`** — dependency-free `current_env()` / `set_current_env()` contextvar
  (default `prod`), importable by deep modules without a circular import on `main`.
- **`main.py` routing** — `TEST_COLLECTION_PREFIX = "test_"`, `_route_col(prod_col, name)`
  (test → `_db["test_"+name]`), all operational + definition accessors routed
  (apps/agents/build_sessions/pending_runs/workflow_staging/trigger_state/smart_app_records/
  audit/audit_preview); `prompt_packs`/`skills` are shared platform assets (NOT routed).
- **Resolver + binders** — `resolve_app_environment`, `_bind_app_env`, `_bind_build_env`,
  `_resolve_build_env`. Bound at the entry of run / chat / tool / approve / detail /
  panel-data / field-options / document / runs / audit / workflow-run / workflow-triggers
  (by store) and build / edit / publish / probe / sample (test).
- **Discovery routing (MCP plane only)** — every `settings.discovery_service_url` read →
  `discovery_url_for(current_env())` across `proxy_clients`, `runtime`, `panel_data`,
  `capabilities`, `trigger_runner`, `publish_validators`, `main` (publish hydrate, document,
  builder-pod spawn). The catalogue (`catalogue_client`) is NOT routed — it always reads the
  shared prod `data_discovery_service_url`.
- **Test-collection indexes** — the critical UNIQUE indexes mirrored onto the `test_`
  collections in lifespan, gated on `test_environment_available`.
- **Promote** — `POST /apps/{slug}/promote-to-prod`: copy `app_spec` (+ referenced
  `agent_spec`) from the `test_` collections to the prod collections, version-bump, deploy.
  No re-validation/diff. Operational data is NOT copied (prod starts clean). Auth = same gate
  as edit. Strips any `_preview` suffix for the prod slug. (Distinct from the older
  `/promote` = intra-store preview→live promote, left intact.)

## 6. Lifecycle (builder builds+tests in TEST; the BA promotes in the UI)

1. **Build** — always test; builder reads the shared (prod) catalogue for schema + probes test
   data via the test MCP.
2. **Write-validation** — in test, execute each declared write (`dry_run=false`) against test
   data and assert (`/builder/probe execute:true`). **Cleanup = reseed/reset the test data**
   (IT/ops) — NOT an inverse action (inverse/rollback retired). "Test the updates like APIs."
3. **Publish → `test_` collections.** The builder publishes the normal slug to the test store,
   `audience=owner` (BA-only), **no `preview_mode`** — writes COMMIT against test data so the BA
   can exercise the app end-to-end (the dry-run-forcing that `preview_mode` did is gone for test).
4. **Builder hands the BA the test URL and STOPS.** No builder promote.
5. **BA tests** the app end-to-end in the test environment (real reads/writes via the test MCP;
   queue + audit in the `test_` collections). Broken app → ask the builder to fix. Broken
   MCP/schema → IT.
6. **BA promotes — in the Citra-UI.** The BA opens the **Test** tab (`GET /apps?scope=test`),
   reviews, and clicks **Promote to Prod** (audience picker) → `POST /apps/{slug}/promote-to-prod`
   copies the spec docs to the prod store + deploys. **Promotion is never the builder's action.**

## 7. Phases

- **Phase 0 — config + Settings helpers.** ✅
- **Phase 1 — env resolution + injection + collection routing.** ✅
- **Phase 2 — discovery routing across all source-resolution sites.** ✅
- **Phase 3 — promote-to-prod (spec copy).** ✅
- **Phase 4 — write-validation harness.** ✅ `/builder/probe` gains `execute:true` (mcp_action),
  HARD-gated to the test env (409 otherwise) → runs `/execute_action` `dry_run=false` against test
  data; response `committed:bool`. `citra-self-test` step 0f drives it (assert effect, not shape;
  cleanup = IT reseed, no inverse). 
- **Phase 5 — builder skills + safety gate.** ✅ W-07 ("write-capable app reaches prod ONLY via
  the BA's promote, never the builder") in `citra-safety-rules`; `citra-app-publish` rewritten —
  publish to test → smoke/visual gate → hand the BA the test URL → **STOP** (no builder promote).
  `promote-to-prod` takes an optional audience override.
- **Phase 6 — BA-promotes-in-UI + clean test model (this session).** ✅
  - **No more `preview_mode` for test.** Builder publishes the normal slug to `test_` with
    `audience=owner`; writes COMMIT in the test env (`dry_run` forced only for a legacy prod
    preview). `preview_mode`/`promote_preview` machinery left intact but unused by the test flow.
  - **`GET /apps?scope=test`** — lists the caller's own apps from the `test_` store (Test tab).
  - **Citra-UI** (`PowerAppsScreen.js` / `SmartAppService.js`): a **Test** scope tab + a
    **Promote to Prod** button on test cards → reuses the audience picker in `promote` mode →
    `promoteToProd(slug, {audience})`. `publish_options` env-bound so the picker works for a test app.
  - **citra-workflow engine discovery is env-aware** (`sources.py _discovery_base_url`): a
    `test` execution resolves the dept-MCP `/query` endpoint against `TEST_DISCOVERY_SERVICE_URL`
    when set, else falls back to prod discovery (preserves existing IT test-execution behaviour).
    Mirrors `resolve_connection(environment=…)`. citra-workflow suite: dept-MCP source tests pass;
    8 unrelated pre-existing failures (code_exec sandbox + real-LLM e2e).
  - **Bugs fixed in review:** `mint_runtime_token` + `builder_preview_smoke` now env-bound (were
    404ing test apps).

### Remaining gaps (workflow-backed test apps — NOT fully closed)
The engine's **discovery** is now env-aware, but full isolation of a *test app's bound workflow*
also needs: (a) the **trigger path** (SmartApp→workflow execute / scheduler) to pass
`environment="test"` for a test app, and (b) the engine's **`staging_writer`** to write to a
`test_`-routed staging collection (today it writes the prod `smartapp_workflow_staging`, which the
test app's env-routed inbox won't read). Until both land, workflow-precomputed recommendations for
a test app won't appear in its test inbox. The on-demand `/run` staging path IS isolated.

## 8. Explicitly dropped (kept simple)

- ❌ A separate test **Mongo db** — replaced by `test_`-prefixed collections in the same db.
- ❌ Promote-time **schema-parity gate / diff** — IT owns parity.
- ❌ Threading `environment` through the **builder** — builder is always test, env-unaware.
- ❌ **UI-driven** environment — smart-app-service resolves it server-side.
- ❌ A `preview_mode`-flag-in-one-collection isolation — not fail-closed (a missed env-bind
  would run a test app against prod). The existing `preview_mode`/`promote_preview` machinery
  is left intact as a separate prod-side owner-preview feature, NOT reused for test↔prod.

## 9. Caveats (residual risk, owned by IT/ops)

- **Test ≠ prod guarantee** rests on IT keeping the schemas identical — no automated catch (by
  design). Drift surfaces as a prod failure, handled as an ops incident.
- **Test-data discipline** (schema-identical, representative, PII-scrubbed, refreshed) is IT's
  ongoing job — the real cost.
- **Side-effecting writes** (email/SMS) in the test MCP must be sandboxed or the test env spams.
- **Reads are safe in prod**; only writes need the test env.
- **Management ops** (set_audience / transfer / lifecycle / inheritance-policy) are NOT
  env-bound — they 404 on a test app (fail-closed). Promote handles the test→prod transition;
  these run on prod apps.

Phases 1–3 touch source resolution + the operational store and are **compile + unit verified
only** — NOT integration-tested from a dev box. Integration-test each on a live stack (test +
prod discovery + Mongo) before it goes live.

## 10. TODO — acme demo: the test MCP plane is NOT provisioned

**Status: NOT BUILT** — an infra gap, not a code gap. Verified against prod 2026-07-16.

Everything in §2–§6 is implemented, but it assumes a **separate test MCP plane exists**. On the
acme demo it does not: `TEST_DISCOVERY_SERVICE_URL` is set to the **prod** discovery, so
`discovery_url_for("test")` and `discovery_url_for("current")` return the same service.

| Setting (running prod smart-app-service) | Value |
|---|---|
| `DISCOVERY_SERVICE_URL` (prod MCP plane) | `http://discovery-service-prod:9000` |
| `TEST_DISCOVERY_SERVICE_URL` (test MCP plane) | `http://discovery-service-prod:9000` ← **same** |
| `DATA_DISCOVERY_SERVICE_URL` (catalogue) | `http://data-discovery-service-prod:8095` (shared — correct, per §4) |
| `test_environment_available` | `True` — the test env is "on" but **not data-isolated** |

### What this breaks

`test_environment_available` is True, so builds/BA-tests bind `test` and definitions route to the
`test_` collections as designed. But source resolution
(`proxy_clients.call_dept_mcp_execute_action` → `resolve_source(discovery_url_for(current_env()))`)
lands on the **prod MCP**:

- ✅ **Definitions** (`app_spec`/`agent_spec`) — isolated (`test_` collections).
- ✅ **App-owned writes** (`smart_app_records` data sources → `write_app_record`) — isolated
  (env-routed to `test_smart_app_records`).
- ❌ **MCP-backed SoR writes** (`mcp_action`, `dry_run=false`) — **NOT isolated**. Both the BA-test
  `/approve` (§6 step 5) and the Phase-4 write-validation probe (`execute:true`) commit to the
  **prod source system**.

The Phase-4 gate ("HARD-gated to the test env — a real write can NEVER run against prod from the
builder") checks `current_env()=="test"` and assumes test ⇒ a separate MCP. With both planes equal
the gate **passes** and the write still lands on prod. The fail-loud in `discovery_url_for` only
guards `TEST_DISCOVERY_SERVICE_URL` being **unset** — not it being set **to prod**. Note that
simply unsetting it is NOT a fix: `test_environment_available` would go False and builds would run
fully in prod (§3), losing the collection isolation too.

### TODO

- **T0 — guardrail (do first; code-only, ~1h).** At startup, and before an `mcp_action` commit in
  the test env, detect `test_environment_available and discovery_url_for("test") ==
  discovery_url_for("current")` → log LOUD; optionally refuse `execute:true` / test-env
  `mcp_action` commits. Closes the silent misconfiguration permanently and catches future drift.
- **T1 — test source data.** Seeded, schema-identical, PII-scrubbed copy of each acme source
  system named in `sources.json` (§9 "test-data discipline" — the real cost). Reset = IT reseed
  (no inverse action, per Phase 4).
- **T2 — test dept-MCP (`acme-power-mcp-test`).** Same GHCR MCP image; test `sources.json` =
  prod's with connections repointed at T1, keeping **identical dataset_ids / schemas / ontology**
  (`artifact_role`, `fraud_screening`, `write_actions`) so the shared catalogue stays valid.
  `DISCOVERY_URL` → T3, own service key, `CRAWL_ENABLED=true`, audit → `/api/audit/ingest` tagged
  test. Sandbox side-effecting writes (§9: email/SMS).
- **T3 — test discovery (`discovery-service-test`).** Own store so its `source_id → MCP` registry
  is isolated; receives T2's boot registration. Co-locate with the prod acme MCP (AWS Common).
- **T4 — wire it.** Vault `prod/smart-app-service`: `TEST_DISCOVERY_SERVICE_URL` → T3. Leave
  `DISCOVERY_SERVICE_URL` and `DATA_DISCOVERY_SERVICE_URL` unchanged. Same for any other
  builder-path service (citra-workflow's `sources.py _discovery_base_url` already reads it).
- **T5 — verify.** A test build reads T1 data via T2; a BA-test `/approve` of an `mcp_action`
  commits to T1 with **prod untouched**; a prod run still resolves to the prod MCP; T0 goes quiet.

The catalogue stays **shared prod** (§4) — no test catalogue; schemas are identical by construction.

**Exposure today** depends on whether acme's apps declare `mcp_action` writes vs app-owned
(`smart_app_records`) writes — if app-owned, test is already isolated and this is hardening.
**Not yet audited.**
