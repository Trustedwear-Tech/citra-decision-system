<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra demo data

Real public datasets + real public SOPs, normalised into the Citra
data model so demos look like real working enterprise installations
rather than hand-built mock-ups.

Three industries are simulated:

| Industry | Persona | Status |
|---|---|---|
| Cement manufacturing | ACME Cement Pvt Ltd (5000 TPD, India) | **in progress (Q2 2026)** |
| Motor-claims insurance | ACME Motor Insurance | pending |
| Healthcare | ACME Care Hospitals | pending |

Same demo data is reused across `dev`, `test`, and `prod` Citra
environments via dedicated demo MCP containers — every environment can
show the demo without polluting tenant data because every demo row
carries `tenant_id=acme-<industry>-demo` and every Milvus chunk
carries `tag=demo`.

## Folder layout

Everything tenant-specific lives under `tenants/<tenant-id>/`. The
Citra platform itself (user-service, citra-service, etc.) holds **zero**
tenant-specific config — add a new demo company by dropping a folder
here and running one script.

```
demo-data/
  README.md                ← this file
  scripts/                 ← generic, tenant-agnostic orchestration
    seed_tenant.py         ← --tenant <id> seeds org/depts/users via admin API
    publish_apps.py        ← publish a tenant's app fixtures to smart-app-service
    refresh_demo.py        ← daily cron: re-anchors Mongo dates + heals
                             emulators for EVERY tenant (reads each
                             tenants/<id>/demo_refresh.json)
    run_demo_tests.py
    backfill_ownership_fields.py
  tenants/                 ← one folder per demo tenant — drop-in pluggable
    acme-cement/
      tenant.json          ← { org: {...}, depts: [...] }
      users.json           ← persona list (no passwords — impersonation only)
      demo_refresh.json    ← refresh manifest read by scripts/refresh_demo.py
      apps/                ← AppSpec/AgentSpec JSONs
      workflows/           ← workflow JSONs (workflows.json)
      mcp/                 ← source-mcp-template configs (sources.json, docker-compose.yml)
      raw/                 ← downloaded public datasets + SOPs (gitignored)
      scripts/             ← tenant-specific generators / fetchers / ingesters
                             (seed_mongo, ingest_pdfs, seed_bq_emulator,
                              seed_gcs_parquet, build_*)
  docs/                    ← architecture + redesign notes
  tests/                   ← integration tests
  results/                 ← run reports (gitignored)
```

## Onboarding a new demo tenant

```bash
# 1. Bootstrap the tenant folder
mkdir -p demo-data/tenants/<tenant-id>/{apps,workflows,mcp,raw,scripts}

# 2. Author tenant.json (org metadata + depts)
cat > demo-data/tenants/<tenant-id>/tenant.json <<'EOF'
{
  "org":   { "id": "<tenant-id>", "name": "...", "domain": "...", "is_demo": true },
  "depts": [ { "id": "...", "name": "..." } ]
}
EOF

# 3. Author users.json (persona list — no passwords)
# 4. Seed via admin API (POST orgs, depts, users — idempotent)
python demo-data/scripts/seed_tenant.py \
    --tenant <tenant-id> \
    --admin-token "$(cat ~/.citra-admin-jwt)" \
    --user-service-url http://localhost:7004

# 5. Author demo_refresh.json so the daily cron keeps this tenant fresh.
#    Declares the Mongo DB + date columns to re-anchor and the in-memory
#    cloud emulators to health-check + re-seed. Copy tenants/acme-cement/
#    demo_refresh.json as a template. With it in place, the generic
#    scripts/refresh_demo.py picks the tenant up automatically.
```

After seeding, those personas appear automatically in the **Impersonate
User → Demo personas** tab in the UI, grouped by the tenant's display
name. A Citra super_admin can pick any persona and walk a prospect
through their own seeded data.

## Storage targets

| Resource | Location | Notes |
|---|---|---|
| Source MongoDB | `mongodb+srv://<user>@<your-cluster>.mongodb.net/` | databases `demo-source-{manufacturing,insurance,healthcare}` |
| Object store | `s3://demo-source-citra` (`ap-south-1`) | folder per industry under root |
| Milvus | shared cluster, collections named `demo_<industry>_<source_id>`, every chunk tagged `tag=demo` | |
| User store | main Citra-User-Service Mongo (`users` + `tenants` collections) | tenants `acme-cement-demo`, `acme-motor-demo`, `acme-care-demo` |

## How a demo works

1. Demo MCP container per industry (e.g. `mcp-demo-manufacturing`)
   is registered in `discovery-service` like any other MCP.
2. The MCP hosts the industry's sources — for cement, 9 sources
   (3 dept-specific + 6 central, including 3 cloud/legacy backends:
   SAP RFC, BigQuery, GCS Parquet).
3. SOPs live in S3 under the matching prefix; the MCP exposes them
   via RAG on the same `/query` endpoint.
4. Citra Smart Apps in the demo tenants are authored against these
   source_ids exactly as a real customer would author against their
   own dept-MCPs.

## Day-by-day build sequence

1. Fetch real datasets + SOPs from public sources → `raw/`
2. Seed Mongo collections (6 per industry) with FK consistency
3. Seed `acme-*-demo` tenants + personas into user store
4. Spin up demo MCP container with all 6 source bindings
5. Author 6 ingestion workflows in workflow-builder
6. Build SmartApps + dashboards via builder
7. `scripts/refresh_demo.py` to re-anchor dates daily before demos

See `tenants/acme-cement/mcp/README.md` for the full, ordered
bring-up sequence (the cement tenant is the worked example).
