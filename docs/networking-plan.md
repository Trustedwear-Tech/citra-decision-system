<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Plan: one network, service names, no host hairpins

Status: plan. Nothing changed yet. Written 2026-08-17.

Goal: containers talk to each other directly, by service name. Clone-and-run
works locally with no manual setup, and the same shape deploys to AWS.

---

## One correction first

`citra-network` is **not** the containment network. It is just the application
network — where citra-service, smart-app-service, mongo and the rest find each
other.

The containment is a different pair, created by `build-sandboxes.sh`:

| network | `internal` | purpose |
|---|---|---|
| `citra-action-egress` | **yes** | agent-executed code gets NO route to the internet |
| `citra-action-approved-egress` | no | the allow-listed path out |

Simplifying `citra-network` does not touch that boundary, and this plan does not
propose removing it — see "What not to simplify".

## What is there today

Five network names are in play:

| network | where | refs |
|---|---|---|
| `citra-network` | the app network; `external: true` in infra | 122 |
| `citra-action-approved-egress` | sandbox egress | 20 |
| `citra-ai-net` | the private repo's name — vestigial here | 16 |
| `citra-action-egress` | sandbox containment | 12 |
| `citra-local-net` | `docker-compose.local.yml` | 6 |

Two problems follow from that.

**`citra-network` is declared `external` and nothing creates it.** Compose then
refuses to create it itself, so the first `docker compose up` on a clean host
fails outright:

```
network citra-network declared as external, but could not be found
```

`docker-compose.infra.yml`'s own header documents this and tells you to run
`docker network create citra-network` by hand — but the quickstart is precisely
the path where nobody has read that file yet. A one-line fix now exists in
`setup.sh`; it is a workaround for a declaration that was never right for a
self-contained install.

**Per-tenant MCPs live in their own compose project, on their own network.** So
they cannot use service names and must hairpin back through the host:

```yaml
DISCOVERY_URL:        http://host.docker.internal:9010
MCP_PUBLIC_BASE_URL:  http://host.docker.internal:18504
SMART_APP_SERVICE_URL: http://host.docker.internal:9100
MILVUS_URI:           http://host.docker.internal:19531
```

Each of those carries a paragraph of comment explaining which port and why,
every one written after a bug "confirmed live". That is the real cost: it is not
that hairpinning is slow, it is that **every service must publish a host port
even when only other containers call it**, and every consumer must know the host
port rather than the service name. `DISCOVERY_SERVICE_URL=http://localhost:9000`
— wrong for a container, silently failing closed as a 403 — came from exactly
this confusion.

## The proposal

**One application network, created by compose, joined by everything.**

1. **Stop declaring it external.** `docker-compose.infra.yml` declares
   `citra-network` with `name: citra-network` and lets compose create it, the
   way `docker-compose.dev.yml` already does. The `docker network create` line
   in `setup.sh` then becomes unnecessary and comes out.

2. **Per-tenant MCPs join `citra-network`** rather than standing up their own.
   They remain separate compose projects — they are generated per tenant and
   have their own lifecycle — but they attach to the existing network as
   `external: true`, which is now honest because the main stack really does
   create it.

3. **Replace every `host.docker.internal:<published>` with
   `<service>:<container-port>`** in the MCP templates, `make_mcp.py`, and the
   generated composes. `discovery-service:9000`, `smart-app-service:9100`,
   `milvus:19530`. The long comments justifying host ports get deleted with
   them.

4. **Publish to the host only what a human or a browser needs.** Today mongo
   (27017), postgres (5432) and minio (9001) are published; the rest reach each
   other over the network anyway. Keep the UI and, while developing, the
   datastores — a self-hoster wanting to inspect Mongo is a real need. But no
   service should be published *because another container needs it*.

5. **Retire `citra-ai-net` and `citra-local-net` from this tree.** The first is
   the private repo's name and means nothing here; the second belongs to a
   compose file that predates the quickstart.

## What clone-and-run looks like

```bash
git clone --recursive https://github.com/Trustedwear-Tech/citra-decision-system
cd citra-decision-system
make wizard
```

and nothing else. No `docker network create`, no reading
`docker-compose.infra.yml` to discover a prerequisite, no port collisions to
reason about because almost nothing is published. If a second Citra stack is
already running on the machine, compose project names keep them apart.

## What AWS looks like later

The same compose, unchanged. Docker's service-name DNS behaves identically on an
EC2 box, so `discovery-service:9000` resolves there exactly as it does on a
laptop — which is the point of removing the host hairpins: the local and
deployed topologies stop differing.

What changes on AWS is only:

- **what is published** — the UI behind the load balancer; datastore ports not
  published at all
- **where secrets come from** — Vault via AppRole instead of `.env`
- **what the deploy scripts do** — those live in `private/`, and are the one
  piece that is legitimately ours-only

If the platform later moves to ECS or Kubernetes, service names map onto that
platform's service discovery with no application change. Host-port hairpins
would not survive that move; service names do.

## What not to simplify

**Keep `citra-action-egress` internal.** It is the only thing stopping
model-generated code in a sandbox from reaching the internet. It costs one
`docker network create --internal`, it is already automated in
`build-sandboxes.sh`, and it is invisible until it matters. Removing it because
"containment is not required now" would be trading a real boundary for nothing —
the simplification worth having is in the *application* network, which is where
the confusion actually is.

Note also that `citra-action-approved-egress` must NOT be internal; the script's
own comment records that making it internal breaks every spawn.

## Order

1. Make `citra-network` compose-created; drop the `external: true` and the
   `docker network create` workaround.
2. Move per-tenant MCPs onto it; replace host hairpins with service names.
3. Trim published ports to what a human uses.
4. Delete the `citra-ai-net` and `citra-local-net` references.
5. Re-run the clean-room test **on a host with no `citra-network` present** —
   which is the one thing the last run could not prove, because the network
   already existed from the earlier attempt.

Step 5 is the acceptance test. Everything above is reasoning; only a clean host
settles whether clone-and-run actually works.
