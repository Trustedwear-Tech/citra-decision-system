<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# The SmartApp system — read this before you build

You are about to author an **AppSpec** (+ usually an **AgentSpec**). You are not
writing UI code and you are not filling in a form against a rulebook. You are
**coding for a system that already exists** — two engines that will render and
execute your spec. This document is your onboarding to that system. Read it, then
read the parts of the real code (under `runtime-reference/`) that matter for what
you're building, *then* author the spec. Understand first; the gates are a floor,
not the design.

The whole system is here in the pod, as real code:

```
runtime-reference/
  renderer/    citra-app-runtime/src/   — HOW your spec RENDERS (Next.js)
  executor/    smart-app-service/*.py   — HOW your spec is QUERIED, CALLED, RUN
  validators/  smart-app-service/*.py   — HOW your spec is CHECKED at publish
  MANIFEST.md                           — what each file is + the snapshot date
```

This is **ground truth**. When this code and any prose skill disagree, the code
wins (and flag the drift). Locate it:
```bash
REF=$(dirname $(find /workspace -path '*citra-system/SKILL.md' 2>/dev/null | head -1))/runtime-reference
```

---

## 1. The two specs, and who consumes them

- **AppSpec** — the *surface*: `pages[]` → `panels[]`, the `data_sources[]` they
  bind to, navigation, theme, and `triggers[]`. Rendered by **renderer/**.
- **AgentSpec** — the *brain*: `tools_v2[]` (mcp reads, rag, mcp_action writes,
  validate_form, …), `actions[]` (entry points a panel button fires),
  `system_prompt`, `model_tier`. Run by **executor/** (`runtime.py`).

The **authoritative shape of both** is the Pydantic in
`executor/models.py` (`class AppSpec`, `class AgentSpec`, `extra="forbid"`). If a
field isn't there, the runtime rejects or drops it. The TypeScript view the
renderer compiles against is `renderer/types/spec.ts`. **These two files are the
field contract — not any prose list.**

## 2. The lifecycle of a spec (and the file that owns each stage)

```
   you author          →  validate            →  publish        →  render            →  run
   app_spec.json          validators/            (stored test_)    renderer/           executor/
   agent_spec.json        validators.py          smart-app-svc     PanelRenderer.tsx   runtime.py
                          publish_validators.py                     pages.ts            panel_data.py
                          data_binding_validator.py                                     tools_v2_dispatch.py
```

- **Validate** — `validators.validate_app_spec` runs **JSON-Schema + Pydantic**;
  `publish_validators.py` runs the **publish gates** (the W-/H-/T-/S-/D- rules,
  `update_identifier`, `editable_fields`, G-01, …). Read these to know *exactly*
  what will reject your spec — they are the real rules, not the prose.
- **Render** — `pages.ts` resolves the URL → a page; `PanelRenderer.tsx` renders
  each panel by `switch (panel.type)` (FAILS LOUD on an unknown type).
- **Run** — a panel button fires an action → `runtime.py:execute_run` runs the
  agent loop; `panel_data.py` turns a `data_source` into a query;
  `tools_v2_dispatch.py` dispatches each agent tool.

## 3. THE VERB-HALF — what the runtime DOES that your spec never shows

This is the half that has caused almost every bug. Your spec declares the **nouns**
(panels, tools, data). The runtime supplies the **verbs** — behavior that is *not
written anywhere in the spec*. You must know these or you will author a spec that
is shape-valid yet wrong. Each one says **what happens** and **where to read it**.

1. **A queue-row button SENDS THE WHOLE ROW to the agent.** On click,
   `renderer/components/PanelRenderer.tsx → fireAction` posts
   `inputs = { ...row, ...action.args }`; `executor/runtime.py` injects that whole
   record into the agent prompt under `Inputs:`. **⇒ The agent ALREADY HAS the
   selected record. Never write a `system_prompt` that re-reads it — that is the
   #1 cause of looping/slow runs.** Read it only for OTHER rows. (Read:
   `PanelRenderer.tsx` `fireAction`; `runtime.py` the `Inputs:` block.)

2. **Dashboard KPIs are computed at the source, and only some aggs work.**
   `executor/panel_data.py → _resolve_dashboard_metrics / _agg_expr /
   _metric_source_computable` — `count/sum/avg/min/max` + `ratio` compute; anything
   else falls back and the tile renders blank. A `ratio` returns a 0..1 fraction
   that `renderer/PanelRenderer.tsx → kpiFromServer` formats as a **percent**. (Read
   both before using any agg beyond count/sum.)

3. **An `mcp` read tool is a NATURAL-LANGUAGE query re-planned to SQL on every
   call — not a cheap scoped fetch.** `executor/tools_v2_dispatch.py` (kind `"mcp"`)
   exposes only `{query, args, max_results}` to the LLM; the dept-MCP re-plans
   NL→SQL each call. **⇒ unscoped reads are slow and imprecise, and the agent will
   loop.** Scope by the record's ids; read each source once. (Read: the `kind ==
   "mcp"` branch.)

4. **`mcp_action` (write) tools carry the action's `input_schema` verbatim.** The
   LLM is given that schema; `editable_fields[].name` must be real properties of it.
   (Read: the `kind == "mcp_action"` branch + `publish_validators` editable_fields.)

5. **`data_source.ref` is `"<source_id>.<table>"`, no `/`.** The resolver splits on
   the first `.`; a wrong shape → "did not resolve." (Read: `panel_data.py
   _resolve_mcp_rows`.)

6. **Detail panels have NO data_source — they bind via `linked_to` + an
   auto-detected id field.** `panel_data.py` (`_detect_id_field`, `_ID_FIELD_PATTERNS`)
   guesses the id column by regex. Mis-wire the `linked_to` and the detail shows
   nothing. (Read: `resolve_detail_data`.)

7. **A `format:"file"` field works even against a plain string column** — the
   platform stores the blob in S3 and writes a ref string; the field name MUST match
   the write column. So don't escalate "no file column"; do name the field correctly.
   (Read: `data_tools.py` / the file-write path; and `forms-and-files` guidance.)

8. **Navigate `{row.X}` params are dropped if `X` wasn't a selected column.**
   `renderer/lib/pages.ts → substituteParams` silently drops unresolved templates.
   ⇒ the queue must SELECT the columns your navigate/scoping needs. (Read:
   `substituteParams`.)

9. **Charts push GROUP BY to the source; only some time_grains are handled.**
   `panel_data.py → _resolve_chart_aggregated / _build_chart_agg_sql`. `x/y/group_by`
   must be real columns; an unhandled grain falls back. (Read those functions.)

10. **The full action `inputs` reach the prompt; input validation does NOT strip
    extra keys.** `runtime.py` validates inputs against the action `input_schema`
    (jsonschema `validate`, which does not drop extras), then injects ALL of `inputs`.
    ⇒ declaring the action's `input_schema` to describe the record makes the record
    *visible to you* as an input — you then know not to re-fetch it.

> If you find a behavior here that the code no longer matches, the **code wins** —
> use it and note the drift in your hand-off so this map gets fixed.

## 4. Navigation — building X? read exactly these

**Read BOTH layers for each feature — RENDER *and* EXECUTE — plus its validator.**
Each row below lists a `renderer/` file (how it displays) AND an `executor/` file
(how it's computed/queried/run) on purpose. A feature can be valid in one layer and
broken in the other — a metric the renderer would show fine but the executor can't
compute (blank tile); a chart the executor parses but the renderer needs as a
structured block (raw JSON). **Read every file a row lists, not just one**, and check
the `validators/` rule that gates it. One-layer reading is how bugs survive.

| You're authoring… | Read in `runtime-reference/` |
|---|---|
| a **dashboard** page | `renderer/.../PanelRenderer.tsx` `DashboardPanel`+`kpiFromServer`; `executor/panel_data.py` `_resolve_dashboard_metrics`,`_agg_expr` |
| a **chart** panel | `PanelRenderer.tsx` chart branch + `renderer/lib/chartToEcharts.ts`; `executor/panel_data.py` `_resolve_chart_aggregated` |
| a **queue** + an **agent action** on it | `PanelRenderer.tsx` `QueuePanel`+`fireAction`; `executor/runtime.py` `execute_run` (the `Inputs:` injection) |
| a **detail** page | `PanelRenderer.tsx` detail branch; `executor/panel_data.py` `resolve_detail_data`,`_detect_id_field` |
| an **mcp read** / **mcp_action** tool | `executor/tools_v2_dispatch.py` (`kind == "mcp"` / `"mcp_action"`) |
| a **form** + submit | `PanelRenderer.tsx` form branch + `forms-and-files`; `executor/runtime.py` form-submit path |
| any **field/enum/required** question | `executor/models.py` (the Pydantic) + `renderer/types/spec.ts` |
| "**will this publish?**" | `validators/publish_validators.py` (the gate that matches your feature) |

## 5. How to use all this (the workflow)

1. **Read this map.** Build a mental model of how your intended app will render, be
   queried, run, and validated — *especially the verb-half in §3*.
2. **Read the code slices** the navigation table points to, for the components you'll
   actually use. Targeted reads — like a developer opening the relevant files, not
   the whole tree.
3. **Author the spec to match the real behavior** you just read — not what a prose
   skill once said, not what you'd assume a generic runtime does.
4. **The gates are the floor.** `citra_spec_validate`, `static_checks`,
   `/builder/preview-smoke`, `/builder/smoke-run` exist to catch the rare miss — if
   you authored from understanding, they pass first try. If a gate fires, the fix is
   usually that you skipped reading the slice that governs it.

You are a coder who reads the codebase, reasons, and writes the right spec — not a
form-filler who gets corrected. That is the whole job.
