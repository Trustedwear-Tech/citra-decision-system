<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# @citra/decision-api (TypeScript)

Drive a Citra **Decision App** from any JS/TS runtime — web, **React Native
(iOS/Android)**, Node 18+, Electron. Dependency-free (uses global `fetch`).

```bash
npm install   # then: npm run build   (emits dist/)
```

```ts
import { DecisionAppClient } from "@citra/decision-api";

const client = new DecisionAppClient({
  baseUrl: "https://apps.citra-ai.com/api",
  token: userJwt,                      // string, or () => string | Promise<string> to refresh
});

const slug = "equipment-inspection-fraud-screen";
const c = await client.getContract(slug);

const rec = await client.recommend(slug, {
  action: c.run_actions[0],
  inputs: { inspection_id: "INS-2026-0013" },
});
// rec.status === "pending_approval" → show rec.decision / rec.reasoning / rec.planned_writes

await client.approve(slug, rec.correlation_id, {
  decision: "approve",
  overrides: [{ outcome: "Pass" }],    // governed override (allow-list enforced)
});
```

## Methods

- **Discovery:** `listApps`, `getApp`, `getContract`
- **Decision loop:** `recommend`, `approve`, `decideDirect`, `chat`
- **Data:** `getPanelData`, `getDetail`, `getFieldOptions`, `getNotifications`, `mediaUrl`, `fetchMedia`
- **Governance/learning:** `listRuns`, `getAudit`, `getLoopMetrics`, `getSelfLearning`, `setSelfLearning`, `submitFeedback`, `calibrateFraud`
- **Auth:** `mintRuntimeToken`

Errors throw `DecisionApiError { status, detail, path }`. See
[`../API-REFERENCE.md`](../API-REFERENCE.md) and
[`../INTEGRATION.md`](../INTEGRATION.md). Runnable demo: [`examples/fraud-screen.ts`](examples/fraud-screen.ts).
