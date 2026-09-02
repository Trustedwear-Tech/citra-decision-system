<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## How it is built

Five moving parts. Each one exists because the next one needs it, so it reads
as a single chain:

> You describe your systems once in **`sources.json`** (§1). The MCP that
> serves them **registers** itself on boot (§2), so `data-discovery-service`
> can crawl it into a **catalogue** (§3). The **builder** interviews you in
> chat and can only offer what that catalogue contains -- it drafts the app,
> tests itself against synthetic cases, and publishes (§4). You then use the
> app: it recommends, you approve or correct, and every correction is
> consolidated into **memory** (§5) that the next recommendation reads. The
> loop closes -- the ontology bounds what may be built, and your corrections
> decide what gets better.

`ARCHITECTURE.md` has the full service map; this is the decision path only.
The concepts above are the "what"; this is the "where in the code".

### 1. The ontology -- one `sources.json` per deployment

Concept above; this is where it lives. Each department gets its own MCP
container, built from `source-mcp-template`, with its `sources.json` mounted
read-only.

Beyond what the concept section covers, the file is also where two definitions
that the rest of the system reasons over are pinned:

- `decision_history` -- marks which dataset *is* the record of completed
  decisions, and which of its values count as good, bad or neutral outcomes.
  This is what feeds the learning loop.
- `value_semantics` -- the money definition every ROI figure is computed from:
  whether this workflow *recovers*, *prevents*, *sanctions* or *settles*
  value, which column carries the exposure, and how a realised amount is
  matched back. Because it lives in `sources.json`, a pilot's metric
  definitions are frozen by a git commit on day zero rather than argued about
  at the end.

On strictness: source, dataset and column blocks reject unknown keys, for the
reason given above. `connection` deliberately does not -- it is genuinely
polymorphic (SAP's `sysnr`/`client`, BigQuery's `project_id`) and a wrong key
there fails loudly at connect time anyway.

Model definitions: `source-mcp-template/registry_models.py` (the input
contract, and the authoritative one) and `source-mcp-template/models.py`
(what the MCP serves back).

### 2. Registration -- the MCP announces itself to `discovery-service`

On boot the MCP POSTs one entry per source to `discovery-service`
`/tools/register`, heartbeats every 60s, and deregisters on shutdown. Each
entry carries the tool id, the org and departments it serves, the endpoints to
reach it, and the visibility block copied straight from the ontology. The
registry is Mongo-persisted, so a restart does not lose it.

`GET /tools/available` is answered against the caller's JWT -- `org_id`,
`dept_ids`, roles -- so discovery is itself access-controlled: you cannot see a
tool you are not entitled to. Semantic (RAG) sources advertise an **empty**
query endpoint, because they are answered platform-side by the reader rather
than by the MCP; a naive consumer therefore cannot route a RAG read to an
address that would 404.

### 3. The catalogue -- `data-discovery-service` crawls what was registered

The builder cannot offer a dataset it has never seen. This service walks the
registered MCPs and, for each one: `GET /datasets`, then per dataset
`GET /datasets/{id}` and `/datasets/{id}/sample`; runs a rule-based PII and
semantic-type classifier over the sample; and computes a schema fingerprint
used to detect when a physical schema has actually changed. The result is
upserted into `data_catalogue`, keyed `(tenant_id, source_id, dataset_id)`. A
reconcile pass then deletes rows the registry no longer backs, so a retired
source stops being offered.

**The crawl calls no LLM, and never renames a column** -- names are carried
verbatim from the source, because a helpfully renamed column breaks the query
that must run against the real one. LLM-drafted *descriptions* exist, but only
behind an explicit two-step curator flow: `POST /catalogue/{id}/draft-descriptions`
returns proposals and writes nothing, and a human applies them with
`PUT /catalogue/{id}/descriptions`. Samples are redacted before any such call.

Datasets are addressed verbatim as `<source_id>.<table>`. That exact string is
what an app's `data_source.ref` must equal -- publishing rejects a ref that
resolves to nothing rather than shipping an app whose panels are silently
empty.

### 4. The builder -- one spec, three surfaces

`smart-app-service` turns a plain-English description into an app spec, using
the catalogue as its dataset palette and the ontology as the envelope of what
may be built.

