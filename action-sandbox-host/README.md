<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Action Sandbox Host

One instance per sandbox-host VM. Owns that VM's local Docker daemon and
exposes a small HTTP API consumed by the **stateless Citra-Service fleet**
to spawn, terminate, and manage per-user action sandboxes.

This is the implementation of **Pattern C** — the sandbox pool is decoupled
from Citra-Service, so any Citra-Service replica can reach any sandbox
directly via the lease's adapter URL. No more reverse-proxy between
Citra-Service replicas.

## Why a dedicated service?

Before (Pattern B): every Citra-Service replica bound `/var/run/docker.sock`
and spawned sandboxes locally. This coupled Citra-Service to Docker,
required user→replica stickiness via Redis lease + reverse-proxy, and made
scaling Citra-Service expensive because each replica needed sandbox capacity.

After (Pattern C):
- Citra-Service: stateless HTTP servers. Scale horizontally for free.
- Sandbox-hosts: dedicated VMs (usually fewer, beefier). Run this scheduler
  + Docker. Scale independently when sandbox load grows.

## Endpoints

All endpoints except `/health` require `X-Sandbox-Host-Secret: <shared>`.

| Method | Path                             | Purpose                              |
|--------|----------------------------------|--------------------------------------|
| GET    | `/health`                        | Liveness (unauthenticated)           |
| GET    | `/capacity`                      | Remaining slots + cpu/mem %          |
| POST   | `/spawn`                         | Launch a sandbox, return adapter URL |
| DELETE | `/session/{session_id}`          | Stop + remove a sandbox              |
| POST   | `/session/{session_id}/upload`   | Copy a file into `/workspace/...`    |

## `POST /spawn` contract

```json
{
  "user_id": "u_12345",
  "session_id": "sess_abc",
  "volume_name": "citra-action-user-u_12345",
  "env": {
    "CITRA_USER_ID": "u_12345",
    "CITRA_SESSION_ID": "sess_abc",
    "CITRA_SCOPED_TOKEN": "…",
    "CITRA_CONTROL_SECRET": "…",
    "CITRA_LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "CITRA_LLM_API_KEY": "…",
    "CITRA_ACTION_MODEL": "citra-action-2026",
    "OPENCLAW_GATEWAY_TOKEN": "…"
  }
}
```

Response:

```json
{
  "session_id": "sess_abc",
  "container_id": "a1b2c3…",
  "container_name": "citra-action-sess_abc",
  "host_port": 31055,
  "adapter_url": "http://sandbox-host-1:31055",
  "public_host": "sandbox-host-1"
}
```

Citra-Service then persists `adapter_url` into the Redis lease. Every
subsequent turn goes directly to that URL — no replica-to-replica proxy.

## Running locally (dev)

```bash
pip install -r requirements.txt
export SANDBOX_HOST_PUBLIC_HOST=localhost
export SANDBOX_HOST_SECRET=dev-secret
python main.py          # listens on :7090
```

## Production per-host install

```bash
cd action-sandbox-host
cat > .env <<'EOF'
SANDBOX_HOST_PUBLIC_HOST=sandbox-host-1.internal
SANDBOX_HOST_SECRET=<long-random-string>
DOCKER_GID=$(getent group docker | cut -d: -f3)
EOF

docker compose up -d --build
```

Then in Citra-Service's env:

```
CITRA_ACTION_SANDBOX_HOSTS=sandbox-host-1.internal:7090,sandbox-host-2.internal:7090
CITRA_ACTION_SANDBOX_HOST_SECRET=<same long-random-string>
```

## Networking note

Sandboxes publish adapter `:8090` to a random host port in
`[SANDBOX_HOST_PORT_MIN, SANDBOX_HOST_PORT_MAX]` (default `31000-31999`).
Citra-Service reaches them via `{public_host}:{random_port}`, so your
internal firewall / security group must allow Citra-Service VMs to connect
to that port range on every sandbox-host.

If you prefer a flat overlay network instead (Docker Swarm / Kubernetes
Service), `host_port` still comes back in the spawn response, but you can
ignore it and construct the adapter URL from the container name — that
lives in a future version of this service.
