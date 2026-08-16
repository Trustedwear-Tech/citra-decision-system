---
name: citra-mcp-discover
description: Enumerate available MCPs and dept-mcps from discovery-service
metadata:
  category: citra
  tools: [bash]
---

# Citra MCP Discover

## Purpose
Phase 1 (Internship) — find out what data and capability already exists in the customer's Citra deployment so the agent can plug into it instead of reinventing.

## When to Use
- First step of any Power AI App build.
- When the BA mentions a domain ("claims", "vendors", "invoices") — check if a dept-mcp already covers it.
- When extending an app and the user asks about a new data source.

## Endpoints
- `GET ${DISCOVERY_SERVICE_URL}/tools/available` — active dept-mcps (source list only — no datasets, no write actions).
- `GET ${SMART_APP_SERVICE_URL}/builder/catalogue?full=true` — the **dataset catalogue**: every dataset with its `columns[]` and, crucially, its `write_actions[]`. This is the only place write actions appear. (`${SMART_APP_SERVICE_URL}` is the smart-app-service base — the same host `citra-app-publish` POSTs `/publish` to.)
- Per-tool taxonomy is included when the dept-mcp registers it.

## Safety rules (citations)

- **W-01** — Write actions are MCP-owned. Discovery only enumerates what the catalogue registers; never synthesise a write that the catalogue does not expose.
- **W-02** — Action ids, dataset ids, and input schemas are catalogue-pinned. Record them verbatim — do not paraphrase, lowercase, or "fix" them.
- **Record each dataset's `row_count` (`row_count_approx`) alongside its `columns[]`.** It's the size signal `citra-app-spec` / `citra-ui-charts` use to keep the design size-aware — filter a large table, give a time-series chart a `time_grain`, and avoid a high-cardinality / id GROUP BY. A dataset whose size is unknown should be treated as potentially large.
- **T-03** — When extracting actions from the catalogue, refuse to copy any action tagged `admin_only=true`. These are reserved for source-system admins and must never be wired into a SmartApp tool surface.

Refer to [citra-safety-rules](../citra-safety-rules/SKILL.md) for the canonical rule list.

## Workflow

Narrate every step per the `Narration convention` in [`AGENTS.md`](../../AGENTS.md). The BA should never see silent stretches while you're enumerating.

1. **Narrate**, then call discovery:
   ```
   > 🔍 Looking at what dept-MCPs your tenant has registered...
   ```
   ```bash
   curl -sS -H "Authorization: Bearer $CITRA_JWT" \
     "$DISCOVERY_SERVICE_URL/tools/available" \
     > /workspace/build/discovery.json
   ```
2. **Emit a finding** with the headline number + names:
   ```
   > ✅ Found 3 dept-MCPs: claims, policies, customers
   ```
   If discovery returned **zero** MCPs, narrate the warning instead:
   ```
   > ⚠️ No dept-MCPs registered for your tenant yet — I'll need to flag this to the BA before we can design an app
   ```
2b. **Pull the dataset catalogue — datasets AND write actions live here, not in `/tools/available`:**
   ```bash
   curl -sS -H "Authorization: Bearer $CITRA_JWT" \
     "$SMART_APP_SERVICE_URL/builder/catalogue?full=true" \
     > /workspace/build/catalogue.json
   ```
   Each entry carries `dataset_id`, `source_id`, `columns[]` and
   `write_actions[]`. Every write action has an `id`, the `dataset_id`
   it runs on, and an `input_schema`. **Naming:** the catalogue field is
   `id`; the AgentSpec `mcp_action` tool field is `action_id` — they are the
   **same value**. Copy `write_actions[].id` verbatim into the tool's
   `action_id`. **Record all of these verbatim** — Phase 2 copies them into
   `mcp_action` tools and must never invent an action id, dataset id, or schema.
3. **Narrate the per-MCP inspection**:
   ```
   > 🔍 Checking the claims MCP — which datasets and write actions it exposes...
   > ✅ claims MCP: 4 datasets (claims, policies_active, customers, agents), 3 write actions (mark_processed, mark_escalated, mark_rejected)
   ```
