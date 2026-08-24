<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Runtime click-through E2E (Playwright)

The **interaction layer** the in-builder vision gate (`citra_visual_review`) does
not cover. That gate renders each page and critiques the screenshot — "does it
look right." It never **clicks a row, drills into a detail page, or submits a
form**. Every prod-breaking bug we've hit lived in that gap:

| Bug | Caught by these tests, missed by vision |
|---|---|
| Detail panel `app not found` (`get_detail_data` `_bind_app_env`) | drill-down test |
| 500-row queue freeze | queue render test |
| `runtimeFetch` 403 (`user_org=None`) | queue panel-data assertion |
| Form submit / file-upload 422 | form-submit test |

Pixels prove "rendered"; these prove "works." Assertions are on DOM + network,
not screenshots — vision stays the chart/visual smoke, this is the behaviour.

## Prerequisites
- The **full dev stack up**: runtime (`:3100`), smart-app-service (`:9100`),
  the dept MCP, Mongo. (These tests do **not** start it.)
- The pinned app **published** (`bsphcl-meter-inspection`, or set `E2E_SLUG`).
- Node 18+.

## Run
```bash
cd citra-app-runtime/e2e
npm install
npm run install-browser          # one-time: chromium
npm run test:read-only           # queue + detail drill-down — NO model credits
npm test                         # all, incl. form submit (needs OpenRouter credits)
npm run report                   # open the HTML report
```

## Credits
- `@read` tests (queue render, detail drill-down) are read-only — **no credits**.
- The **form-submit** test fires the form's `on_submit` (an `agent_action` → `/run`
  → the model), so it needs OpenRouter credits. It asserts the submit succeeds
  with **no 422** — i.e. the `#3b` file-upload blob fallback stored the file and
  the write received a string ref.

## Auth
`auth.ts` mints a normal end-user JWT with the shared `JWT_SECRET` (read from
`Citra-Service/.env`) and hands it to the runtime via the same `?_t=` param the
Citra-UI uses on launch. Override the identity/app with env vars:
`E2E_USER_ID`, `E2E_TENANT_ID`, `E2E_SLUG`, `E2E_QUEUE_PAGE`, `E2E_FORM_PAGE`,
`RUNTIME_BASE_URL`, `CITRA_SERVICE_ENV`.

## Scope (deliberate)
Tests a **pinned published app** so it's deterministic. It does **not** test the
LLM *build* step (non-deterministic — that's covered by the in-builder
`preview-smoke` + form-submission gates). Build output → gates; runtime render +
interaction → this suite; data correctness → both (gates assert numbers, here we
assert rows resolve + submits commit).
