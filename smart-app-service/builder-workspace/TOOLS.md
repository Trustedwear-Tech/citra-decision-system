<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Tools — Builder Surface Reference

This is the **always-loaded** catalogue of tools you can call while
helping the BA design their app. The slug-based skills (under `/skills/`)
remain authoritative for **process** — what to do in each Phase — but
this file is the source of truth for **the tools themselves**.

## OpenClaw built-ins (always available)

| Tool | What it does |
|---|---|
| `read`, `write`, `edit` | File I/O on `/workspace/` |
| `exec` | Bash / Python — use for `curl`, `jq`, `python`; for long-running work pass `background: true` |
| `sessions_*` | Multi-session control (rarely needed in a build) |
| `citra_toolkit.scratch.memory` | Durable **per-tenant** memory, used **inside `exec`** (`from citra_toolkit.scratch import memory`) — survives pod reaping, unlike `/workspace/build/` files. See `MEMORY.md` for the read/write pattern. This is the **only** `citra_toolkit` submodule available in the builder; `files` / `vault` / `discovery` / `ocr` are action-chat-only and **404 here** (see `IDENTITY.md`). |

`web_search` / `web_fetch` / `browser` / `fetch` / `http` / `cron` /
`process` / `apply_patch` / `code_execution` / `canvas` / `message`
are **not callable** in this pod — either denied in config or never a
real tool. Internet goes through the `citra_web_search` /
`citra_web_fetch` MCP tools below; the execution tool is `exec` (there
is no separate `code_execution`), and background work is `exec` with
`background: true`.

## Native MCP tools — enterprise builder surface

These are registered with OpenClaw at boot via the `citra` MCP server
([citra-mcp-service](http://citra-mcp-service:9090/mcp), URL injected as
`CITRA_MCP_URL`). The MCP server validates the builder's scope claim
(`smart-app-builder`) and **only exposes the enterprise / utility tools**
listed here. **User-personal tools** (vault search, file list/get/put,
OCR over user uploads, DuckDB over user files, image generation) are
intentionally NOT injected into the builder — the builder is for
designing enterprise software, not for poking at the BA's personal
data. Those tools live only on action-chat sandboxes.

### Discovery — the most-used pair during a build

| Tool | When to use during a build |
|---|---|
| `citra_discovery_search` | **Phase 1 (Internship)** and any time the BA mentions a new domain. `{query, top_k?, tag?, data_type?}` → ranked top-k dept-MCPs with `relevance_score`. Be specific in `query` ("claims processing data sources" beats "data"); discovery-service embeds the query and cosine-ranks every claim-visible source. **This replaces the old `curl /tools/available + read all + scan` pattern** — you get 10 ranked candidates with descriptions instead of 100 to read. |
| `citra_discovery_query` | **Phase 1–2 validation.** `{tool_name, args, timeout_seconds?}` → executes a query against a discovered source's `query_endpoint`. Use when you need to sample what a dept-MCP actually returns before committing it to `data_sources` in the AppSpec (e.g. confirm "claims_staging" has the columns the BA's app needs). Forwards the caller's JWT so RLS is preserved. |

### Web access — research during design

| Tool | When to use |
|---|---|
| `citra_web_search` | Public-web search via Serper. `{query, top_k?, freshness?}` → `{results: [{title, url, snippet, source}]}`. Use when the BA references an external regulation / standard / framework you need to look up (GDPR / HIPAA / a specific govt schema name) to design the app correctly. **Sparingly** — the catalogue is the authoritative source for design. |
| `citra_web_fetch` | HTTP GET with SSRF guard. `{url, max_bytes?}` → `{url, status, content_type, text \| body_b64, bytes}`. Read a page returned by `citra_web_search` or a URL the BA pasted. Decodes gzip/deflate. |

### Utilities

| Tool | When to use |
|---|---|
| `citra_embed` | Embed text(s) to vectors. `{texts: [str], model?}` → `{embeddings: [[float]], model, dim}`. Rare — most semantic work goes through `citra_discovery_search` (which already embeds your query server-side). |
| `citra_rerank` | Cross-encoder rerank of a candidate list. `{query, chunks: [{id, text, score?, metadata?}], top_k}` → `{reranked_chunks: [...]}`. Use after merging candidates from multiple sources. `citra_discovery_search` already reranks server-side. |

