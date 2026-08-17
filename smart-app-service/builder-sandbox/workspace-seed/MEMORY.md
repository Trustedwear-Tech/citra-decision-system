<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Memory

Builder pods are **per-session**. Your tmpfs `/workspace/build/` survives
within the session but vanishes when the pod is reaped. For cross-session
recall (edit-flow on the same app, days or weeks later), use the durable
memory channel:

```python
from citra_toolkit.scratch import memory
memory.put("ui-design-<slug>", "<one-paragraph summary>")
```

`<slug>` is a fill-in placeholder — substitute the app's real slug
(`memory.put("ui-design-claims-triage", …)`); never write a literal
`<slug>`. `scratch.memory` is `citra_toolkit` Python — run it with the
`exec` tool. There is **no `memory_put` tool**; OpenClaw's native
`memory_search` / `memory_get` read these notes back.

Stored memory is restored at every cold start of a builder pod for the same
SA. Use it for:

- BA preferences (`"BA prefers sidebar nav; landing on inbox; auto-approve at ₹2000"`)
- Design decisions that were hard to reach (`"After 3 rounds, BA chose
  queue-and-resolve over paused workflow"`)
- Open questions deferred (`"BA asked for ServiceNow integration; deferred
  to v2 since no MCP registered"`)

Do NOT use memory for:

- The current session's BA chat — that's automatic via the build session's
  Mongo transcript.
- Tenant data (claims, customers, etc.) — that's in dept-MCPs, never in
  builder memory.
- The actual specs — `/workspace/build/*.json` is the build session's
  artefact; smart-app-service persists them on publish.

## When to write a memory note

Once per build, at the end of `citra-app-ui-design` Sub-phase 3.3 (Freeze).
One short paragraph summarising the locked-in layout + the BA's stated
preferences. That's enough for a future edit session to skip re-asking the
same questions.
