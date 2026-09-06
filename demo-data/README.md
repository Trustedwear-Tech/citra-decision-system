<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Citra demo data

Real public datasets and real public SOPs, normalised into the Citra data
model, so the demo looks like a working enterprise installation rather than a
hand-built mock-up.

One tenant ships: **acme-bank**, an India-flavoured BFSI demo — retail
lending, collections, general-insurance claims, and a sales dashboard. Its
contract is [`tenants/acme-bank/SPEC.md`](tenants/acme-bank/SPEC.md); its
bring-up is [`tenants/acme-bank/README.md`](tenants/acme-bank/README.md).
The platform serves one org at a time, and `make wizard` brings this one up.

## Folder layout

Everything tenant-specific lives under `tenants/<tenant-id>/`. The platform
itself holds **zero** tenant-specific config — a new demo company is a folder
here plus one seeding run.

```
demo-data/
  README.md                ← this file
  scripts/                 ← tenant-agnostic orchestration
    seed_tenant.py         ← --tenant <id> seeds org / depts / users via the admin API
    publish_apps.py        ← publish a tenant's app fixtures to smart-app-service
    build_via_builder.py   ← build an app (and a workflow) through the real builder
    teach_clause.py        ← teach the demo a judgement from officer corrections
  tenants/
    acme-bank/
      SPEC.md              ← identifiers, schema, sources, apps, personas — the contract
      README.md            ← bring-up order and status
      tenant.json          ← { org: {...}, depts: [...] }
      users.json           ← persona list (no passwords — impersonation only)
      apps/                ← AppSpec / AgentSpec JSONs
      mcp/                 ← the tenant's dept-MCP: sources.json, docker-compose.yml
      raw/                 ← fetched public datasets and SOPs (gitignored)
      scripts/             ← tenant-specific seeders, ingesters and checks
  tests/                   ← fixture integrity (every JSON parses, every media
                             column has an item tool, every app has a prompt)
```

## Onboarding another demo tenant

```bash
# 1. Bootstrap the tenant folder
mkdir -p demo-data/tenants/<tenant-id>/{apps,mcp,raw,scripts}

# 2. Author tenant.json (org metadata + depts)
cat > demo-data/tenants/<tenant-id>/tenant.json <<'EOF'
{
  "org":   { "id": "<tenant-id>", "name": "...", "domain": "...", "is_demo": true },
  "depts": [ { "id": "...", "name": "..." } ]
}
EOF

# 3. Author users.json (persona list — no passwords)
# 4. Seed via the admin API (POST orgs, depts, users — idempotent)
python demo-data/scripts/seed_tenant.py \
    --tenant <tenant-id> \
    --admin-token "$(cat ~/.citra-admin-jwt)" \
    --user-service-url http://localhost:7004
```

After seeding, the personas appear in the **Impersonate User → Demo personas**
tab in the UI, grouped by the tenant's display name. A super_admin can pick any
persona and walk a prospect through that persona's own seeded data.

Remember that `data-discovery-service` pins `ORG_ID` to one org: bringing a
second tenant up means switching the platform to it, not adding it alongside
acme-bank.

## How the demo works

1. The tenant's dept-MCP container (`tenants/acme-bank/mcp/`) serves its
   sources — Postgres tables, a policy library, and API-as-dataset lookups —
   and registers them with `discovery-service` on boot.
2. SOPs are ingested into the shared policy library and reach the apps through
   the platform reader; the MCP serves no RAG of its own.
3. The Decision Apps in `apps/` are authored against those source ids exactly
   as a customer would author against their own dept-MCP.
4. `scripts/seed_memory.py` in the tenant folder seeds officer corrections so
   the demo can show what the system has learned.

See `tenants/acme-bank/README.md` for the ordered bring-up sequence.
