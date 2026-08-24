<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Integrating a custom UI with the Decision API

This guide shows how to put **your** front end — web, Android, iOS, or desktop —
in front of a Citra Decision App and inherit its agentic reasoning, governed
decisions, fraud detection, and learning. The API is plain JSON over HTTPS, so
any HTTP client works; the two bundled SDKs (TypeScript, Python) are thin
conveniences.

Everything below assumes:
- `BASE` = your smart-app-service origin (e.g. `https://apps.citra-ai.com/api`)
- `JWT` = the **end user's** bearer token (see [Auth](#1-auth))
- `SLUG` = the app's slug

## 0. The model in one paragraph

A Decision App = a UI **and** an agent. Your client talks to the agent through
the Decision API. You **read** data to render screens, and you **commit
decisions** only through the governed endpoints (`/run` → `/approve`, or
`/tool/*`). You never write the system of record yourself — that boundary is what
preserves governance, audit, and self-learning across every client.

## 1. Auth

Send the **end user's** JWT on every request:

```
Authorization: Bearer <JWT>
```

Per-user authorization and audit are applied server-side from that token. Two
ways to obtain it:

- **Forward the user's existing session JWT** (the same token your IdP/SSO issues
  and that Citra-UI uses). Best for first-party apps.
- **Mint a scoped runtime token** for a launched app session:
  `POST /apps/{SLUG}/runtime/token` → `{ token }`. Short-lived; use for embeds.

Never hard-code a token in a shipped mobile/desktop binary — obtain it at runtime
from your auth flow, and refresh on 401. In the SDKs, pass a **function** as the
token so it can refresh:

```ts
new DecisionAppClient({ baseUrl: BASE, token: async () => await getFreshJwt() });
```

## 2. Discover the contract (do this first)

```
GET {BASE}/apps/{SLUG}/decision-contract
```

Returns the actions you can run, the `inputs` JSON Schema, the endpoint paths,
and the governance rules — self-describing, so you don't hard-code shapes. Build
your input form from `request_schema`; list the officer's choices from
`run_actions` and `write_actions[].editable_fields`.

## 3. The governed decision loop

```
POST {BASE}/apps/{SLUG}/run
     { "action": "<run_actions[0]>", "inputs": { … per request_schema … } }
  → { correlation_id, status, decision, reasoning, planned_writes, … }
```

If `status == "pending_approval"`, render `decision` + `reasoning` +
`planned_writes` for the officer, then commit:

```
POST {BASE}/apps/{SLUG}/run/{correlation_id}/approve
     { "decision": "approve" }                       // or "reject" / "cancel"
     { "decision": "approve", "overrides": [ { "outcome": "Pass" } ] }   // governed override
```

`overrides[i]` edits `planned_writes[i]`'s editable fields; the server enforces
the allow-list. A `reject`/`cancel` writes nothing but is audited.

**No-AI decision:** `POST /apps/{SLUG}/tool/{tool_name}` with
`{ "arguments": { … } }` runs the same governed write path without a recommendation.

## 4. Render lists, details, and media

```
GET  {BASE}/apps/{SLUG}/data/{panel_id}?status=open        // queue/table rows
GET  {BASE}/apps/{SLUG}/detail/{panel_id}?id={record_id}   // one record + sections
GET  {BASE}/apps/{SLUG}/media/{ds_id}?key_field=inspection_id&key=INS-2026-0013&col=defect_photo_url
```

**Media** streams the bytes through the MCP (never a storage URL). Add the auth
header and use the response directly as an image/PDF. On the web you can point an
`<img>` at a same-origin proxy that injects the header; on native, fetch the
bytes and hand them to your image view.

## 5. Item findings, feedback, fraud, learning

A `/run` returns **`item_findings`** — one review card per analysed artifact,
data-lookup check, or fraud case:

```jsonc
"item_findings": [
  { "item_id": "inv-88-doc", "modality": "document", "fields": {…},
    "recommendation": "amount matches PO", "confidence": 0.9 },
  { "item_id": "app-42-cibil", "modality": "api",       // a bureau/CIBIL check
    "fields": { "score_band": "620-660", "hit": true }, "confidence": 0.8 },
  { "item_id": "app-42-fraud", "modality": "case",       // fraud — EVIDENCE ONLY
    "fields": { "fraud_risk": "medium", "signals": "exact_duplicate×2" } }
]
```

Disposition each card, then approve:

