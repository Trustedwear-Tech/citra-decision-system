<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Access control — read / write role gating

Status: **current state + planned direction.** This note records a
deliberate decision so it isn't lost: SmartApp Service and the dept MCP
(`source-mcp-template`) will gate user access by **distinct read and
write roles**, not a single access check.

## Why

Today access is gated by four coarse roles from the Citra-User-Service
JWT `roles` claim — `user`, `dept_admin`, `org_admin`, `super_admin` —
and historically a caller who could *reach* a data source could also
*write* to it. That conflates two very different privileges. Reading a
quality test result and *releasing a batch to dispatch* are not the
same act and must not require the same permission.

## Current state (the first half-step — already shipped)

The dept MCP now enforces **two separate gates** on every data-plane call:

| Gate | Where | Question |
|---|---|---|
| **Read visibility** | `auth.check_visibility` — the source's `visibility.roles_allowed` / dept / org rules | Can the caller *see* this source? |
| **Write permission** | `auth.check_write_permission` — the action's `WriteAction.roles_allowed_write` | Of those who can see it, who can *write*? |

- `/query`, `/run_query`, `/datasets*` → read gate only.
- `/execute_action` → read gate **and** write gate.
- A write action with no `roles_allowed_write` defaults to
  `DEFAULT_WRITE_ROLES = (dept_admin, org_admin, super_admin)` — i.e.
  **writes are privileged by default; a plain `user` cannot write**
  unless an action explicitly lists `"user"`.
- Every outcome (read-deny, write-deny, exec-fail, success) is audited
  to `dept_query_audit`.

This is correct but still coarse: read and write both draw from the
same four-role set. There is no way to grant a principal "read source X,
never write" other than the blunt `user`-vs-`dept_admin` split.

## Planned direction

Both **SmartApp Service** and the **dept MCP** will move to explicit
**read and write roles (capabilities)** so user access is gated
granularly:

- A principal will hold distinct grants — conceptually `read:<source>`
  and `write:<source>` (or `<source>:reader` / `<source>:writer`) —
  rather than inferring write rights from a coarse org/dept role.
- The MCP: `visibility.roles_allowed` remains the read gate;
  `roles_allowed_write` becomes the write gate's role set — both
  evolving toward per-source/per-dataset capability checks against the
  caller's granted capabilities, not just role-name intersection.
- SmartApp Service: an app/agent that invokes a write action must carry
  the **write** capability for the bound dataset; a read-only app
  requires only **read**. The builder should surface this when an app
  wires a `write_action`, and the publish path should refuse to ship a
  writing app to principals lacking write capability.

Until that lands, the `roles_allowed` (read) + `roles_allowed_write`
(write) split above is the interim model — keep declaring both, and do
**not** assume read access implies write access anywhere new.

## For implementers

- New dept-MCP write actions: always declare `roles_allowed_write`
  (don't rely on the default) and keep it as narrow as the operation
  warrants — see `demo-data/tenants/acme-bank/mcp/sources.json` for
  worked examples (`release_batch`, `create_dispatch` →
  dept_admin+; `update_dispatch_status` → also `user`).
- New SmartApp surfaces: treat read and write as separate authorization
  questions. Never gate a write with only a read/visibility check.
