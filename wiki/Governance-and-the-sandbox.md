<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## How the agent is sandboxed


<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/3-governed-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/3-governed-light.svg">
    <img alt="The agent proposes an action, a policy gate bounds it, a person approves before anything is written, and every step is recorded" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/3-governed-light.svg" width="100%">
  </picture>
</p>

The builder is an [OpenClaw](https://github.com/openclaw/openclaw) agent, and it
never runs on your host. Every build session gets its **own container**, spawned
by `action-sandbox-host` and destroyed when the session ends. OpenClaw's own
nested-sandbox feature is switched off deliberately -- this container *is* the
sandbox.

Everything below is applied by
[`action-sandbox-host/scheduler.py`](action-sandbox-host/scheduler.py) on every
spawn. It is code, not a policy document you have to take on trust:

| | |
|---|---|
| **Read-only root filesystem** | `read_only=True` -- the agent cannot modify the image it runs from. |
| **No capabilities** | `cap_drop=["ALL"]` plus `no-new-privileges:true`. |
| **Non-root** | the image drops to `citra` (uid 1500) at build time; nothing runs as root. |
| **No volumes, nothing on your disk** | `/workspace`, `/home/citra` and `/tmp` are size-capped **tmpfs**; no Docker volume is attached at all. They are RAM-backed and die with the container -- anything worth keeping is uploaded through the toolkit before it is reaped. |
| **Bounded** | per-tier `mem_limit`, `cpu_quota` and `pids_limit`, so a runaway agent starves itself rather than the box. |
| **Cannot install anything** | `apt`, `apt-get`, `pip`, `pip3` and `npm` are symlinked to a shim that exits 127 with a one-line explanation, so the agent stops rather than retrying against a read-only filesystem. |
| **Trimmed** | upstream's bundled cloud-provider extensions and its ~50 stock skills are deleted at build time. The agent's skills come only from the workspace seed you control. |
| **Pinned by digest** | the OpenClaw base is `ghcr.io/openclaw/openclaw:<version>@sha256:...`, not a moving tag -- an upstream release cannot change what you run without a commit here. |
| **One per user** | one container per user id, and a fresh gateway token minted per spawn. |

### Network: what is true, and what we will not claim

The sandbox's primary network, `citra-action-egress`, is `internal: true` -- it
has **no default gateway**, so nothing on it can route anywhere.

That is not the whole story, and it would be wrong to tell you the container
simply has no internet:

- The sandbox is **also** attached to `citra-action-approved-egress`, which is
  `internal: false` **by design**. The host needs the `host.docker.internal`
  route on that network to manage sandbox instances; making it internal removed
  the route and broke instance management.
- On that network it reaches only the services you dual-home onto it --
  `citra-mcp-service`, `discovery-service`, and smart-app-service's internal
  proxies. Nothing else is routable, and the sandbox holds no database
  credentials.
- Public egress exists for exactly one thing: **the model endpoint you
  configure**. With `LLM_BASE_URL` pointed at OpenRouter the sandbox does reach
  `openrouter.ai` -- the scheduler deliberately declines to blackhole that host,
  because it is where the model lives.

**Point `LLM_BASE_URL` at a vLLM you run, and the sandbox has no public egress at
all.** That is the sovereign configuration, and it is the one to use when the
data matters.

---