```
POST {BASE}/apps/{SLUG}/items/{item_id}/feedback
     { "modality": "case", "task_type": "fraud-screening",
       "decision": "accept", "reason": "…", "subject": "app-42" }
// reason is REQUIRED when decision="reject" (max 500 chars — it trains the rubric)
POST {BASE}/apps/{SLUG}/fraud-calibration          { }   // read-only report
GET  {BASE}/apps/{SLUG}/runs/{correlation_id}/audit
```

- `decision` is **`accept`** (confirm the finding), **`reject`** (dismiss it), or
  **`cancel`** (skip / undecided) — for every modality (`image` / `document` /
  `api` / `case`).
- The app's **`item_review_gate`** (`hard` / `soft` / `none`, from the
  decision-contract) says whether each non-fraud finding must be dispositioned
  before `/approve`. A **`case`** (fraud) finding is **evidence only** — it never
  gates Apply and never auto-rejects; the officer decides.
- Fraud endpoints return **409 `fraud_not_enabled`** when the app's ontology
  (its MCP `sources.json`) does not turn fraud detection on. Guard for it.

Fraud detection needs **no client code** to *produce* — it runs inside the agent
during `/run`. You only read `item_findings` and disposition them.

---

## Per-platform

### Web / Electron / React Native — use the TS SDK
`import { DecisionAppClient } from "@citra/decision-api"` — one class, all
methods. Works unchanged in a browser, an Electron main/renderer, and a React
Native screen (uses global `fetch`). See [`typescript/`](typescript/).

### Python desktop / automation — use the Python SDK
`from decision_app import DecisionAppClient`. See [`python/`](python/).

### Android (Kotlin, OkHttp) — raw HTTP

```kotlin
val client = OkHttpClient()
fun recommend(base: String, slug: String, jwt: String, inspectionId: String): String {
  val body = """{"action":"screen_inspection","inputs":{"inspection_id":"$inspectionId"}}"""
      .toRequestBody("application/json".toMediaType())
  val req = Request.Builder()
      .url("$base/apps/$slug/run")
      .header("Authorization", "Bearer $jwt")
      .post(body)
      .build()
  client.newCall(req).execute().use { r ->
    if (!r.isSuccessful) throw IllegalStateException("Decision API ${r.code}: ${r.body?.string()}")
    return r.body!!.string()   // parse correlation_id / decision / reasoning
  }
}
// approve:  POST $base/apps/$slug/run/$cid/approve  {"decision":"approve","overrides":[{"outcome":"Pass"}]}
```

### iOS (Swift, URLSession) — raw HTTP

```swift
func recommend(base: URL, slug: String, jwt: String, inspectionId: String) async throws -> Data {
  var req = URLRequest(url: base.appendingPathComponent("apps/\(slug)/run"))
  req.httpMethod = "POST"
  req.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")
  req.setValue("application/json", forHTTPHeaderField: "Content-Type")
  req.httpBody = try JSONSerialization.data(withJSONObject: [
    "action": "screen_inspection",
    "inputs": ["inspection_id": inspectionId]

  ])
  let (data, resp) = try await URLSession.shared.data(for: req)
  guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
    throw NSError(domain: "DecisionAPI", code: (resp as? HTTPURLResponse)?.statusCode ?? -1)
  }
  return data   // parse correlation_id / decision / reasoning
}
// approve: POST apps/\(slug)/run/\(cid)/approve  {"decision":"approve"}
```

### curl (any backend / testing)

```bash
curl -sX POST "$BASE/apps/$SLUG/run" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"action":"screen_inspection","inputs":{"inspection_id":"INS-2026-0013"}}'

curl -sX POST "$BASE/apps/$SLUG/run/$CID/approve" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"decision":"approve","overrides":[{"outcome":"Pass"}],"note":"genuine repair"}'
```

## Error handling & idempotency

- Non-2xx → `{ "detail": … }`. Retry only idempotent reads and `/run` calls that
  you keyed with your own `correlation_id` (a retry with the same id is safe).
- `401` → refresh the JWT and retry once.
- `409` on approve → the run was already decided; re-fetch its audit.
- Never auto-retry a `direct` write without an `idempotency_key`.

## The one rule (again)

Your UI is presentation only. **Do not** write the system of record from the
client — commit exclusively through `/run`+`/approve` or `/tool/*`. That is the
governance + self-learning boundary that makes every client behave identically.
