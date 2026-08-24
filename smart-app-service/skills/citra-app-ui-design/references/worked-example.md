<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-app-ui-design — Worked example (consolidated Q&A + Proposal block)

This is a sample of the consolidated record you append to `ui_design.md`
**after** the question script is done — it shows the expected shape of the
`## Q&A — v1` and `## Proposal — v1` sections (a claims app). Reproduce the
*structure*, filled with the BA's actual answers — not these values.

```markdown
## Q&A — v1 (BA answers, verbatim)
- multi/single: multi-page
- landing page: inbox
- submission location: separate page (/home)
- queue page: shares inbox with chart
- detail location: separate page (deep-linkable)
- charts: inline next to queue on inbox
- chat panel: global (floating, all pages)
- trigger automation: poll new claims (precompute triage into the inbox)
- nav chrome: sidebar
- post-submit landing: new claim's detail page

## Proposal — v1 (derived from answers above)

### Pages
1. **`home`** — Submit a claim
   - Layout: stack
   - Panels:
     - `submit_claim` (form, on_submit → process_claim, then navigate to `record` with `{result.claim_id}`)
2. **`inbox`** — My claims (landing)
   - Layout: grid
   - Panels:
     - `my_claims` (queue, row click → `record?id={row.claim_id}`)
     - `volume_trend` (chart, line of amount over sla_due)
3. **`record`** — Claim detail (hidden from nav)
   - Layout: grid
   - Panels:
     - `claim_detail` (detail, linked_to=my_claims, sections: agent_timeline, fields, approval, documents)
     - tool_buttons: [{label: "Run payout", tool_name: "run_payout", confirm: "Release payout?"}]  ← direct mcp_action write (no LLM)

### Navigation
- Style: sidebar
- Default page: inbox
- Global chat: yes (floating)
```
