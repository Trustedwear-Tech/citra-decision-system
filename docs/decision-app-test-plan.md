<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Decision App Platform — End-to-End Test Plan

Covers the three surfaces on `smart-app-service` (:9100) + `citra-app-runtime`
(:3100) + `citra-app-builder` pod + dept-MCP (`mcp-demo-acme-power`):

1. **Builder** — LLM authoring loop (`/build*`, `/apps/{slug}/edit`)
2. **App-build output** — published AppSpec/AgentSpec + publish validators
3. **Runtime UI** — every panel type + detail section, UI copy
4. **Decision API** — headless invocation (`/apps/{slug}/tool/{name}`, `/run`, `/chat`, runtime token)
5. **Governed decision loop** — recommend → approve → override → reject; auto-recommend / auto-process

**Environments:** run the full matrix in **test** (`test_*` collections), promote a subset to **prod**.
Use the **acme-power** demo only (source `field_operations.*`, `org_id=acme-power`, MCP `mcp-demo-acme-power`).

The executable harness lives in [`smart-app-service/tests/e2e/`](../smart-app-service/tests/e2e/) — see its README to run.

---

## 1. Harness & fixtures (build once, reuse)

| Item | Detail |
|---|---|
| **Auth** | Mint HS256 JWTs with `JWT_SECRET` (Vault `prod/smart-app-service`). Role matrix: `super_admin`, `org_admin`, `dept_admin`, plain member; `org_id/tenant_id=acme-power`; SA membership via `service_account_admin_of`. |
| **Runtime token** | `POST /apps/{slug}/runtime/token` → `?_t=` launch token + `citra_user_token` cookie. |
| **Seed data** | `equipment_inspections` incl. fraud fixtures (3× reused photo, 2× reused report, backdated) + a clean CRUD source + a source with a media column. |
| **Fixture apps** | (a) fraud screen, (b) form-CRUD, (c) dashboard, (d) overlay/thread — so every panel/tool kind is covered. |
| **Golden files** | Snapshot each published AppSpec JSON + each runtime page accessibility snapshot for regression diffing. |

Test layers per area: **unit** (validators, fraud hash/format fns) → **contract** (endpoint req/resp, auth) → **E2E** (builder→publish→render→decide).

---

## 2. Builder

| # | Case | Expected |
|---|---|---|
| B-1 | `POST /build` → `session_id`+`pod_id`; SSE `/build/{sid}/chat/stream` emits `thinking/tool_call/tool_result/message/done` | pod spawns; events well-formed |
| B-2 | Multi-turn authoring to `/publish` (fraud app) | published app renders |
| B-3 | Discovery from `data_catalogue` (NOT dept_sources); source absent there is not offered | no phantom sources |
| B-4 | TOOL_CATALOGUE (X-01): every emitted tool kind present in `_builder_env` — esp. `consistency_check`, `fraud_synthesis` | no "unknown tool" at publish |
| B-5 | Resilience/budget: past limits → 10M `MAX_TOKENS_PER_SESSION` fires `BudgetExceeded` to agent (no silent hang); `MAX_CALLS=1000`; injected "never retry >3× / contact IT" rule present in every agent | graceful error |
| B-6 | `/steer`, `/cancel`, `DELETE /build/{sid}` | no orphan pods |
| B-7 | `/apps/{slug}/edit` preserves unrelated panels | targeted change only |
| B-8 | Builder emits invalid spec → publish rejects with rule message → self-corrects | loop closes |

---

## 3. App-build output — publish validators (negative + positive)

Each rule: **crafted-bad spec → expect rejection with that code**, plus **clean spec → passes**.
Run via `/builder/validate` (no pod/persist) and `/publish`.

