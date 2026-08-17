---
name: citra-app-spec
description: Author and validate AppSpec JSON for Citra Power AI Apps
metadata:
  category: citra
  tools: [bash]
---
<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Citra App Spec

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift. Don't trust a remembered rule over the
> runtime you can read.


## Purpose
Author the **AppSpec** that declares the user-facing surface of a Citra Power AI App: panels, data sources, theme, permissions. The runtime (`citra-app-runtime`) renders this JSON — you do **not** write React.

## Headless (agent-only / decision-API) mode
When the BA wants **only the decision engine** — to plug it into THEIR own / a developer-crafted UI (common for internal ops with an existing console), not the Citra SmartApp UI — build **headless**:

- Author the **AgentSpec fully** (`citra-agent-spec`: tools, grounding, policy gate, outcome) — the brain is unchanged.
- Author a **stub AppSpec**: set `"headless": true` and **omit `panels` and `pages`** (the headless flag is the ONLY thing that lets an AppSpec validate with no UI). Keep slug/title/owner/audience.
- **Skip the UI phases** — do NOT run `citra-app-ui-design` / `citra-ui-panels`; there is no UI to design.
- Governance + the self-learning loop are **identical** — the app still runs through `/run` + `/approve` (DecisionRecord, policy gate, outcome poller, grounding all apply).
- **Hand-off:** give the BA/developer the **decision API** — `POST /apps/{slug}/run` then `POST /apps/{slug}/approve/{correlation_id}` — and point them at `GET /apps/{slug}/decision-contract` (request schema + response shape + auth). Do NOT hand over a SmartApp UI URL (there is none); the app shows in the apps list as a card badged "API · headless" with a copyable API URL.
- **The one rule for the external UI:** it MUST call `/run` + `/approve` — it must NEVER write the system of record directly, or it bypasses the governance + learning boundary.

Trigger when the BA says "just the agent / API", "I'll build my own UI", "plug into our console/portal", or "I don't want the Citra UI." Otherwise build the normal UI-backed app.

## When to Use
- Phase 3.5 (Compose) of a Power AI App build — runs **after** `citra-app-ui-design` has frozen `/workspace/build/ui_design.md`.
- When the BA asks to add/remove/reorder panels in an existing app (still re-runs `citra-app-ui-design` first to capture the diff).

## Detailed references — load on demand
This SKILL.md is the index + core rules. Read the matching reference file **only when the build needs it** (keeps context lean):

| If you are… | Read |
|---|---|
| Needing a full multi-page / single-page **template** | `references/examples.md` |
| Deciding the **chart** shape / placement (auto-inject) | `references/charts.md` |
| Authoring a **form** panel or any **file** upload/view/download | `references/forms-and-files.md` |
| Wiring a **detail** drill-down or a **document** panel (incl. RAG) | `references/panels-detail.md` |
| Building a **write** path — approval queue, direct write, or **triggers** | `references/writes-triggers.md` |
| Making the app **learn from officer corrections** (facets + reason codes) | `references/case-signature.md` |
| The SOP carries a **scoring / assessment framework** (scorecard, rating, criteria checklist) | `references/factor-set.md` |

