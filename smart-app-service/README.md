<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Smart App Service

> Backend for **Citra Power AI Apps** — the BA-friendly "describe it, get an app + agent"
> surface. Sibling service to `discovery-service`, `reranker-service`, and
> `action-sandbox-host`.
>
> Architecture: [`docs/smart-app-architecture.md`](../docs/smart-app-architecture.md)
> is the **verified** build→runtime data path (read this first — incl. the runtime
> design guardrails and the auditability plan).
> [`docs/smart-app-builder-plan.md`](../docs/smart-app-builder-plan.md) is the original
> design plan.

## What it owns

- **AppSpec / AgentSpec** JSON schemas (the contract between builder pod and runtime)
- Mongo persistence: `smart_apps`, `smart_agents`, `prompt_packs`, `skills`, `build_sessions`
- HTTP API consumed by:
  - Citra-UI (My Apps, builder chat)
  - `citra-app-builder` open-claw pod (publish artifacts)
  - `citra-app-runtime` (load specs, run actions)

## Endpoints (v0)

| Method | Path | Status | Purpose |
|---|---|---|---|
| GET    | `/health` | ✓ | Liveness + Mongo connectivity |
| POST   | `/publish` | ✓ | Builder pod posts AppSpec + AgentSpec |
| GET    | `/apps` | ✓ | List apps (My Apps page) |
| GET    | `/apps/{slug}` | ✓ | Fetch full spec (used by runtime) |
| DELETE | `/apps/{slug}` | ✓ | Archive |
| POST   | `/build` | 501 | Phase 6 — builder session |
| POST   | `/apps/{slug}/edit` | 501 | Phase 6 — re-spawn builder |
| POST   | `/apps/{slug}/run` | 501 | Phase 7 — runtime invocation |

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MONGO_URI = "mongodb://root:citradev@localhost:27017/dev?authSource=admin"
uvicorn main:app --reload --port 9100
```

## Tests

```powershell
.\venv\Scripts\Activate.ps1
pytest -q
```

The test suite covers JSON Schema + Pydantic validation against the fixtures in
`tests/fixtures/`. No Mongo required for the validator tests.

## Schemas

- [`schemas/app_spec.schema.json`](schemas/app_spec.schema.json) — UI declaration (panels, data sources, permissions)
- [`schemas/agent_spec.schema.json`](schemas/agent_spec.schema.json) — agent + sub-agents + actions

`validators.py` validates payloads against **both** the JSON Schema and the
Pydantic model on every publish.

## Phase status

- [x] Phase 1: scaffold service
- [x] Phase 2: AppSpec + AgentSpec JSON Schemas + Pydantic models + tests
- [x] Phase 3: Persistence + CRUD endpoints (publish / list / get / archive)
- [ ] Phase 4: `citra-app-runtime` Next.js scaffold
- [ ] Phase 5: builder skills (`smart-app-service/skills/`)
- [ ] Phase 6: `/build` + WS streaming + sandbox-host integration
- [ ] Phase 7: `/apps/{slug}/run` runtime invocation
- [ ] Phase 8: My Apps page in Citra-UI