4. Save the relevant tool ids, **dataset ids, write-action ids + their input_schema**, source `source_type`, and dept-mcp names. Phase 2 wires by `source_type`: a **STRUCTURED** source's reads → `kind:"mcp"`; a **SEMANTIC** (RAG / document / policy) source's reads → **`kind:"rag"`** (short-circuited to the Citra platform reader / Milvus — the dept-MCP serves NO RAG, so wiring a semantic source as `kind:"mcp"` 404s at runtime). Every write action → `kind:"mcp_action"`.
5. **Summarise to the BA in plain prose** (no `>` prefix — this is the actual message the BA acts on): which departments are covered, which doc types are available (policy/sop/manual/contract/regulation), and which structured tables exist. Example:
   > "Your Citra installation has connections to the claims, policies, and customers systems, plus a knowledge base of 240 policy documents and 80 SOPs. We can read from and write back to all three."

## Preflight probe — confirm each source actually returns data

Cataloguing proves a dataset *exists*; it does not prove it *returns rows* or
that the live read path holds. Two failures hide here until it's too late —
**both must be caught now, before Phase 2:**

- **Unresolvable** — the catalogue is stale and a `dataset_id` / column /
  `action_id` you recorded no longer resolves at runtime. The publish validator
  **hard-rejects** this (`E_UNKNOWN_DATASET` / `E_UNKNOWN_COLUMN` /
  `E_UNKNOWN_ACTION`) — *after* you've designed the whole app. A green probe
  here means the validator won't reject on resolvability.
- **Registered but empty** — the dataset resolves but has **zero rows**. The
  validator passes it (it only checks the *shape*, not the *contents*); the app
  publishes and renders a blank tile / chart / queue.

For every dataset / write-action / RAG source the BA's goal will actually use
(not the whole catalogue — only what the app needs), run two cheap probes
against smart-app-service. Narrate each per the `Narration convention`.

1. **Connectivity / resolvability** — `POST /builder/probe` (read-only: `mcp`/
   `rag` send a `limit=1` query; `mcp_action` sends a `dry_run=true` execute —
   never commits):
   ```bash
   curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/probe" \
     -H "Authorization: Bearer $CITRA_JWT" -H "Content-Type: application/json" \
     -d '{"kind":"mcp","source_id":"<src>","dataset_id":"<ds>"}'
   ```
   `ok:false` → the source/auth/network path is broken or the ref doesn't
   resolve. This is the **Discovery error** failure mode — surface the `detail`
   to the BA, do not design around it. (For a write action, send
   `{"kind":"mcp_action","source_id":"<src>","dataset_id":"<ds>","action_id":"<aid>"}`
   — a green dry-run is your assurance the write will fire at runtime.)

