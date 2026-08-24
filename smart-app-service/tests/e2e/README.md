<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Decision App — E2E test harness

Runnable pytest suite for `smart-app-service` (the builder, app-build validators,
runtime/Decision API, media, and the governed decision loop). Backs
[`docs/decision-app-test-plan.md`](../../../docs/decision-app-test-plan.md).

**Design:** everything is env-driven and **skips (never hard-fails)** when the
stack is unreachable or secrets are missing, so `pytest` is safe to run anywhere.
Read paths run by default; write/build paths are opt-in.

## What runs where

| Module | Needs | Notes |
|---|---|---|
| `test_07_fraud_formats` | nothing (offline) | pure unit tests of the identifier validators |
| `test_00_smoke` | service reachable | health, auth gate, contract |
| `test_01_auth_matrix` | service + `SAS_JWT_SECRET` + published app | fail-closed access |
| `test_02_publish_validators` | ″ | negatives via `/builder/validate`; base = a real published app |
| `test_03_media` | ″ | live MCP media stream (the hardened path) |
| `test_04_decision_api` | ″ | contract negatives always; write happy-paths gated |
| `test_05_decision_loop` | ″ + `DA_ALLOW_MUTATING=1` | recommend→approve→override→reject (writes SoR) |
| `test_06_builder` | ″ + `DA_RUN_BUILDER=1` | spawns a pod; slow, LLM budget |

## Configure

```bash
export SAS_BASE_URL=http://localhost:9100          # or the TEST-env URL
export RUNTIME_BASE_URL=http://localhost:3100
export SAS_JWT_SECRET=<JWT_SECRET from Vault prod/smart-app-service>
export SAS_JWT_ISSUER=Citra-AI                      # default
export DA_ORG_ID=acme-power                         # default
export DA_APP_SLUG=equipment-inspection-fraud-screen   # a PUBLISHED app = golden base
export DA_DS_ID=ds_inspections
export DA_RECORD_ID=INS-2026-0013
export DA_KEY_FIELD=inspection_id
export DA_MEDIA_COL=defect_photo_url
```

Pull the secret from the box (read-only):

```bash
# via SSM on the prod/test box, or locally from your smart-app-service .env
export SAS_JWT_SECRET=$(grep '^JWT_SECRET=' ../../.env | cut -d= -f2-)
```

## Run

```bash
cd smart-app-service && . venv/Scripts/activate    # PyJWT + httpx + pytest live here

# offline unit tests only (no stack):
pytest tests/e2e/test_07_fraud_formats.py

# everything runnable against a reachable stack (read paths, validators, media):
pytest tests/e2e

# include the write paths (TEST env, disposable record):
export DA_ALLOW_MUTATING=1 DA_RUN_ACTION=<action> DA_OVERRIDE_FIELD=outcome DA_OVERRIDE_VALUE=Pass
pytest tests/e2e/test_05_decision_loop.py

# include a real builder pod:
export DA_RUN_BUILDER=1
pytest tests/e2e/test_06_builder.py
```

## Extending

- **More validator rules:** add a mutator to `specs.py::MUTATORS` returning
  `(payload, expected_rule)`; it auto-parametrizes into `test_02`. Mutators reuse
  a real panel/tool from the fetched golden base so they stay schema-valid and
  isolate the target *rule* (not a schema error). Return `None` to skip when the
  base lacks the needed structure.
- **Runtime panel/UI-text coverage** (plan §4) is best driven through the browser
  preview tools against `RUNTIME_BASE_URL` — accessibility snapshot per panel +
  copy assertions; scaffold a `test_08_runtime_ui` when a runtime is up.
- **Golden-file regression:** snapshot each published AppSpec + a11y tree and diff
  on change.
