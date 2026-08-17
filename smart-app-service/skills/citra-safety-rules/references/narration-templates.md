<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# citra-safety-rules — Narration templates

Use these **verbatim** (substitute angle-bracket placeholders) when refusing or surfacing a safety stop. Always cite the rule ID. One sentence. No emoji.

**S-02 (no-source request):**
```
> I build agentic operations apps — a UI and dashboards over your own data, with an AI agent that reads it, recommends decisions, writes back on your approval, and can run on a schedule or webhook. The one thing I need is a source: every read and write has to bind to a dataset your IT team has exposed through an MCP and the data catalogue (rule S-02). The domain is wide open — marketing ops, sales ops, internal operations — as long as there's a source behind it.
```
```
> I don't see a source for <goal> in your tenant catalogue, so there's nothing for the app to run on (rule S-02). Two ways forward: if this data already lives in one of your systems, ask IT to expose that dataset to me via an MCP and the data catalogue; if it doesn't exist yet, ask IT to create the database and business function and expose it the same way. The moment it shows up I'll build the UI, agent, and dashboards on top of it.
```

**W-01 (delete attempted):**
```
> Cannot ship: rule W-01 forbids verb=delete. Use a soft-delete column (is_archived=true) instead.
```

**L-01 (high-stakes decision — confirm autonomy, never refuse):**
```
> This is a high-impact decision (<domain>). How do you want it handled — you click to approve each one, the AI queues a recommendation for your approval, or auto-process the clear cases within a bound you set? And if auto-process, what's the condition (e.g. amount < X, confidence ≥ Y)?
```

**H-04 (chat-write attempted):**
```
> Cannot ship: rule H-04 deprecates hitl_policy.allow_writes_in_chat. Move this write into a queue-action or form on_submit.
```

**T-03 (admin_only tool):**
```
> Cannot ship: rule T-03 forbids admin_only tool <tool_id> in a BA-authored spec. Ask your platform admin to wrap it in a BA-safe action with approvals.
```

**W-03 (reassure: every write is approved):**
```
> Every write this app makes pends in your approval queue and commits only when an officer clicks Approve — there is no auto-execute, so nothing goes through unattended.
```

**C-03 (cron too tight):**
```
> Cannot ship: rule C-03 caps an app trigger's cron at a 5-minute minimum interval. The tightest legal schedule is "*/5 * * * *".
```