**It is a conversation, not a form.** A builder pod runs an agent that
interviews you the way a business analyst would -- it asks at most three
clarifying questions up front, and proposes rather than interrogates. Its
competence is packaged as **20 skills** in `smart-app-service/skills/`, each a
`SKILL.md` the agent loads when that part of the job comes up: discovering
sources (`citra-mcp-discover`), drafting the agent's brain
(`citra-agent-spec`), designing panels and charts (`citra-ui-panels`,
`citra-ui-charts`), the safety rules it may not break (`citra-safety-rules`),
testing itself (`citra-self-test`), publishing (`citra-app-publish`).

The build runs in phases, and two of them are gates rather than steps:

| Phase | What happens |
|---|---|
| **0 — Understand** | The agent reads the runtime it is authoring for *before* composing. It writes specs for a renderer, executor and validators that already exist. |
| **1 — Internship** | Discovery over the catalogue from §3. **If the catalogue is empty the build stops here** and says so -- it will not invent a dataset to keep going. |
| **1.5 — Grounding** | Optional; only when the goal is repetitive decisions. |
| **2 — Expertise** | Authors the AgentSpec -- tools, grounding, policy gate, outcome -- then **self-tests it**. |
| **3 / 3.5 — UI** | Page list agreed first, then one page at a time, each confirmed before it is composed. Skipped entirely for a headless build. |
| **3.6 — Backstop** | Optional second opinion from a runtime-verifier sub-agent. |
| **4 — Deploy** | Publish. |

**The test step is real and it blocks.** `citra-self-test` runs synthetic cases
against the drafted agent and scores them *before* publishing, so prompt gaps,
missing tools and ambiguous instructions surface while they are still cheap to
fix. For any app with an action tool -- a write, a queue decision -- a green
run is **required to publish**. The single exception is a pure read-only
dashboard, which has no agent decisions to exercise. A static-check pass runs
again after the app spec is composed, because six of its checks read a spec
that does not yet exist at self-test time.

You are not locked in afterwards: `POST /apps/{slug}/edit` reopens the build,
and apps live in a **test environment first** -- `POST /apps/{slug}/promote-to-prod`
is what moves one to production, so you exercise it against real data before
anyone depends on it.

Publishing validates the whole spec before anything goes live:
refs must resolve, and a `case_signature` facet that reads a column no panel
projects is rejected, because that facet would resolve to `__unknown` on every
case and every rule scoped to it would be dead on arrival.

One published spec is served three ways:

- **Decision App** -- `citra-app-runtime` renders the case-working pages,
  dashboards and copilot inside the Citra UI shell.
- **Decision API** -- `GET /apps/{slug}/decision-contract` returns the app's
  own request/response schema, the endpoints to call, and the auth and
  governance rules, so any system can drive it headlessly:
  `POST /apps/{slug}/run` for a grounded recommendation, then
  `POST /apps/{slug}/run/{correlation_id}/approve` for the schema-validated
  commit.
- **Embeddable UI** -- `GET /apps/{slug}/embed/snippet` yields a script tag;
  the host page calls `Citra.init({ getToken })` then
  `citra.mount(selector, { embed, recordId, onDecision })`.
  The card renders in a shadow root, so the host's CSS and the card's cannot
  reach each other. `bank-demo/` is a complete worked integration.

### 5. The memory -- what makes the next recommendation better

Every correction an officer makes is recorded. A background job
(`consolidation.py`, leader-elected and off the officer's request path) does
three things with them, and only one of them writes text:

- **REINFORCE** -- the correction matches an existing clause: provenance and
  counters only, no LLM call, no text change.
- **CREATE** -- a cluster of roughly three related corrections matches nothing:
  one LLM call, once, ever.
- **MERGE** -- two clauses are near-duplicates: keep the more general.

It consolidates; it does not summarise. A clause's text is written once at
birth and never rewritten, so the Nth correction cannot degrade the (N-1)th
lesson. Clustering is lexical rather than embedding-based on purpose -- the
hard partition by reason code plus a facet-overlap requirement already
separates lessons, and a synchronous embedding call would add a network
failure mode inside the batch for a marginal gain.

At run time, `select_clauses` finds every clause whose `scope_facets` is a
**subset** of the case's facets (a Mongo `$setIsSubset` residual on a multikey
index; globally-scoped clauses always match), then sorts by specificity first
and score second. That sort *is* the backoff: a thin `(theft ∧ photo ∧ us ∧
>25k)` cell falls through to `(theft ∧ photo)`, then `(theft)`, with no
special-casing and no cold-start cliff. Dedupe keeps the most specific
survivor, and the block is filled to an injection budget. If the store fails,
it logs loudly and returns an empty block so the run still proceeds --
learning degrading must never take a decision down with it.