2. **Non-emptiness** — `POST /builder/sample` (real rows, capped
   at 20):
   ```bash
   curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/sample" \
     -H "Authorization: Bearer $CITRA_JWT" -H "Content-Type: application/json" \
     -d '{"source_id":"<src>","dataset_id":"<ds>","limit":3}'
   ```
   Empty `rows[]` → the table resolves but has no data **yet** (the source may
   not be populated). This is a **warning, not a blocker** — *warn the BA and
   let them decide*: *"The `tamper_events` table is registered but has no rows
   right now — a tile on it will be empty until your team loads data. Include it
   anyway (it fills in once data lands), or leave it out?"* **If the BA says
   proceed, build it** — the panel populates automatically when the source has
   data; you do not remove it. Record the gap in `requirements_unmet` either
   way. **Never** substitute synthetic rows to make a panel look populated.
   (RAG sources are already covered by `citra-rag-probe`'s hit counts — if that
   corpus is empty, warn that policy lookups return nothing until IT indexes it,
   then proceed if the BA wants to. Don't double-probe RAG with `/builder/sample`.)

```
> 🔍 Probing the complaints + outages + tamper tables to confirm they return data...
> ✅ complaints: 3 sample rows · outages: 3 sample rows
> ⚠️ tamper_events: registered but returned 0 rows — flagging to the BA before building a tile on it
```

A clean preflight here is what stops the build→publish→422-reject loop, and
what stops a "successful" publish that renders an empty app.

## What to Extract
- For each tool: `tool_id`, `display_name`, `source_type` (semantic/structured), `taxonomy.doc_types[]` (if any), `query_timeout_seconds`, business description.
- For each catalogue dataset: `ref`, `dataset_id`, `source_id`, `columns[]`. **`ref` (== `dataset_id`, already source-qualified `<source_id>.<table>`) is the EXACT string that later becomes a `data_source.ref` in the AppSpec — carry it through verbatim.** `source_id` is metadata only; **never** combine it with `ref`/`dataset_id` (no `source_id/dataset_id` paths) — that yields an unresolvable ref and a blank app.
- For each `write_actions[]` entry on a dataset: `id` (→ the tool's `action_id`), `dataset_id`, `source_id`, `input_schema` — the exact tuple a `kind:"mcp_action"` tool needs. A "review / triage / approve / record-a-verdict" goal almost always needs one of these; if the catalogue exposes a matching write action, the app is incomplete without it.
- **For every status / category / state / type column you will use** (in a status flow, a filter, a write-action option, or a form select): capture the column's **real allowed values from `columns[].distinct_values`** in the catalogue — that IS the enum. If `distinct_values` is empty or absent (the crawler caps it at ~10, so a low-cardinality status field is fully captured but a high-cardinality one is truncated), run a `SELECT DISTINCT "<col>" FROM "<table>"` via `citra_discovery_query` **before** you design the flow. Record the exact set + casing. **Do this up front in Phase 1 — never propose status values from the field name or domain intuition.**

## BA-friendly output
Translate the discovery dump into one or two plain English paragraphs. Don't show JSON to the BA. Examples:
> "Your Citra installation already has a connection to the Insurance department's claims database and a knowledge base of 240 policy documents and 80 SOPs."

## Hard Rules
- Never invent a tool, dataset, or write action that isn't in discovery / catalogue output. Action ids and dataset ids are copied **verbatim** — an improvised `flag_confirmed_leak` on a guessed `suspect_records` dataset will fail at publish or at runtime.
- **`mcp_action.dataset_id` (and `mcp` read refs) = the catalogue's `dataset_id` VERBATIM — the source-qualified form (`field_operations.theft_cases`).** Do **not** strip it to the bare table name (`theft_cases`): the dept-MCP matches its registered dataset id **exactly**, so a bare name 404s ("Dataset 'theft_cases' not found on source 'field_operations'") at probe time and at runtime. `source_id` is a **separate** field (`field_operations`); the two are NOT redundant — keep `dataset_id` fully qualified even though it repeats the source. (If a static check ever appears to want the bare form, that's a checker bug — do not strip; the runtime needs the qualified id.)
- **Never invent a field VALUE either** — statuses, categories, states, types, option lists. Bind a status flow / filter / select / write-action option **only** to values you actually observed in `distinct_values` (or a `SELECT DISTINCT` probe). Guessing `pending → partial → recovered` when the data only holds `pending / under_recovery / recovered / disputed` either **throws a write exception** (the source rejects an unknown enum value) or **filters to zero rows**. If the BA wants a value that isn't in the data, that's a *source change* (ask IT to add it) — record it in `requirements_unmet`; do **not** fabricate it into the spec. Probe the real values in Phase 1 so you propose the correct flow the first time, not after the BA catches it.
- If the goal needs a data source / write action that the catalogue does **not** expose, do **not** silently scaffold a fake one and do **not** approximate it with an unrelated dataset. Tell the BA plainly that a SmartApp can only build on data a dept-MCP exposes through the catalogue, and offer the **two ways forward**: **(1)** if the data already exists in one of their systems, ask IT to **expose that dataset** to you via its dept-MCP + the data catalogue; **(2)** if it doesn't exist yet, ask IT to **create the database and business function and expose it** through MCP + the data catalogue. Record it in `requirements_unmet` and let the BA decide whether to build a partial app around what *does* exist or pause for IT. The verbatim BA message lives in **AGENTS.md → Phase 1 → Discovery failure modes → "Requested data or capability is not in the catalogue."**
- Cache **both** dumps under `/workspace/build/` — `discovery.json` (sources) and `catalogue.json` (datasets + write_actions) — so Phase 2 can re-read them.

## Empty / failing discovery — stop and surface

Citra is the UX layer over a source-system catalogue you do not own. When discovery comes back empty or errors, **stop the build and tell the BA in their own words** (AGENTS.md Hard Rule 11). The verbatim BA messages for the zero-MCPs / HTTP-error / partial-discovery cases live in **AGENTS.md → Phase 1 → Discovery failure modes**. One case is specific to this skill:

- **Zero catalogue datasets from `/builder/catalogue`** even when `/tools/available` returned MCPs → the MCPs are up but registered without datasets. Surface it precisely:
  > "I see the `<mcp_names>` MCPs are registered, but none of them have any datasets catalogued — IT has connected the systems but not yet declared which tables/collections to expose. The build can't proceed without datasets."

Never retry silently, never invent a placeholder dataset to "unblock" the build, never paraphrase a real error as "everything looks fine". The BA will discover the gap the moment they open the app and see empty queues.