### Build QA — validate the spec, review the rendered app

These two close the build → publish → 422 loop and the "publishes but looks broken" gap. Both are **present and callable**; use them as described, but neither is a hard blocker on its own backend being down (see notes).

| Tool | When to use |
|---|---|
| `citra_spec_validate` | **Phase 3.5 — after composing the AppSpec, BEFORE `/publish`.** `{app_spec (object, required), agent_spec (object, optional)}` → `{passed:true}` or `{passed:false, errors:<detail>}`. Runs the **identical** JSON-Schema + Pydantic two-layer check `/publish` runs, without persisting — so it catches the cross-reference errors JSON Schema alone can't (dangling `navigate.page`, duplicate `is_row_click`, sub-agent tool-subset violation, dashboard page missing `agent_id`). A `passed:true` here means publish won't reject on spec-shape grounds. **Always run it before `/publish`.** Fix any `errors` and re-validate (≤3 attempts per distinct error). **Fallback (if `citra_spec_validate` is NOT in your function list — MCP gateway unreachable):** it just proxies to smart-app-service, which you can reach directly. Via `exec`: `curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/validate" -H "Authorization: Bearer $CITRA_JWT" -H "Content-Type: application/json" -d '{"app_spec":…,"agent_spec":…}'` — **HTTP 200 = passed; 422 body = the same `errors`.** Use this rather than looping on the missing tool or skipping straight to `/publish`. |
| `citra_visual_review` | **Phase 4 — after the data smoke gate passes, before sharing the URL (OPTIONAL but recommended).** `{url, context?, full_page?}` → `{passed, overall_ok, issues:[{severity, area, description, likely_fix}]}`. Renders the page in the headless browser and has the vision model critique what a user would see (blank charts, overlapping labels, "—" KPIs, error text). A `fail` issue → fix via `likely_fix`, re-publish, re-run (≤3 per distinct issue). **If the render backend is unreachable (the tool errors), do NOT loop or block** — note it to the BA and share the URL; the data smoke gate already passed. |

## Routing intuition for a build

- **BA mentions a domain** → `citra_discovery_search(query=<domain phrase>)` → pick top-1–3 sources → `citra_discovery_query` to sample.
- **BA references an external standard** (regulation, schema, framework) → `citra_web_search` → `citra_web_fetch` on the top hit.
- **BA asks "what data exists for X?"** → `citra_discovery_search` first, never start from scratch.

## What you DON'T have, and why

The following tools exist on action-chat sandboxes but are **deliberately
not exposed** to the smart-app builder:

- `citra_vault_search` / `citra_files_list` / `citra_files_get_url` / `citra_files_get_bytes` / `citra_files_put` — user-personal storage. The BA's vault and uploads aren't relevant to designing the enterprise app schema.
- `citra_ocr` — operates on user-uploaded images via the chat user-data backend.
- `citra_sql_duckdb` — operates on session-uploaded file bytes (a chat upload pattern).
- `citra_image_generate` — generates end-user content; not used in spec authoring.

If a build genuinely requires reading a BA-supplied sample file (e.g.
sample CSV to confirm column names), use OpenClaw's built-in `read` on
files placed into `/workspace/build/` by the spawn payload, or have the
BA register the file as a dept-MCP catalogue dataset first.

## How this loads

OpenClaw bootstraps the `citra` MCP server at startup (URL =
`$CITRA_MCP_URL`) and merges its tools into the function catalogue.
The MCP server filters the tools list by the JWT scope (`smart-app-builder`)
so only the enterprise/utility/build-QA tools above appear in your function
list alongside the OpenClaw built-ins. The connection re-establishes
on container restart; no caching to worry about.

The legacy `citra-mcp-discover` skill at `/skills/citra-mcp-discover/`
documents the **bash + curl** way of doing the same discovery — keep
it as a fallback for when the MCP server is unreachable, but prefer
`citra_discovery_search` in the normal path: it ranks by relevance,
the curl path returns the whole catalogue.
