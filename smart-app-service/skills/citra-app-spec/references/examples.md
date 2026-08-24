<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# citra-app-spec — Examples & page shapes

Worked AppSpec examples. Read this when you need a full structural template
(multi-page app, single-page shorthand). The core rules live in `SKILL.md`.

## Single-page shorthand (legacy, still supported)
When `ui_design.md` explicitly chose single-page (e.g. a one-shot form or a single-dashboard app), emit top-level `panels[]` instead of `pages[]`. **Do not emit both.** The Pydantic validator rejects specs that set both. In single-page mode `navigation` is unused; omit it.

## Multi-page example (claims app)
```jsonc
{
  "spec_version": "v0",
  "slug": "motor-claims",
  "title": "Motor Claims",
  "kind": "app",
  "agent_id": "motor_claim_agent",
  "data_sources": [
    // type="mcp" ref is "<server>.<tool>" — DOT-separated, both halves
    // copied verbatim from citra-mcp-discover output. Never a slash.
    {"id": "claims_db", "type": "mcp", "ref": "claims.list_claims"}
  ],
  "navigation": {"style": "sidebar", "default_page": "home"},
  "pages": [
    {
      "id": "home", "title": "Submit claim", "layout": "stack",
      "panels": [{
        "id": "submit_claim", "type": "form",
        "schema_ref": "agent.input_schema",
        "on_submit": {
          "agent_action": "process_claim",
          "navigate": {"page": "record", "params": {"id": "{result.claim_id}"}}
        }
      }]
    },
    {
      "id": "inbox", "title": "My claims", "layout": "grid",
      "panels": [
        {
          "id": "my_claims", "type": "queue",
          "data_source": "claims_db",
          "columns": ["claim_id", "insured", "amount", "status"],
          "actions": [
            {"label": "Open", "is_row_click": true,
             "navigate": {"page": "record", "params": {"id": "{row.claim_id}"}}}
          ]
        },
        {"id": "trend", "type": "chart", "chart_type": "line",
         "data_source": "claims_db", "x": "sla_due", "y": "amount"}
      ]
    },
    {
      "id": "record", "title": "Claim detail", "hide_in_nav": true,
      "params": [{"name": "id", "required": true}],
      "panels": [{
        "id": "claim_detail", "type": "detail",
        "linked_to": "my_claims",
        "sections": [
          {"type": "agent_timeline"}, {"type": "fields"},
          {"type": "approval", "roles": ["claims_manager"]},
          {"type": "agent_chat"}
        ]
      }]
    }
  ]
}
```