| Rule | Assert rejects |
|---|---|
| H-04 | `allow_writes_in_chat` on a chat agent |
| T-03 | admin/DDL action in a tool |
| G-01 | grounding contract missing/broken |
| W-01 | delete verb in a write action |
| W-06 | direct write button without confirm |
| D-02 | dashboard page with no narrator |
| E-03 (`editable_fields`) | override combo whose options don't resolve / not allow-list enforced |
| F-01 | any `format:"file"` field (media columns disabled) |
| S-01 | non-internal audience |
| V-CHART-01 | chart missing x/y axes |
| update_identifier | update action with no record identifier |
| mcp_action_input_schema | `mcp_action` without input_schema |
| Layer-B (~13) | each `_raise_layer_b` rule |
| data-binding | panel bound to non-existent column/source |

Fraud **format validators** unit-tested: `validate_pan/ifsc/gstin/vin/aadhaar/email/phone_in/phone_us/ssn/ein/routing/zip` (valid + invalid + boundary).

---

## 4. Runtime UI — panels, detail sections, UI text

Render fixtures exercising each; verify structure (a11y snapshot), data binding, and copy.

**Panels:** `form` (all `ControlType`s incl. dynamic combo via `/field-options`, typeahead, multi-step wizard, required-field validation), `queue` (cards/table/kanban + `filter_bar` + sort), `table`, `detail`, `dashboard`+`chart` (bar/line/area/pie, axes, stacked)+KPI/timeseries, `agent_chat`, `document_view`, `markdown`, `notice`, `calendar`, `map`, `notifications`.

**Detail sections:** `fields`, `attachment` (MCP-streamed media, §7), `documents`, `agent_timeline`, `approval`, `markdown`, `agent_chat`, `comments`.

**UI-text pass:** user-language labels; button says-what-it-does + matching toast; errors explain the fix; "Decision Apps" display rename (code/slug stay SmartApp); OpsMark brand mark; theme currency/date formatting; no lorem; unknown panel → **fails loud** (visible error card).

---

## 5. Decision API (headless)

| # | Case | Expected |
|---|---|---|
| API-1 | `POST /apps/{slug}/tool/{tool_name}` (with `panel_id` allowlist) | runs, audited, typed result; wrong panel → denied |
| API-2 | `POST /apps/{slug}/run` (`action`,`inputs`) → correlation_id | run recorded |
| API-3 | `POST /apps/{slug}/chat` — read-only; write blocked unless `allow_writes_in_chat` | no write from chat |
| API-4 | Runtime-token flow → read endpoints with `?_t=`/cookie | authenticates |
| API-5 | Bad input → 4xx clear detail; unknown tool → 404; archived → 410 | fail-loud |

---

## 6. Governed decision loop

| # | Case | Expected |
|---|---|---|
| L-1 | `run` → recommendation staged (no source write) | pending decision |
| L-2 | Approve (`/run/{cid}/approve` `decision:"approve"`) replays planned writes | golden record changes, audited |
| L-3 | **Override via E-03**: `approve` with `overrides[i]={field:new_value}` (Fail→Pass) through the prepopulated combo; allow-list enforced | governed override commits chosen value |
| L-4 | Reject (`decision:"reject"`, note) | no write, audited |
| L-5 | Overlay writes (`smart_app_records`, merge vs thread) anchored to `record_id` | correct shape; golden vs app-owned labeled |
| L-6 | Auto-Recommend (agent triggers off workflows, test/prod-isolated) | recs without manual run |
| L-7 | Auto-process gating — auto-reject only in auto-process, never on-demand/auto-recommend | policy holds |
| L-8 | `/decision-contract`, `/loop-metrics`, `/self-learning` (GET+POST), `/items/{id}/feedback` | reflect the loop |

---

## 7. Tool kinds + fraud stack + data plane

**Each of 12 kinds** (`mcp, mcp_action, rag, llm, validate_form, vision_ocr, code_exec, neighbor_samples, image_analyze, doc_extract, consistency_check, fraud_synthesis`): happy-path + a failure (source down / bad input) → **fail-loud**.