(Reference paths above are **relative to this skill's own folder** — read them with the file tool. The full UI catalogues live in `citra-ui-panels` / `citra-ui-fields` / `citra-ui-charts`; the canonical safety rules in `citra-safety-rules`.)

## Hard Rules
- The AppSpec is **JSON only**. Never produce a React component, HTML page, or freeform code as a substitute.
- **Validate against `app_spec.schema.json` (seeded at `/workspace/.openclaw/workspace/schemas/app_spec.schema.json`) BEFORE saving — this is the single biggest publish‑friction killer.** The schema sets `additionalProperties:false` on nearly every object, so the publish gate 422s on *any* invented/extra field; validating locally catches them up front. Do **not** "validate at publish time."
- **All `id` fields are lowercase snake_case `^[a-z][a-z0-9_]*$`** (data_source `id`, page `id`, panel `id`, trigger `id`/`action`) — human display text goes in `title`/`label`, never the `id`.
- **Dashboard pages allow ONLY `chart`, `dashboard` (KPI), `markdown` panels** (no `queue`/`form`/`notice`/`detail` — those go on a standard page; the copilot is the automatic hero‑brief band, not a panel). See `citra-dashboard-spec`.
- One panel = one purpose. Don't pack two ideas into one panel.
- **Layout source of truth = `ui_design.md`.** Don't invent pages, layouts, or navigate targets here. If `ui_design.md` is missing or has no `## Frozen` section, stop and tell the user to run `citra-app-ui-design` first.
- `pages[]` is the default for non-trivial apps; `panels[]` (single-page shorthand) is allowed only when `ui_design.md` explicitly froze a single-page design.
- **At least ONE panel.** Either top-level `panels[]` must have ≥1 entry, OR `pages[]` must have ≥1 page whose `panels[]` has ≥1 entry. The publish validator rejects empty-on-both-sides specs.
- `slug` must be lowercase-hyphen, ≤63 chars.
- Every `panel.data_source` must reference an entry in `data_sources[]`.
- `navigate.page` on every form on_submit and queue action must reference a `pages[].id` you actually declared. The Pydantic validator rejects dangling references — fail locally first.
- **Smart Apps are INTERNAL operations tools — never public-facing.** A Smart App is used by the organisation's own staff — officers, analysts, approvers. Citizen / public intake and filing belong on a **separate public portal** and are out of scope. A `form` panel is therefore ONLY for an officer entering or correcting a record internally. **Never** author a citizen-facing "file a complaint / submit an application" form, and never place a public-intake form alongside an internal triage/review queue. If the BA's goal includes public submission, surface it in `requirements_unmet` and design only the officer-facing surface (queues, review, dashboards, chat).
- **MCP `data_source` refs must be dot-qualified.** A `type:"mcp"` data_source's `ref` must be `<source_id>.<table>` (e.g. `urban_grievances.grievances`) — never a bare `source_id`; the publish validator rejects unresolvable bare refs. Use `type:"rag"` for a semantic/document source (its `ref` is the bare source_id). Each `mcp` data_source resolves to ONE dataset — if a page needs two tables, declare two data_sources.
- **The `ref` is the catalogue entry's `ref` field, copied VERBATIM.** Every `/builder/catalogue` entry carries a ready-to-use **`ref`** field (identical to its `id` / `dataset_id`, already source-qualified as `<source_id>.<table>`, e.g. `field_operations.complaints`). Copy that string **exactly** into `data_source.ref`. **Do NOT build the ref yourself by combining `source_id` with `id`** — the entry also exposes a separate `source_id` (`field_operations`), but stitching them (`source_id` + `/` + `id`) yields `field_operations/field_operations.complaints`, which resolves to a bogus source, renders every panel empty ("UI but no data"), and is now rejected at publish (`E_MALFORMED_DATASET_REF`). ✅ `ref: "field_operations.complaints"` (the entry's `ref`)  ❌ `ref: "field_operations/field_operations.complaints"`  ❌ `ref: "field_operations/complaints"`.
- **ROI / "money saved" pages bind to `type:"decision_ledger"` — NEVER recompute money from raw source tables.** The platform stamps each settled decision's monetary value (`value_amount`, `value_kind`, `currency`) on the decision ledger using the ontology's frozen `value_semantics` definition. A `decision_ledger` data source (set `ref` to the app's own `slug`; the runtime always scopes to this app's ledger) exposes those stamped rows: `decision_id`, `case`, `mode`, `overridden`, `decided_at`, `outcome_label`, `value_amount`, `value_kind`, `currency`, `definition_version`. Author KPI tiles / charts / queues over THOSE columns. ❌ Never author a "recovered amount" tile as a SUM over a raw payments table — that invents a second, conflicting money definition; the ledger is the single canonical one. If no `value_semantics` is declared in the ontology, the ledger has no `value_amount` — surface the ROI ask in `requirements_unmet` instead of improvising.
- **Keep platform-impact (ROI) pages SEPARATE from operational pages.** They answer different questions with different numbers: an operational dashboard shows how the BUSINESS is doing (whole-table SoR aggregates — all collections, all pending cases — bound to `mcp` sources); an ROI page shows what deciding through the platform has earned (attributed, windowed, definition-versioned money — bound to `decision_ledger`). Both are authored by the business in this builder, but never on the same page: ❌ a `decision_ledger` money tile beside an SoR revenue tile — the attributed number reads as a business total and both lose credibility. Give the ROI view its own clearly-titled page (e.g. "Recovery ROI — decisions made here") and keep every operational KPI bound to SoR sources.
- **Set `theme.locale` + `theme.currency` for correct money/number/date formatting.** The runtime localises every KPI tile, chart axis, and date label from these (via `Intl`): currency symbol + grouping (₹2.6 Cr vs $2.6M), number grouping, and date order. **Infer them — don't leave money formatted as ₹ for a non-Indian tenant:**
  - **Currency:** from the data — a catalogue column like `amount_inr`/`amount_paid` with ₹ values, or the tenant/region in the BA's goal. Map to ISO-4217: `INR`, `USD`, `EUR`, `GBP`, `AED`…
  - **Locale:** from the tenant region + the BA's language. BCP-47: `en-IN` (India), `en-US`, `en-GB`, `en-AE`…
  - Default to `en-IN` / `INR` only when there's genuinely no signal (the platform's first market). Example: `"theme": {"locale": "en-US", "currency": "USD"}` for a US tenant → tiles render `$2.6M`, dates `Mar 2`.
