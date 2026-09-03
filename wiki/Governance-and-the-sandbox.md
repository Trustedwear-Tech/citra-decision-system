<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

# Governance and the sandbox

Two questions, answered separately because they are different: **what is the agent allowed to do**, and **what can it reach even if it tries**.

## What the agent is allowed to do

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/3-governed-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/3-governed-light.svg">
    <img alt="The agent proposes an action, a policy gate bounds it, a person approves before anything is written, and every step is recorded" src="https://raw.githubusercontent.com/Trustedwear-Tech/citra-decision-system/main/assets/story/3-governed-light.svg" width="100%">
  </picture>
</p>

This is the part people most want to understand, so here it is in full.

**The AI never writes SQL against your systems.** Not "we ask it not to" --
there is no field on the request where a statement could travel.

What you declare in the ontology is a **write action**: a named, human-authored
operation on one table. Its heart is a fixed parameterized statement you wrote
yourself. For example:

```json
{
  "id": "record_credit_decision",
  "verb": "update",
  "sql_template": "UPDATE loan_applications SET status=:status, decision_reason=:decision_reason, decided_by=:decided_by, decided_at=:decided_at WHERE application_id=:application_id",
  "key_fields": ["application_id"],
  "roles_allowed_write": ["dept_admin", "org_admin", "super_admin"],
  "input_schema": {
    "type": "object",
    "required": ["application_id", "status"],
    "properties": {
      "status":       { "type": "string", "description": "approved | rejected | under_review." },
      "decided_by":   { "type": "string", "x-citra-fill": "actor" }
    }
  }
}
```

Read what that guarantees. **The statement only `SET`s four columns, so those
are the only four columns any AI, any officer, or any API caller can ever
change through this action.** Not because something checks a per-column
"updatable" flag -- there is no such flag -- but because the statement is
fixed and the model never gets to write one. To make a fifth column writable,
a human edits the ontology and the change goes through review like any other
code.

What the model actually produces is not SQL but a small structured object:

```
{ dataset_id: "loan_origination.loan_applications",   <- chosen from a fixed list
  action_id:  "record_credit_decision",               <- chosen from a fixed list
  payload:    { application_id: "...", status: "rejected", decision_reason: "..." } }
```

Both ids come from an enumerated list bound to the app, so the model is
picking from a menu, not composing a command. That request goes to the MCP,
which independently re-checks, in order:

1. the caller's service credentials and user token are valid;
2. the action is actually registered on that dataset (unknown -> rejected);
3. the caller's **role** is allowed to write (default: department admin and
   above -- an empty allow-list does not mean "everyone");
4. the payload carries every required field, and the key fields are present;
5. then, and only then, the values are **bound as parameters** to your stored
   statement. Nothing is ever string-concatenated into SQL.

Two fields are stamped by the server and cannot be forged by the payload:
`x-citra-fill: actor` binds the verified identity from the token, and
`now` binds the server clock. So "who decided this, and when" is not
something the AI gets to assert.

**And a human still approves.** During a case the agent's proposed writes are
only *staged* -- collected as `planned_writes`, shown to the officer with the
exact values. Approval replays precisely those staged writes, with no second
model round-trip, so what was approved is what commits. If the plan changed
between display and approval, a hash check rejects it. Officers can edit
values before approving, but only fields the app marks editable, and an edited
payload is re-validated before it commits.

Two more guarantees worth stating:

- **Chat is structurally read-only.** The write tool is blocked at dispatch in
  the chat surface unconditionally -- not by prompt, by code.
- **Read before write.** An agent may not stage a write about a record it never
  actually read. This is enforced in the human-approval path; on the fully
  unattended path it currently ships in observe-and-log mode, so treat that
  one as reporting rather than blocking.

Being straight about the trust boundary: the platform does not parse or
sanity-check your `sql_template`. It executes the statement you registered. The
security property is that *only a human can author or change one*, and it is
reviewed like code -- not that the system validates the SQL you wrote. Also
note that on the SQL path an extra, undeclared field in the payload is simply
ignored rather than rejected; it cannot reach the database, because the
statement does not mention it.

Deep dive: `docs/write-actions.md`.

## How the agent is sandboxed



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