**Fraud ladder:** T0 (SHA-256 exact + perceptual near-dup: caught after WhatsApp compress/crop/resize; report reuse; backdated; `consistency_check` record-binding resolves+hashes) → T1 embeddings (Milvus) → T3 `fraud_synthesis` gated LLM (`gate_min_points`) → Pass/Repair/Fail + cited evidence. Plus `image_analyze`/`doc_extract` OCR, `/fraud-calibration`, `/grounding/refresh`+status.

**Data plane:** MCP reads; `/field-options` (live DISTINCT, filter, typeahead `?q=`, server-side column safety); **media (hardened)** — `attachment` streams `s3://` via MCP (200 real bytes); SSRF guard refuses private/metadata http refs; non-media column → no failing call; missing key_field → fail-loud; `/document`.

---

## 7b. API-type sources (REST / bureau, e.g. a CIBIL screen) — SUPPORTED PATH + GAPS

**REST is an unimplemented extension point in the base MCP.** An API source does
NOT work out of the box — not even agentically. Every REST path is a stub; a
**dept subclass** must implement them (or re-wire the orphaned `api_engine`).

**Intended flow (what SHOULD happen once a dept implements REST):** IT registers
`type: rest_api` on the dept-MCP (`connection{base_url {{ph}}, method, headers,
query_template, body_template, response_path, auth.env_prefix}` +
`options.invocation_template` prose hint) → discovery writes a `data_catalogue`
entry → builder declares an **`mcp`** read tool (`dataset_kind:"rest"`; there is
no `rest_api` AppSpec type) → at runtime the agent calls it with an NL `query`;
the MCP is meant to run an LLM to craft `{path_overrides, query_params,
body_overrides}`, interpolate the templates, fire `httpx`, extract
`response_path`, and return the result.

**Reality — every REST path is a stub (all fail loud):**
1. **NL `/query` → `planners/nl_to_rest.plan` returns `None`** → orchestrator
   returns a `"not_implemented"` chunk ([query_planner.py:757-771](../source-mcp-template/query_planner.py)).
2. **`/run_query kind=rest` → `catalogue._run_rest` → 501** stub.
3. **`query_engine._run_rest` → `connectors/rest_connector.execute_rest` →
   `NotImplementedError`.**
4. **`rag/api_engine.search_api` is fully coded but has ZERO callers** — orphaned
   dead code from the old per-source-type architecture (replaced by the
   per-dataset planner pipeline, see `router.py:187`).

The dispatcher also *prefers* the keyed-read `/run_query` path when it sees flat
filters + `dataset_kind` ([tools_v2_dispatch.py](../smart-app-service/tools_v2_dispatch.py)),
so a "look up by PAN" filter lands on the 501 stub.

**STATUS — IMPLEMENTED (deterministic, schema-driven).** The REST read path is now
built end-to-end: a dataset DECLARES `input_schema` (params) + `columns` (output) +
`read_via.extra.{request,response}` (mapping); the UI supplies params as
`filters`; the MCP validates, interpolates, SSRF-guards, fires the HTTP call, and
projects typed rows. Files: `connectors/rest_connector.py` (engine, 7 unit tests),
`catalogue.py` (`_run_rest` dispatch + `input_schema` on `DatasetSchema`, 3
integration tests), `data-discovery-service` (`CatalogueEntry.input_schema`),
`smart-app-service/panel_data.py` (`rest_api`→`rest` kind map + fail-loud
`_coerce_rows`), builder skill `citra-app-spec/references/api-sources.md`. The
older `nl_to_rest` NL path stays a stub — the deterministic param path is primary.