- **Theme v2 design tokens (all optional, CLOSED enums — an unknown value is rejected at publish).** `theme` also accepts: `font` (`inter|source-sans|ibm-plex|system`), `radius` (`sharp|soft|round`), `density` (`comfortable|compact` — compact for data-dense analyst apps), `surface` (`flat|elevated|glass`), `mode` (`light|dark|auto` — **dark is BETA**: verify in the render preview before publishing a dark app), `chart_palette` (`calm|vivid|mono|brand` — brand derives a ramp from `primary`). Unset = the classic look; pick tokens to match the app's tone (calm ops vs bold exec), not for novelty.
- **`theme.company_name` is INHERITED — don't invent it.** When the ontology declares an `organization` block (sources.json), publish automatically defaults `company_name`, `logo_url` and `primary` from it, and the runtime header renders "Company · App". Leave these unset unless the BA explicitly wants an override; NEVER guess a company name the ontology doesn't declare.

## Be data-size aware (don't author a source-hammering or overflowing panel)
Use each dataset's `row_count` from `/builder/catalogue`. On a large table (roughly **≥100K rows**):
- Give a `queue` a `query` filter — don't bind a raw unfiltered list (the runtime caps it at 500 and shows an arbitrary slice).
- Give a time-series `chart` a `time_grain` so the buckets stay legible.
- **Never `group_by` / chart `x` a high-cardinality or id column** — charts cap at ~100 buckets and truncate the rest to an arbitrary top-N. Bin it (time grain / category roll-up / range) instead.
- If a genuinely useful view needs an unbounded scan the source can't serve cheaply, surface it in `requirements_unmet` rather than author it.

## How the data plane behaves (design within it — you author the spec, not the query)
You never write SQL or NL queries. You declare a panel's `query` filter + `aggregation` + `x`/`y`/`group_by`/`time_grain`; the runtime turns that into a deterministic query and the dept-MCP answers the copilot's NL. Both apply these guarantees — rely on them, don't try to reproduce them:

