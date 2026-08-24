<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Live Traefik configs (captured from the running boxes)

**These are the configs that ACTUALLY run in prod**, captured 2026-06-07 via SSM.
They are hand-managed on each box at `/home/ubuntu/citra-ai/traefik/dynamic/`
(the lowercase `citra-ai` runtime dir — **NOT** the git clone at
`/home/ubuntu/Citra-AI`, and **NOT** `infrastructure/traefik/dynamic/`).

> ⚠️ Config drift: the older files under `infrastructure/traefik/dynamic/` and
> `infrastructure/traefik/dynamic.yml` are stale / aspirational and do **not**
> match what runs. Treat the files in *this* folder as the source of truth for
> the live routing until the deploy flow is changed to push from git.

## App box (Citra-AI-1, 172.31.39.51) — target group `citra-ai-tg`
- `app-box/traefik-dynamic.yml` — routers for `api.citra-ai.com` paths
  `/citra-ai`, `/smart-apps`, `/action-chat`, and the
  `/citra-ai/api/workflows|admin/workflows|dept-sources` carve-out to
  citra-workflow. 4 Citra-Service shards. `slow_timeouts` transport gives
  backends 1800s response-header timeout (SSE-safe).
- `app-box/apps-runtime.yml` — `Host(apps.citra-ai.com)` -> citra-app-runtime:3100.

## Common box — collapsed into the App box for demo-prod (2026-07)
The Citra-AI-Common box (`172.31.23.112`) was terminated. Its Traefik routes
were folded into the App box's dynamic dir as
`/home/ubuntu/citra-ai/traefik/dynamic/95-common-merged.yml` (user-service,
collaboration, vault, redis), so demo-prod runs exactly one Traefik. The
customer topology puts these routes back on a Common box — see
[`../../../docs/deployment-topology.md`](../../../docs/deployment-topology.md).

> **KEEP** the ALB target group `citra-common-tg` (zero targets today) and the
> `citra-infra-sg` security group (attached to no ENI today). They are not
> leftovers — they are the reusable scaffolding for the Common box's ingress in
> the customer multi-box topology.
>
> ⚠️ The one genuinely dead thing here is the listener rule at priority 80 for
> `dashboard.citra-ai.com`, which points at `citra-common-tg` and returns 503.
> It was Superset's route — Superset was decommissioned 2026-06-11. That rule
> is safe to delete; nothing else references it (verified 2026-07-26: every
> other rule targets `citra-ai-tg`).

Note: `../dynamic/91-action-services-common.yml` is **not deployed** on any box
(verified 2026-07-26). Action Chat is parked and sandbox-host runs on the App
box on :7090, so the file is inert. Its sandboxhost URL used to point at the
decommissioned `172.31.23.112`; it was repointed to `172.31.39.51:7090` so the
file is at least correct if it is ever mounted.

## To re-capture after an on-box change
```
aws ssm send-command --instance-ids <id> --document-name AWS-RunShellScript \
  --parameters 'commands=["base64 -w0 /home/ubuntu/citra-ai/traefik/dynamic/<file>"]'
# then base64 -d into the matching file here
```