**Live E2E (after deploy):** register `source-mcp-template/demo/rest_source_public_directory.json`
in a dept-MCP's `dept_sources`, then:
```bash
# 1. MCP direct — the "MCP does the hard work" half:
curl -sX POST $MCP/run_query -H 'X-User-JWT: <jwt>' -H 'Authorization: Bearer <svc>' \
  -d '{"source_id":"public_directory","dataset_id":"public_directory.user","kind":"rest","query":{"id":"1"},"row_limit":5}'
# expect: {"rows":[{"name":"Leanne Graham","email":"...","city":"Gwenborough","company":"Romaguera-Crona"}],...}

# 2. Discovery — schema reaches the catalogue:
#    GET $SMART_APP/builder/catalogue?full=true → the public_directory.user entry
#    carries columns[] + input_schema{required:[id]}.

# 3. App — the "UI calls it, gets data" half: publish a tiny app with a form(id) →
#    a detail bound to ds(type:mcp, ref:public_directory.user, filters:{id:"{param.id}"});
#    open it → the row renders. (See references/api-sources.md.)
```
2. **Silent-empty on nested JSON** — `panel_data._coerce_rows` only unwraps
   `rows/items/data/results`; a single object `{credit_score,…}` → `[]`.
   **Fixed** to surface a clear note via `_is_unrenderable_object`
   ([panel_data.py](../smart-app-service/panel_data.py)) — a columnar panel over an
   API object now reports the shape instead of rendering blank.
3. **`mcp` reads expose only `query`+`max_results`** to the agent (no structured
   `input_schema`) — identifiers must be verbalized into the NL query.
4. **No machine-readable API contract in the catalogue** — only prose
   `invocation_template` + a `response_path` string; the builder infers from
   hand-declared `columns[]`. REST is not in `_INTROSPECTABLE_KINDS`.
5. **Builder skills barely cover API sources** (one mention in
   `citra-agent-spec/SKILL.md`); no CIBIL example.

**How to test an API source:** agent-first (an `mcp` read consumed by the agent
via `/query`, rendered through a `detail`/`agent_chat` panel), NOT as a
queue/table data source. See `tests/e2e/test_08_api_source.py`.

## 8. Environments, lifecycle, versioning

`promote-to-prod` (test→prod, `test_`→prod collections); `versions`+`rollback`; `transfer` (SA ownership); `audience` set (org/team/dept/owner) + read matrix; `archive`/`lifecycle/archive` → 410 on runtime; `spec/lint`; `PUT /apps/{slug}/spec`; `grounding/refresh`.

---

## 9. AuthZ matrix (fail-closed)

Roles {super_admin, org_admin, dept_admin, member, anonymous} × audiences {owner, team:sa, dept, org} × endpoints {read spec/data/detail/media, run, approve, publish, edit, promote, transfer, audience, archive}. Assert: published→audience-scoped; draft→editors only; cross-tenant denied; `/publish` builder-scope only in prod; anonymous → nothing.

---

## 10. Non-functional

- **Security:** media hardening set (SSRF, filename injection, stream-safe error, no key-guessing) automated; secrets never in URLs; user-JWT forwarding or dept-MCP 403.
- **Concurrency/perf:** N concurrent builds; high-cardinality typeahead; large queue pagination; detail with many media columns.
- **Failure modes:** MCP/Milvus/Vault down, budget exceeded, LLM 429 → **fail loud** (RULE #1).
- **Idempotency/audit:** approve-once; audit logged only on success.

---

## 11. Golden E2E scenarios (per release, both envs)

1. **Fraud screen** — build → publish (validators pass) → Screen → FAIL/HIGH evidence (dup photo 3×, report 2×, backdated) → **E-03 override Fail→Pass** → ID locked → media renders/downloads → audit+self-learning updated → promote-to-prod → re-verify.
2. **Form-CRUD** — dynamic-combo form → create → detail → edit → overlay thread comment → audit.
3. **Dashboard** — each chart type + KPI + narrator chat (read-only) + filter_bar.

**Exit criteria:** validator negatives reject with correct code; every panel renders + fails loud on unknown; decision loop 4/4; fraud T0/T1/T3 catch fixtures; media E2E green incl. SSRF; authz fail-closed; no RULE-#1 violations; UI-text pass clean.

**Prioritization (time-boxed):** §6 loop + §3 validators + §7 fraud/media + §9 authz first; §4 panels + §2 builder resilience next; §10 non-functional last.
