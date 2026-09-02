<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# Operations

Running the stack day to day, after [the first
install](Install-and-first-run).

## Everyday commands

Every `make` target is a thin wrapper. If `make` is not installed — it usually
is not on Windows — the equivalent is shown beside it.

| | | |
|---|---|---|
| `make ps` | show what is running | `docker compose -f docker-compose.quickstart.yml ps` |
| `make logs` | tail the core service logs | `… logs -f citra-service citra-user-service smart-app-service` |
| `make stop` | stop the containers, keep them | `… stop` |
| `make up` | bring them back — no rebuild, **no re-seeding** | `… up -d` |
| `make down` | stop and remove containers; **data volumes survive** | `… down` |
| `make down ARGS=-v` | also wipe the volumes — destroys the demo data | `… down -v` |
| `make start` | full phase 2 again: services, admin, re-seed the demo | `bash scripts/quickstart/start.sh` |

`make up` is the one you want after a `make down`. `make start` also works but
re-runs the seed, which is slower and unnecessary if the data is still there.

## After a reboot

**The stack comes back by itself.** Every service is declared `restart:
unless-stopped`, so Docker restarts them and you need to run nothing.

The one exception is `mongodb-init-rs`, which is `restart: no` on purpose: it
initialises the Mongo replica set once and is meant to exit. A one-shot
container sitting in `Exited (0)` is the correct state, not a failure.

## Rebuilding after a code change

`make up` does not rebuild. To rebuild one service:

```bash
docker compose -f docker-compose.quickstart.yml up -d --build smart-app-service
```

To rebuild everything, `make wizard` again — it is idempotent, and layer
caching makes the second run minutes rather than tens of minutes.

## The sandbox images

Three images are spawned per user **at runtime**, not by compose, so they are
built separately — `make wizard` does this on first run:

```bash
bash scripts/quickstart/build-sandboxes.sh
```

They form a chain: `citra-agent-sandbox-base` (from the upstream OpenClaw
image) → `citra-app-builder`, plus an independent `quick-chat-sandbox`. The
base must exist before the builder can build, which is why they are built in
order rather than in parallel.

If this step fails the installer **warns and carries on**, deliberately:
*running* Decision Apps is unaffected. Only *building* them, and code
execution in chat, need these images. Re-run the script once the cause is
fixed.

## Backups

What holds state, and what you actually need to copy:

| Volume | Holds |
|---|---|
| `*_mongodb_data` | apps, specs, users, orgs, the decision ledger |
| `*_milvus_data`, `*_milvus_etcd_data`, `*_milvus_minio_data` | the SOP vector index |
| `*_minio_data` | uploaded documents and generated files |
| `*-pgdata` | the demo tenant's system of record |

`make down` keeps every one of them. Only `make down ARGS=-v` removes them,
and it does so without a second prompt — there is no undo.

## Upgrading

Releases are source archives; nothing is published to a container registry, so
there is no image to pull and no registry to authenticate against.

```bash
curl -sSL https://github.com/Trustedwear-Tech/citra-decision-system/archive/refs/tags/vX.Y.Z.tar.gz | tar xz
cd citra-decision-system-X.Y.Z
cp /path/to/old/.env .            # keep your secrets and model settings
make up                           # or `make wizard` to rebuild everything
```

Read the release notes first: a release that changes the ontology schema or a
database migration will say so.

## Health

Five services are polled by the installer and are the right things to check by
hand:

```bash
curl -fsS http://localhost:7004/health    # citra-user-service
curl -fsS http://localhost:9100/health    # smart-app-service
curl -fsS http://localhost:8085/health    # citra-service
curl -fsS http://localhost:9010/health    # discovery-service
curl -fsS http://localhost:8095/health    # data-discovery-service
```

When one of these does not answer, [Troubleshooting](Troubleshooting) starts
in the right place.