- **Aggregates are exact and computed at the source.** A KPI tile or `aggregation` chart runs a real `COUNT`/`SUM`/`AVG`/`GROUP BY` over the *whole* filtered table — never a client-side count of a fetched page. So a tile/chart number is always the true total; never pre-compute one yourself or split a metric across pages.
- **Charts cap at ~100 buckets** — time-series keeps the most **recent** N, categorical keeps the **top** N by value, and the panel labels itself when it truncated. So bin big dimensions (time grain / category roll-up / range) and never `group_by` a high-cardinality / id column.
- **Row lists** (`queue`, `calendar`, `map`) **cap at 500 rows**, projected to the columns you declare. A list bound to a large table without a `query` filter shows an arbitrary slice — always filter it.
- **The copilot answers ad-hoc questions correctly at the source.** "How many / total / breakdown by X" come back as true aggregates; a broad "show all …" returns the **count**, never a misleading sample. So you can lean on the copilot for ad-hoc numbers instead of trying to bake every possible figure into a tile — design tiles for the headline KPIs, let the copilot handle the long tail.
- **Empty or failed data is surfaced, not hidden.** A failed query renders an error (not a fake `0`), and an empty source renders an empty panel (caught earlier by the preflight probe). Don't design defensive fallbacks for "what if the source is down" — it fails loud.

## Bind to COLUMNS that exist — never reference a column the dataset doesn't have
A queue column, a detail field, a chart `x`/`y`/`group_by`, a `query` filter — every column you name must be in that dataset's `columns[]` from the catalogue. `field_operations.theft_cases` has `consumer_id`, **not** `consumer_name` (the name lives in `billing.consumers`) — referencing `consumer_name` on the theft_cases queue is rejected at publish (`E_UNKNOWN_COLUMN`). A chart's `x`/`group_by` must be a column **in that chart's own `data_source`** — you cannot `group_by` a column from a different source (no cross-source columns in one panel). Cross-reference `columns[]` before you author; if you need a field that lives in another dataset, that's a join the source doesn't expose — surface it, don't invent the column.

## Never invent a rubric — weights, bands and grades come from the SOP
If the BA's policy document carries a scoring framework, you **execute theirs**: extract the factors, weights and bands, get a human to **confirm** them, and declare them in `factor_set`. You never author one. A hallucinated weight yields a scorecard that looks authoritative and is wrong — worse than having none, and the fastest way to lose a credit audience. No rubric in the SOP ⇒ **no `factor_set`** (say so; this is the empty-catalogue rule applied to scoring). Criteria with no weights and none implied ⇒ `mode:"checklist"` — judged rows with **no total**, and do not ask for weights that do not exist. `mode` is **permanent** for a published app (FS-02). Read `references/factor-set.md` before authoring one.

## Bind to values that exist — never invent statuses / categories / options
A status flow, a `query` filter on a status/category column, a select/typeahead's options, and a write-action's status option **must use only the values actually present in the data** — the column's `distinct_values` from the catalogue (or a `SELECT DISTINCT` probe), captured in Phase 1 per `citra-mcp-discover`. **Never derive option/status values from the field name or domain intuition.** Authoring `pending → partial → recovered` when the source only holds `pending / under_recovery / recovered / disputed` makes the write action send an unknown enum the source **rejects (write exception)**, and a filter on a guessed value **matches zero rows**. If the BA wants a value the data doesn't have, it's a source change (flag in `requirements_unmet`), not something you fabricate into the spec.

## DO NOT author server-stamped fields

The Pydantic `AppSpec` model in `smart-app-service/models.py` has fields the **server populates at publish time**. The JSON Schema (`app_spec.schema.json`) deliberately omits them — the schema is the builder-facing contract, and these fields are not yours to write. **Never put any of these keys in `app_spec.json`**:

| Key | Owner / source |
|---|---|
| `author_user_id`, `author_email`, `author_at` | Server stamps from the publisher's JWT on first publish (audit, immutable) |
| `owner_type`, `owner_id`, `owner_changed_at`, `owner_changed_by`, `previous_owners` | Server / admin manages ownership transitions |
| `org_id`, `dept_ids`, `tenant_id` | Server derives from the publisher's JWT + SA membership |
| `visibility` | Server defaults (admin tunes via separate API) |
| `lifecycle_stage` | Server-managed state machine (`draft` → `team_managed` → `archived`) |
| `inheritance_policy`, `inheritance_target`, `inheritance_grace_days` | Server defaults; admin overrides per-app |
| `dataset_directory` | Server hydrates from discovery-service at publish time |
| `app_id` | Server allocates on first publish; re-publish carries it forward |
| `version`, `deployed_at`, `status` | Server bumps `version` per publish; sets `deployed_at`/`status` automatically |

