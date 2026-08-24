<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Cloudflare Tunnel for Observability — Runbook

This document covers operating the **`citra-obs`** Cloudflare Tunnel that
exposes Grafana, GlitchTip, Prometheus, and Alertmanager on the public
internet behind Cloudflare Access SSO.

Created 2026-05-18. Tunnel `id = 3b618743-3431-4265-aeb9-d1b8d69bbb72`.

## Overview

The tunnel is a `cloudflare/cloudflared` Docker container running on
**Citra-AI-1** (private IP `172.31.39.51`). It establishes an outbound
QUIC connection to Cloudflare's edge in `bom06` / `bom08` (Mumbai) — no
inbound rules are needed on the box. From the edge, Cloudflare routes
public traffic for the obs hostnames through the tunnel to the
container, which proxies to the local docker-network services by name.

```
internet ──TLS──> Cloudflare edge ──QUIC──> cloudflared on Citra-AI-1 ──HTTP──> citra-grafana:3000 etc
                       │
                       │ CF Access policy in front: @trustedweartech.com only
                       │ Identity provider: one-time PIN by email (default)
```

## Resources

### On Cloudflare
- **Account ID:** `aace43d6c7b88e0ef9987c1c87c0163e`
- **Zone ID (citra-ai.com):** `452afa62a360559633e34b7fb9b5a4c7`
- **Tunnel ID:** `3b618743-3431-4265-aeb9-d1b8d69bbb72` (name `citra-obs`)
- **Tunnel CNAME target:** `3b618743-3431-4265-aeb9-d1b8d69bbb72.cfargotunnel.com`
- **Zero Trust team domain:** `trusted-wear.cloudflareaccess.com`
- **CF Access Apps** (one per hostname, IDs as of 2026-05-18):
  - Citra Obs: grafana   — app id `934d14ab-5c1f-4cf4-aa78-d4831f471e41`
  - Citra Obs: glitchtip — app id `a80eb21e-2104-4580-829b-60d458bef6b7`
  - Citra Obs: prom      — app id `0b6f99d5-9ac4-41b8-aa82-1fb149e43fa6`
  - Citra Obs: alerts    — app id `4eac4260-812e-4bab-914e-70c1148fb62e`
- **CF Access policy on each:** decision=allow, session 8h, includes:
  - `email_domain == trustedweartech.com`  **OR**
  - `email_domain == citra-ai.com`
  Anyone with a `@trustedweartech.com` or `@citra-ai.com` email can
  authenticate via the One-Time-PIN-by-email flow. Other domains are
  denied automatically (CF Access default deny).

### DNS records (Cloudflare, all proxied)
| Subdomain | Type | Target |
|---|---|---|
| `grafana.citra-ai.com` | CNAME | `3b618743-….cfargotunnel.com` |
| `glitchtip.citra-ai.com` | CNAME | `3b618743-….cfargotunnel.com` |
| `prom.citra-ai.com` | CNAME | `3b618743-….cfargotunnel.com` |
| `alerts.citra-ai.com` | CNAME | `3b618743-….cfargotunnel.com` |

### On Citra-AI-1
- **Container:** `cloudflared`, image `cloudflare/cloudflared:latest`
- **Run command:** `tunnel --no-autoupdate run`
- **Auth:** `TUNNEL_TOKEN` env var (long JWT, returned from
  `GET /accounts/{acct}/cfd_tunnel/{id}/token`). Stored only in container
  env (no host file).
- **Network:** joined to `citra-network` (so it resolves backend container
  names like `citra-grafana`, `glitchtip-web`).
- **Restart policy:** `unless-stopped`
- **Logs:** docker json-file driver, 10m / 5 files.

## Daily operation

### Check status
```bash
# On Citra-AI-1
docker ps --filter name=cloudflared
docker logs cloudflared --tail 30
```
Healthy output ends with `Registered tunnel connection ...` lines for
~4 edge POPs.

### View tunnel state from Cloudflare side
https://one.dash.cloudflare.com → Networks → Tunnels → `citra-obs`

### View Access activity (who logged in when)
https://one.dash.cloudflare.com → Logs → Access

## Adding a new obs URL

Say you want to expose `loki.citra-ai.com` → `citra-loki:3100`.

1. **Update tunnel ingress** (CF API). The PUT replaces ALL rules —
   include the existing ones plus the new entry. Last rule MUST be the
   catch-all `service: http_status:404`.

   ```bash
   curl -X PUT "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/cfd_tunnel/$TUNNEL_ID/configurations" \
     -H "Authorization: Bearer $CF_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "config": {
         "ingress": [
           {"hostname": "grafana.citra-ai.com",   "service": "http://citra-grafana:3000"},
           {"hostname": "glitchtip.citra-ai.com", "service": "http://glitchtip-web:8080"},
           {"hostname": "prom.citra-ai.com",      "service": "http://citra-prometheus:9090"},
           {"hostname": "alerts.citra-ai.com",    "service": "http://citra-alertmanager:9093"},
           {"hostname": "loki.citra-ai.com",      "service": "http://citra-loki:3100"},
           {"service": "http_status:404"}
         ]
       }
     }'
   ```

2. **Create CNAME DNS record** pointing to the tunnel:
   ```bash
   curl -X POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE/dns_records" \
     -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
     -d '{"type":"CNAME","name":"loki.citra-ai.com","content":"3b618743-3431-4265-aeb9-d1b8d69bbb72.cfargotunnel.com","ttl":1,"proxied":true}'
   ```

