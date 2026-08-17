<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# citra-app-builder

The smart-app-service's per-session ephemeral builder pod image. Layered on
the neutral `citra-agent-sandbox-base`; adds the smart-app builder persona
(SOUL / IDENTITY / USER / MEMORY) plus the 16 builder skills + schemas +
AGENTS.md + TOOLS.md from `smart-app-service/`.

## Symmetric pair

| | `action-chat-service/sandbox/` | `smart-app-service/builder-sandbox/` |
|---|---|---|
| Built tag | `citra-action-chat-sandbox:latest` | `citra-app-builder:latest` |
| Consumer | action-chat-service | smart-app-service |
| Workload | Deep multi-step chat / research | Build a Citra Smart App from a BA goal |
| Spawn rule | `SANDBOX_HOST_IMAGE` (action-sandbox-host default) | `SANDBOX_HOST_BUILDER_IMAGE` |
| Lifetime | Per-user (long-running) | Per-session (ephemeral) |
| Persona files location | `./workspace-seed/` (in-repo) | `./workspace-seed/` (in-repo) + cross-COPY from `smart-app-service/skills/`, `smart-app-service/schemas/`, `smart-app-service/builder-workspace/` |
| Research sub-agents | 6 (researcher/analyst/reporter/excel-generator/chartist/synthesizer) â€” personas in `./workspace-seed/agents/`, wiring in `./openclaw.config.overlay.json` | **none** (no overlay â†’ base `agents.list` = `[main]`) |

Both consumers share the same on-disk shape: `Dockerfile` + `workspace-seed/`
+ `README.md`. Each owns its own persona; the base stays neutral.

### Sub-agent roster lives only with action-chat

The base `openclaw.config.template.json` declares only the `main` agent with an
empty `subagents.allowAgents`. The deep-research sub-agent roster is **not**
wired in the shared base â€” both its **personas** (`workspace-seed/agents/<name>/AGENTS.md`)
and its **delegation wiring** (`openclaw.config.overlay.json`, deep-merged onto
the rendered config by the base `entrypoint.sh`) ship only with the action-chat
consumer. This builder ships neither, so its `main` agent correctly advertises
no delegation tool. Previously the base wired all 6 sub-agents while only
action-chat supplied their personas, so the builder inherited 6 personaless
delegatable shells it must never use â€” that split-brain is now closed.

## Build

Build context is `smart-app-service/` (the Dockerfile COPYs from sibling
dirs `builder-workspace/`, `schemas/` and `skills/`):

```bash
cd c:/Github/Citra-AI
docker build \
  -f smart-app-service/builder-sandbox/Dockerfile \
  -t citra-app-builder:latest \
  smart-app-service
```

The base image (`citra-agent-sandbox-base:latest`) must be built first via
`infrastructure/action-sandbox/Dockerfile`.

## Workspace-seed contents (at /srv/citra/workspace-seed/ in the image)

| File / Dir | Source | Owner |
|---|---|---|
| `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md` | `./workspace-seed/` | this dir |
| `AGENTS.md` | `smart-app-service/builder-workspace/AGENTS.md` | smart-app-service team |
| `TOOLS.md` | `smart-app-service/builder-workspace/TOOLS.md` | smart-app-service team |
| `schemas/` | `smart-app-service/schemas/` | smart-app-service team |
| `skills/citra-*/` (16 skills) | `smart-app-service/skills/citra-*/` | smart-app-service team |

## Adding a new skill

1. Author the skill under `smart-app-service/skills/<your-skill>/SKILL.md`.
2. Reference the skill in `smart-app-service/builder-workspace/AGENTS.md`.
3. Rebuild: `docker build -f smart-app-service/builder-sandbox/Dockerfile -t citra-app-builder:latest smart-app-service`.

No Dockerfile edit needed: the whole `skills/` tree ships via a single
wildcard COPY (the old per-skill-COPY drift trap is closed — see project
memory `project_app_builder_dockerfile_drift.md`).

## History â€” why this dir replaces `citra-app-builder/`

Before 2026-05-19, this image was built from `citra-app-builder/Dockerfile`
at the monorepo root, and its COPYs landed at `/workspace/AGENTS.md` and
`/skills/citra-*/` â€” paths the OpenClaw agent **never reads**. The agent
loads from `/srv/citra/workspace-seed/` (seeded by the base entrypoint
into `/workspace/.openclaw/workspace/`). All baked-in skills sat unread,
and the agent fell back to whatever persona was in the base image
(action-chat's, since the base wasn't neutral).

Resolved by the 2026-05-19 architectural refactor:
- Base image (`infrastructure/action-sandbox/`) made neutral (no
  workspace-seed COPY).
- Action-chat persona extracted into `action-chat-service/sandbox/`.
- Smart-app builder persona moved here, paths fixed to `/srv/citra/workspace-seed/`.
- `action-sandbox-host/config.py` `sandbox_image` default updated.

See `smart-app-service/tests/integration/cement-e2e-report.md` for the
diagnostic that surfaced the bug.