If your draft includes any of these the publish endpoint will reject it as `Spec validation failed: Additional properties are not allowed (...)`. There is a defensive strip on the server side, but **author cleanly** — only emit the keys listed in `app_spec.schema.json` under `properties` (you can `cat /workspace/.openclaw/workspace/schemas/app_spec.schema.json | python -c "import json,sys; print(list(json.load(sys.stdin)['properties'].keys()))"` to see them).

## Allowed panel types (v0)

> **Full catalogue: `citra-ui-panels`** (panel types + detail sections),
> **`citra-ui-fields`** (form controls), **`citra-ui-charts`** (chart types +
> KPI metrics). Those skills are the authoritative, up-to-date source of truth;
> the table below is a quick reference. The runtime **fails loud** on any panel
> type not in the catalogue.

| `type` | Use for |
|---|---|
| `form` | **Officer-facing internal** data entry — an officer records or corrects an instance (binds to `agent.input_schema` or `schema_inline`). **Never** a citizen/public submission form — see Hard Rules. |
| `queue` | List existing instances filtered by status. |
| `detail` | Per-record drill-down (fields, attachment, timeline, approval, documents, chat). Linked to a queue — see `references/panels-detail.md`. |
| `dashboard` | KPI cards (count, sum, avg, min, max, ratio, optional time window). |
| `chart` | Visual chart (line, bar, area, pie, funnel, scatter) over a tabular data source — see `citra-ui-charts`. |
| `agent_chat` | Free-form chat with root agent or a named sub-agent. |
| `document_view` | Browse a document library (`static` or RAG-backed) — see `references/panels-detail.md`. |
| `markdown` | Static instructional content. |
| `notice` | Static callout band (`tone`: info / warn / error / success) — an SLA caveat or "what to check" note. No data binding — see `citra-ui-panels`. |

## Auto-inject charts (core rule)
**Whenever the app makes a judgement an officer reviews (an approval queue, a
recommendation, any write behind a review gate), author a `case_signature`** —
even if the BA never asks. It is what lets the app scope what it learns from
their corrections; without it every learned rule fires on every case of that
kind. Four to eight facet families lifted from the bound dataset's own columns,
plus the reason codes officers will pick from when they reject. **Shape, the
worked example, the platform signal list and the cardinality trap:
`references/case-signature.md`.**

**PROPOSE them in chat and get the BA to accept or edit — publish rule CS-04
requires it.** Narrating them in a summary is not enough: these decide what
"cases like this one" means for every judgement the app will ever learn, and the
BA is the only person who knows how their team actually groups cases. **This is
a conversation turn, not a notification.** Propose, then STOP and wait.

