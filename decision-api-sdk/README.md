<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra Decision API — SDK & Integration

Build **any** front end (web, Android, iOS, desktop) on top of a Citra **Decision
App**, and get the *same* agentic reasoning, governed decision loop, fraud
detection, feedback, and self-learning — because **the agent, not the UI, does
the work.**

When a Decision App is built, the builder produces **two things**: a UI *and* an
**agent** (the AgentSpec — tools, grounding, governance, learning). The web
runtime is just one renderer over that agent. This SDK lets you put a **different
renderer** in front of the same agent:

```
   Web UI ─┐
 Android  ─┤
   iOS    ─┼──►  Decision API  ──►  the App's AGENT  ──►  MCP (systems of record)
 Desktop  ─┤     (this SDK)         reason · recommend · fraud · govern · learn
 Automation┘
```

Every client that goes through the Decision API inherits, unchanged:

- **Agentic reasoning & recommendations** — the app's agent reads the record,
  reasons over its grounded data + past decisions, and recommends.
- **Governed decision loop** — `recommend → approve / override / reject`, or a
  no-AI `direct` decision. Every commit runs the policy gate → schema-validated
  idempotent write to the system of record → audit → DecisionRecord.
- **Fraud detection** — the tiered fraud checks run *inside the agent*, so a
  mobile screen gets the same duplicate-photo / backdated-report detection a web
  screen does.
- **Feedback & self-learning** — officer feedback and outcomes feed the same
  learning loop; the app gets smarter regardless of which client submitted.

**The one rule:** the UI is *presentation only*. It must **never** write the
system of record directly — only through the decision endpoints. That boundary
is what makes the governance, audit, and learning hold across every client.

## What's here

| Path | |
|---|---|
| [`typescript/`](typescript/) | TS/JS SDK — web, **React Native (iOS/Android)**, Node, Electron |
| [`python/`](python/) | Python SDK — desktop, automations, backends, notebooks |
| [`API-REFERENCE.md`](API-REFERENCE.md) | Every endpoint: method, path, request, response |
| [`INTEGRATION.md`](INTEGRATION.md) | Step-by-step + **per-platform** notes (Kotlin/Swift/RN/Electron) and raw-HTTP examples |

Native Android (Kotlin) and iOS (Swift) have no bundled SDK yet — the API is
plain JSON over HTTPS, so [`INTEGRATION.md`](INTEGRATION.md) shows the exact
requests to make from `URLSession`/`OkHttp`. A thin generated client can follow.

## 30-second quickstart (TypeScript)

```ts
import { DecisionAppClient } from "@citra/decision-api";

const client = new DecisionAppClient({
  baseUrl: "https://apps.citra-ai.com/api",   // your smart-app-service / gateway
  token: userJwt,                             // the END USER's JWT — per-user authz + audit
});

const slug = "equipment-inspection-fraud-screen";

// Discover the contract, then run the governed loop:
const c = await client.getContract(slug);
const rec = await client.recommend(slug, { action: c.run_actions[0], inputs: { inspection_id: "INS-2026-0013" } });
//   rec.decision / rec.reasoning / rec.planned_writes   ← show these to the officer

await client.approve(slug, rec.correlation_id, {
  decision: "approve",
  overrides: [{ outcome: "Pass" }],           // governed override (allow-list enforced)
  note: "Reviewed evidence; genuine repair.",
});
```

Python is symmetric — see [`python/`](python/).

## Auth in one line

Send the **end user's** `Authorization: Bearer <jwt>` on every call so per-user
authorization and audit apply. For a launched-app session you can mint a scoped,
short-lived token via `mintRuntimeToken(slug)`. Details in
[`INTEGRATION.md`](INTEGRATION.md).

## The decision loop (the important part)

```
recommend(action, inputs)          → { status: "pending_approval",
                                        decision, reasoning, planned_writes }
        │  (show to the officer — nothing written yet)
        ▼
approve(correlation_id, {decision, overrides?, note?})
        │  decision = approve | reject | cancel
        │  overrides[i] edits planned_writes[i]'s editable fields (governed override)
        ▼
   governed write path → SoR write · audit · DecisionRecord · self-learning
```

For a decision made **without** the AI, call `decideDirect(slug, tool, args)` —
it runs the identical governed path. Read-only apps return `status:"completed"`
straight from `recommend`.