3. **Create CF Access App** for the new hostname, with email-domain policy:
   ```bash
   # 3a. Create app
   APP=$(curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/access/apps" \
     -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
     -d '{"name":"Citra Obs: loki","domain":"loki.citra-ai.com","type":"self_hosted","session_duration":"8h"}' \
     | jq -r .result.id)
   # 3b. Attach policy — allow @trustedweartech.com OR @citra-ai.com
   curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/access/apps/$APP/policies" \
     -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
     -d '{
       "name":"Allow trustedweartech.com or citra-ai.com",
       "decision":"allow",
       "include":[
         {"email_domain":{"domain":"trustedweartech.com"}},
         {"email_domain":{"domain":"citra-ai.com"}}
       ]
     }'
   ```

4. Done — `https://loki.citra-ai.com` is live behind SSO within ~30s.

## Removing an obs URL

Reverse of above:
1. Delete the Access App (Apps API DELETE).
2. Delete the CNAME DNS record.
3. PUT the tunnel ingress without the removed entry.

## Restricting access tighter

Edit the policy on the relevant Access App (`PATCH .../policies/{id}` or
in the CF dashboard):

- **Specific people only** (instead of whole domain):
  ```json
  "include": [
    {"email": {"email": "rohit@trustedweartech.com"}},
    {"email": {"email": "alice@trustedweartech.com"}}
  ]
  ```
- **Require Cloudflare WARP enrollment:**
  ```json
  "include": [{"email_domain": {"domain": "trustedweartech.com"}}],
  "require": [{"warp": {}}]
  ```
- **Add an IP allow-list** (e.g., office IP only):
  ```json
  "require": [{"ip": {"ip": "1.2.3.4/32"}}]
  ```

## Adding a real Identity Provider (Google / GitHub / Okta)

Default identity is one-time PIN by email — works without setup but
requires checking email every 8h. To add SSO:

1. CF Dashboard → Zero Trust → Settings → Authentication → Add new
2. Pick Google / GitHub / Okta / etc., walk through the OAuth flow
3. In the Access App, set `allowed_idps` to the new IdP's ID (or leave
   empty to include all configured IdPs)

## Rotating the tunnel

If the tunnel token is compromised:

```bash
# 1. Rotate the tunnel secret (CF rotates run-token automatically)
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/cfd_tunnel/$TUNNEL_ID/rotate" \
  -H "Authorization: Bearer $CF_TOKEN"

# 2. Fetch the new run-token
NEW_TOKEN=$(curl "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/cfd_tunnel/$TUNNEL_ID/token" \
  -H "Authorization: Bearer $CF_TOKEN" | jq -r .result)

# 3. Restart cloudflared with the new token (on Citra-AI-1)
docker rm -f cloudflared
docker run -d --name cloudflared --restart unless-stopped --network citra-network \
  -e TUNNEL_TOKEN="$NEW_TOKEN" \
  cloudflare/cloudflared:latest tunnel --no-autoupdate run
```

The tunnel id, hostnames, ingress, and Access policies survive the
rotation. Only the cloudflared connector authentication changes.

## Disaster recovery

### cloudflared container died and won't restart
1. `docker logs cloudflared --tail 100` — usually a config error
2. If `TUNNEL_TOKEN` env was lost, re-fetch with
   `GET /accounts/{acct}/cfd_tunnel/{id}/token` and re-create the container
3. As a last resort, recreate the tunnel entirely:
   - `POST /accounts/{acct}/cfd_tunnel` (new tunnel)
   - Update all CNAME records to the new tunnel-id
   - Re-apply ingress + Access apps
   - Delete the old tunnel

### Citra-AI-1 died
The tunnel will go offline; the obs URLs will return 502 from CF edge.
On the replacement box:
1. Install Docker + join to `citra-network`
2. Re-run cloudflared with the existing `TUNNEL_TOKEN`
3. Same tunnel-id keeps working — DNS + Access stay intact

### Cloudflare account access lost
The 5 Vault Shamir shares + AWS IAM access remain independent — Vault
still auto-unseals via KMS. Obs URLs fail; SSM port-forward
(`scripts/port-forward.ps1`) still works as the fallback access method.

## Cleanup if the project ever moves off Cloudflare

```bash
# Delete CF Access apps (4)
for id in <app-id-grafana> <app-id-glitchtip> ... ; do
  curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/access/apps/$id" \
    -H "Authorization: Bearer $CF_TOKEN"
done

# Delete DNS records (4) — get IDs from /zones/$CF_ZONE/dns_records?name=grafana.citra-ai.com etc

# Delete tunnel
curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/$CF_ACCT/cfd_tunnel/$TUNNEL_ID" \
  -H "Authorization: Bearer $CF_TOKEN"

# Stop cloudflared
docker rm -f cloudflared
```

## CF API token (required scopes)

If you ever need to recreate or extend this setup, the API token must
have:

| Type | Resource | Permission |
|---|---|---|
| Account | Cloudflare Tunnel | Edit |
| Account | Access: Apps and Policies | Edit |
| Zone | DNS | Edit |
| Zone | Zone | Read |

Scope: include `citra-ai.com` zone + the relevant Cloudflare account.

Create at: My Profile → API Tokens → Create Token → Custom Token.