**Say it in their language.** "Facet" is our word — the BA has never heard it
and it tells them nothing. What this is, to them, is **the business categories
the app files its learning under**. Never put `facet`, `case_signature`,
`family`, `scope_facets` or `__unknown` in a sentence a BA reads; say what it
DOES. (Full say/don't-say table: `references/case-signature.md`.)

> "One more thing before I publish. When one of your officers corrects this
> app, I need to know which *other* cases should get that lesson — otherwise
> it's either applied to everything or to nothing.
>
> I'd file what it learns under: **product**, **amount band**, **FOIR band**,
> **LTV band**, and **sourcing channel**. So if an officer corrects it on a
> large personal loan sourced through a DSA, that correction comes back on
> similar large personal loans — not on a small home loan from a branch.
>
> Is that how your team thinks about these? Tell me what to add or drop — if
> branch matters more than sourcing channel, say so and I'll swap it."

Note what that does and does not do. It explains the **consequence** ("comes
back on similar cases, not on a small home loan"), names the categories in the
BA's own domain words, and asks a question they can answer without knowing
anything about how the app is built. It never mentions scoping, signatures or
subsets.

Then **implement what they say — this is the part that matters.** Their reply is
a spec change, not feedback to acknowledge:

| They say | You do |
|---|---|
| "yes, that's right" | record and move on |
| "add region" | add a `region` facet — pick the column from the bound dataset, `kind: "enum"` with its real values (or `band` with edges). Do not invent a column. If no bound column carries it, SAY SO rather than faking one: "nothing in the data I'm connected to records region — I'd need that column added." |
| "drop LTV, it's not relevant" | remove that facet |
| "branch matters more than channel" | replace the facet — and say what you swapped |
| "we'd group these by dealer tier" — a category you never offered | take it. Find the column their word maps to, or derive it; name the family in THEIR word. See below. |
| "what does amount band mean?" | answer in their terms ("whether it's a small, mid or large ticket — I'd cut it at 10 lakh, 50 lakh and 1 crore"), then re-ask. A question is not an acceptance. |
| "why does this matter?" | one sentence on the consequence, not the mechanism: "it decides which other cases get a correction your officer makes." |

**The BA outranks you on how the business groups cases.** You proposed a list
from the columns you could see; they know how their team actually thinks. If
they name a category you never offered — "we'd split these by dealer tier", "it
really depends on programme type" — take it, do not defend your list. Work it
out in this order:

1. **Find the column that carries it.** Their word and the column name often
   differ — "ticket size" is `amount_requested`, "vintage" is
   `relationship_start_date`. Search the bound datasets for what their concept
   maps to before concluding it is absent.
2. **Derive it if it is not stored directly.** A band over a number, a
   `presence` over a nullable column, an `age_band` over two dates. Most
   business categories are a derivation, not a column.
3. **Say honestly when it is not there.** "Nothing in the data I'm connected to
   records dealer tier — is it held somewhere else, or is there a field that
   stands in for it?" Never fabricate a column to satisfy the request.
4. **Push back only on cardinality, and in their terms.** If their category
   resolves to something with hundreds of distinct values, it groups nothing:
   "there'd be too many different values there for it to group anything useful —
   could we band it, say under 50 lakh / 50 lakh to 2 crore / above?"

**Name the family in THEIR words.** The family name is not internal — it is
rendered verbatim on the officer's decision card (`ticket_size` shows as
"Ticket size"). If the BA says ticket size, call it `ticket_size`, not
`amount_band`. Their vocabulary should reach their own officers unchanged.

If you changed anything, **show the revised list and confirm that one** — the
list you record must be the list they last saw. Then set `confirmed_families`
to exactly the families you declare. (`confirmed_by` / `confirmed_at` are
optional provenance; who built the app is already in the audit trail, so do not
manufacture an identity.)

Publish rejects one thing: `confirmed_families` not matching the declared
families. That is the divergence nobody could see afterwards — you heard "drop
LTV" and shipped LTV anyway.

**If they never answer, do not publish.** Say the app is ready and blocked on
that one question. An unanswered question is a smaller problem than an app that
silently learns along the wrong lines.

**You MUST inject at least one `chart` panel — even if the BA never asks — whenever the data is numeric** (a queue with a numeric column; any `dashboard` panel; a time-series/transactional data source; agent actions emitting numeric rows). Default to `line` when a time field exists, else `bar`; place it right after the dashboard/queue it visualises; don't ask the BA first (mention it in the summary). **Full shape-selection table + placement rules + examples: `references/charts.md`.**

## Safety rules (citations)

- **Plan-then-apply default (authoring default, no rule-id)** — Queue actions that fire writes default to `plan_then_apply` with a confirm step. Undo a wrong write with a normal compensating write or an IT source fix.
- **S-01** — `audience` must be one of `{owner, team:*, dept:*, org}`. **Never `public`** — Smart Apps are internal-only staff tools; public-facing intake lives on a separate portal (see Hard Rules).
- **S-03** — Never paste secrets, tokens, API keys, or connection strings into spec defaults, panel config, or data_source refs. Use Vault refs (`vault://...`) only; the publish validator scrubs and rejects obvious secret shapes.

Refer to `citra-safety-rules` for the canonical rule list.

## Permissions block
Standard keys: `view`, `submit`, `approve`, `edit`. Map each to a list of role ids the BA confirms in plain language ("Who approves these?").

## Workflow
Narrate per [`AGENTS.md`](../../AGENTS.md). Phase 3.5 is mechanical translation with no BA dialogue — the narration is the BA's only visibility into what's being assembled.

1. **Narrate** the kickoff:
   ```
   > 📝 Translating your frozen design into the AppSpec JSON...
   ```
   Read `/workspace/build/ui_design.md`. Locate the most recent `## Frozen — v<N>` section. If it doesn't exist, stop — tell the user to run `citra-app-ui-design` first.
2. Read `/workspace/build/agent_spec.json` for `actions[].input_schema` (used for form panels) and `actions[].name` (used in form `on_submit.agent_action` and queue actions).
3. Translate every page in `## Frozen` into a `pages[]` entry. For each page:
   - `id`, `title`, `icon`, `hide_in_nav`, `layout` come straight from the design.
   - **`kind`** — set `kind: "dashboard"` for a page the design marked as a KPI/dashboard page (executive KPI + chart grid topped by the hero-brief copilot). Leave it unset (`"standard"`) for ordinary pages. A dashboard page may hold only `chart` / `dashboard` (KPI) / `markdown` panels and requires the app to declare `agent_id`; author it with **`citra-dashboard-spec`** rather than by hand, then keep its `pages[]` entry here alongside your standard pages. This is how a multi-page info app (e.g. a CMD briefing) combines one dashboard page with queue/document/assistant pages.
   - `panels[]` are translated panel-by-panel. Each panel's `data_source` must already exist in the app-level `data_sources[]` — collect them across all pages, deduplicate, and emit a single top-level `data_sources[]` array.
   - For form panels: `on_submit.agent_action` and `on_submit.navigate` are taken from the design. Use `{result.<field>}` templates for params populated by the action output.
   - For queue panels: each `actions[]` entry gets `label`, optionally `agent_action`, optionally `navigate`, and `is_row_click: true` when the design says "row click". `{row.<field>}` templates substitute the clicked row's data. When the `agent_action`'s `input_schema` **requires** a field the queue has **no column** for (a fixed `record_type` / category discriminator), supply it with `args` on the action — `args` is merged onto the clicked row before the action runs (`args` keys win). Without this the run fails validation with `'<field>' is a required property`.
4. Emit `navigation` at the top level using the design's chrome settings (style, default_page, show_chat_globally).
5. Auto-inject `chart` panels per the **Auto-inject charts** core rule (detail in `references/charts.md`). Place each chart on the page whose queue/dashboard it visualises. **Narrate** the injection so the BA isn't surprised:
   ```
   > 📊 Auto-injecting a volume chart on the inbox page (your queue has a numeric amount column)
   ```
6. **Narrate** before file write + validation:
   ```
   > 📝 Writing the AppSpec...
   > ✅ Spec validates against schema and Pydantic model
   ```
   Write `/workspace/build/app_spec.json`.
7. **Validate the spec — `citra_spec_validate` is the authoritative gate.**
   (Optional fast offline pre-check first — pure JSON Schema, catches obvious
   structural errors without a round-trip:)
   ```bash
   python -c "import json,jsonschema; \
     spec=json.load(open('/workspace/build/app_spec.json')); \
     schema=json.load(open('/workspace/.openclaw/workspace/schemas/app_spec.schema.json')); \
     jsonschema.Draft202012Validator(schema).validate(spec); \
     print('schema ok')"
   ```
   Then call the **`citra_spec_validate` tool** — it runs the **exact same
   JSON-Schema + Pydantic two-layer check `/publish` runs**, without persisting,
   and catches the cross-references JSON Schema alone can't (a dangling
   `navigate.page` target, a duplicate `is_row_click` column, a sub-agent
   tool-subset violation):
   ```json
   {"tool": "citra_spec_validate", "args": {"app_spec": <the AppSpec object>}}
   ```
   (Include `"agent_spec": <object>` too if the app has an agent.) `{"passed": true}`
   → publish will not reject on spec-shape grounds. `{"passed": false, "errors": …}`
   is the same failure `/publish` would return — fix the spec and re-validate
   **now**, before the publish round-trip (≤3 attempts per distinct error). This is
   the single biggest publish-friction killer: **never `/publish` a spec that hasn't
   returned `passed:true` from `citra_spec_validate`.**

   **Fallback — if `citra_spec_validate` is NOT in your function list** (the MCP
   gateway is unreachable): it only proxies to smart-app-service, which you reach
   directly. Run the identical check via `exec`:
   ```bash
   curl -sS -X POST "$SMART_APP_SERVICE_URL/builder/validate" \
     -H "Authorization: Bearer $CITRA_JWT" -H "Content-Type: application/json" \
     -d "$(jq -n --slurpfile a /workspace/build/app_spec.json \
              --slurpfile g /workspace/build/agent_spec.json \
              '{app_spec:$a[0], agent_spec:$g[0]}')" -w '\n%{http_code}\n'
   ```
   **HTTP 200 = passed; 422 body = the same `errors`.** Do this instead of looping
   on the missing tool or skipping straight to `/publish` (omit `agent_spec` if the
   app has no agent).