**Managing the memory** is a first-class surface, not a database chore. Status
governs how a clause is used:

| Status | Behaviour |
|---|---|
| `active` | corroborated team judgement -- asserted and cited |
| `candidate` | one officer's judgement, injected but **labelled** as uncorroborated (capped at 3 per case) |
| `dissented` | officers acted against it often enough that it is rendered as a disagreement notice, never as a rule |
| `sop_conflict` | contradicts the written SOP; surfaced for adjudication, not injected |
| `quarantined` | suspended by an admin -- e.g. taught by someone who has since left |
| `orphaned` | scoped to a facet family the app no longer emits, so it can never fire |
| `retired` / `superseded` | withdrawn |

A lone officer's experience is used immediately and labelled rather than
hidden, so a one-officer branch office still learns -- but promotion to
`active` counts **distinct** officers, which is what stops one prolific
reviewer quietly authoring the app's policy. Dissent is stored, never silently
resolved. Every clause can be inspected (`/apps/{slug}/memory/clauses`), traced
to the corrections and officers that formed it
(`/memory/clauses/{id}/provenance`), listed by officer, retired, quarantined,
resolved against the SOP, or exported wholesale -- the ledger is yours, in your
database, in a schema you can read.

---

## The services, and which one is which

Twelve application containers plus the data stores. You open exactly one of
them; the rest are called on your behalf.

| | Service | Host port | What it is |
|---|---|---|---|
| **UI** | **Citra-UI** | **8081** | The page you actually open. Expo / React Native web shell -- sign-in, chat, documents, the app builder and the published-app lists. |
| **UI** | citra-app-runtime | 3100 | Next.js renderer for a published Decision App. Runs *inside* the shell -- never opened directly. |
| **API** | **smart-app-service** | **9100** | The engine, and the API you integrate against: authors apps, runs the agent loop, records decisions, serves the embed. |
| | Citra-User-Service | 7004 | Auth, orgs, departments, users. Issues the JWT every other service verifies. |
| | Citra-Service | 8085 | Chat, documents, the SOP library, the RAG reader. |
| | discovery-service | **9010** → 9000 | Registry of running MCPs; each self-registers on boot. |
| | data-discovery-service | 8095 | Crawls registered MCPs into the catalogue the builder picks datasets from. |
| | citra-mcp-service | 9090 | Sandbox toolbelt (web, files, OCR) for builder pods. |
| | action-sandbox-host | 7090 | Spawns the builder pod that authors an app. |
| | duckdb-query-service | 7301 | Analytics over structured files. |
| | reranker-service | 7302 | Retrieval reranking. |
| | playwright-render-service | 3001 | Headless render. |

Plus one **dept-MCP per tenant** (18504+), which is the only thing that touches
your systems of record. The data stores -- Mongo, Milvus, MinIO, Redis ×2, and
the demo's Postgres -- are not published to your machine at all, except MinIO's
console on 9001.

**The request path.** Your browser talks to Citra-UI on 8081; it gets a token
from Citra-User-Service and calls smart-app-service for everything about an
app. A Decision App's pages are rendered by citra-app-runtime and framed by the
shell, which is why *"the shell loads but the app area is blank"* localises to
the runtime rather than the shell. The agent reaches your data only through the
tenant's dept-MCP -- no service holds your connection strings.

> **discovery-service is the one service whose host port is not its container
> port.** Sibling containers call `discovery-service:9000`; from your machine it
> is **9010**. This is worth getting right because it does not fail loudly: a
> different Citra stack answers on host 9000 with the same
> `{"status":"ok","tool_count":N}` shape, so the wrong port can report a
> healthy pass against a stack that is not yours.

`ARCHITECTURE.md` has the same map with the shared packages and conventions.