8. **Do not show the BA JSON.** The BA already approved the design via `citra-app-ui-design`. If something can't be expressed in the AppSpec (an agent action with no matching tool, a chart on data that isn't numeric, a navigate target that doesn't exist), surface it in `requirements_unmet` and tell the BA in one sentence what's missing.

## Output
Write a single `app_spec.json` at `/workspace/build/app_spec.json`. Do **not** commit any other UI files. The runtime renders from this JSON alone.

## Common Mistakes
- ❌ Writing React. The runtime won't load it.
- ❌ Authoring pages without `ui_design.md` having a `## Frozen` section. Run `citra-app-ui-design` first.
- ❌ Setting both top-level `panels[]` and `pages[]`. They are mutually exclusive; pick one based on what the design froze.
- ❌ `navigate.page` referencing a page id you never declared. The Pydantic validator catches this — run it locally.
- ❌ Multiple queue `actions[]` with `is_row_click: true`. Only one is allowed per queue.
- ❌ Detail pages visible in the sidebar — set `hide_in_nav: true`.
- ❌ Inlining a JSON schema in `schema_ref` (use `schema_inline`).
- ❌ Forgetting `linked_to` on a detail panel, or `id_field` when the queue's key column is not named `id` / `*_id`.
- ❌ A detail panel with no queue that navigates to it — wire a `is_row_click` `navigate` action on the linked queue (see `references/panels-detail.md`), or the detail page is unreachable.
- ❌ A `document_view` panel (or `documents` detail-section) pointed at a `data_source` that does not exist, or a long free-text form field left as a plain string instead of `format: "textarea"`.
- ❌ Putting `agent_action` strings that don't exist in `AgentSpec.actions`.
- ❌ A queue `agent_action` whose `input_schema` requires a field the queue has no column for, with no `args` to supply it — the action 422s on every click. Add `args: {"<field>": "<value>"}` to the action.
- ❌ A `type: "mcp"` data source whose `ref` isn't `"<server>.<tool>"` (dot-separated, both halves from `citra-mcp-discover` output). A slash (`dept-mcp/claims`) or an invented name never resolves — the panel renders a permanent `must reference 'server.tool'` error. The publisher rejects this.
- ❌ Emitting a `type: "mcp"` placeholder when `citra-mcp-discover` returned **no** matching MCP. There is nothing real to point at. Instead: for data the app itself produces (form submissions, agent decisions, approvals) use `type: "smart_app_records"` with `ref` = the row `kind` (`queue_item` / `decision` / `approval`) — the app then works as a self-contained store, fed by its own forms and agent. For external live data that needs a system not yet connected, omit the data source and list the need in `requirements_unmet`.
- ❌ Wiring an agent **action** (approve, reject, submit, …) as a `tool_buttons` entry. `tool_buttons` are **only** for deterministic `tools_v2` tools and the publisher rejects anything else. To surface an agent action, use a `QueueAction` with `agent_action` (queue panel) or a `DetailSection` of `type: "approval"` (detail panel) — see `references/writes-triggers.md`. Never delete the control to get past the validator; wire it the right way.
