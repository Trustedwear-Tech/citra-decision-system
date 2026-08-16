<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# App‑Owned Data Plane (Overlay) — Design

**Status:** Proposed / UNBUILT · **Date:** 2026‑06‑08 · **Owner:** Platform

> Lets a SmartApp keep its **own operational data as a document overlay on the customer's system‑of‑record (SoR)** — without relaxing the builder's catalogue‑grounding rule or diluting the recommend → approve (HITL) governance that is Citra's moat.
>
> **Scope decision:** **overlay only.** App‑owned data is **always correlated to a SoR** (anchored to a SoR record, or in service of operating SoR records). The builder does **NOT** build standalone apps — Citra is never the *sole* system‑of‑record for a business object. "Standalone DB + UI from scratch" is **out of scope** and the builder declines it.
>
> **Storage decision:** the overlay is stored as **schemaless documents in Citra's MongoDB** — no DDL, no schema to provision/migrate/promote. It rides the existing `test_`→prod collection routing, so promote stays *spec‑only*. (Postgres‑via‑MCP is reserved for the rare overlay that ever needs relational/SQL/NL→query — not the default.)

---

## 1. Why we are doing this (the case)

For an *operational* SmartApp, the overlay is the difference between a **viewer** and a **place a team actually works** — and for enterprise specifically, it's near‑essential:

- **The enterprise SoR is rigid by design.** Adding a column to corporate SAP/Salesforce means IT tickets, change‑advisory boards, weeks‑to‑months, and a system team that won't add *your* team's field. The promise of a SmartApp is the BA building their operating layer *fast*. **Without app‑owned data, every "I also want to track X" hits the IT bottleneck and the tool stalls.** The overlay removes the single biggest friction.
- **It's the line between a cockpit and a workspace.** Read + recommend + governed‑write over the SoR is a *viewer*. The moment a team operates they need their own state — status/tags the SoR doesn't model, who's working what (assignment), rationale/notes, review/sign‑off, checklists. **Those *are* the operations.** Without them the team keeps its real work in spreadsheets/email/Slack *next to* the app, and the app becomes a screen they glance at, not where they work. That caps adoption.
- **It mirrors how enterprise ops actually split.** SoR = the locked‑down, audited *corporate truth* (owned by a system team). Operational layer = *team‑specific, fast‑changing* working context. Forcing the operational layer into the SoR is politically impossible and architecturally wrong (it pollutes corporate truth with team cruft).
- **It's table‑stakes for credibility.** ServiceNow/Pega/Appian/Salesforce‑native/Retool all carry app‑owned operational data. An enterprise buyer *will* ask "where do annotations / assignments / workflow state live?" — "a spreadsheet" loses the deal.
- **It amplifies the moat instead of diluting it.** It makes recommend → approve *richer* (deliberate → assign → review → commit) and *stickier* (the team's working context now lives in Citra), while preserving no‑egress (overlay is Citra‑side, SoR untouched). The combo — *team operational layer + untouched governed SoR + recommend/approve* — is distinctive; most tools either own **all** the data or integrate **read‑only**.

**Why overlay‑only (no standalone):** keeping app‑owned data *always correlated to a SoR* keeps the positioning razor‑sharp — "we operate over **your** systems" — and avoids drifting into generic low‑code DB territory (Airtable/Retool) where Citra has no edge, plus the sole‑SoR DB‑product obligations (durability/migration/export for arbitrary business domains).

## 2. Problem

The builder is restricted to catalogue / dept‑MCP datasets — the customer's SoR. That delivers *safe operations over systems of record*. But every real operational tool needs **scaffolding the SoR doesn't model** (routing rationale, classification overrides, triage state, review/sign‑off, notes, follow‑ups). We need to support that **without** relaxing catalogue‑grounding or diluting the HITL moat — and **without** becoming a standalone‑database builder.

## 3. Core principle — *extend* the catalogue, don't bypass it

App‑owned data lives in **Citra's MongoDB as schemaless documents**, registered in the catalogue tagged `citra_app`. The builder defines the overlay's *shape* in the app spec (the fields a form writes); **there is no DDL — no tables to create, no schema to migrate or promote** — and the collection is created lazily on first write. The overlay is surfaced in the same UI components (forms, detail, queue) and governed by the `owner` tag, but accessed as **fetch‑by‑record‑key CRUD**, not the SQL/NL→query rail (an annotation layer doesn't need it).

So the catalogue‑grounding rule is **unchanged** — you add a Citra‑owned source class to the catalogue rather than allowing un‑catalogued data. The platform supplies the **substrate + boundary rules**; the BA/builder own **all overlay shape + UI**. There is **no hardcoded "collaboration/notes/comments" feature** — those are just things a builder *might* build (over‑specifying a fixed primitive set was rejected; see *Platform primitives, not features*).

**Storage = document, no schema lifecycle.** The overlay is flexible, evolving, fetched‑by‑record‑key CRUD — a document fit. MongoDB means **no schema‑promote step**: the overlay rides the existing `test_`→prod collection routing, so promote stays *spec‑only* (no DDL replay, no test/prod schema drift, no migrations when the BA edits fields). Schemaless ≠ shapeless: the app spec's field definitions enforce shape at the app layer; add an index on `(tenant, app_id, system_record_id)` + field validation. Reserve **Postgres‑via‑MCP** only for the rare overlay that genuinely needs relational/SQL/NL→query power.

## 4. The hard rule — app‑owned data is always SoR‑correlated

Every overlay must be **in service of operating a customer SoR** — one of:
- **Per‑record overlay** — fields/threads anchored to a SoR record by id (a note/status/assignment/review on *this* case), or
- **Operation‑scoped scaffolding** — data that supports working the SoR records (routing rules, reviewer config, a checklist template applied to SoR records).

**Anchor test:** *"Is this in service of operating a SoR?"* → **yes** = overlay (build it); **no** (a standalone business object with no SoR relationship) = **out of scope** → the builder declines and redirects: *"I build operational layers over your systems of record, not standalone databases. Which system holds the records you want to operate on?"*

So the choice is a single line: **no SoR anchor → not built.**

## 5. The boundary — *posture, not schema*

The platform constrains **who is the system‑of‑record and how writes are governed** — storage‑agnostic, so it holds no matter what the builder invents. Every catalogue dataset carries **`owner: customer_system | citra_app`**:

- **`customer_system`** → read + recommend + **governed write** (recommend → approve → MCP). Never owned, copied, or cached.
- **`citra_app`** → **app‑local write**: direct, audited, RBAC'd, lower‑stakes — and **always an overlay correlated to a SoR** (rule §4).

Four anti‑patterns the builder must never cross:
1. **No shadowing** — never copy SoR business fields into the overlay. Reference the SoR record by id and read golden live; an overlay doc holds only app‑owned fields.
2. **No laundering** — don't change business state in the overlay that *should* be a governed SoR write. (An overlay "reassign/route" note ≠ a SoR owner change unless that change is its own governed write.)
3. **Provenance always visible** — UI + audit always distinguish golden (from SAP/Salesforce) vs app‑owned (entered by people).
4. **Owner‑tag drives governance** — the recommend/HITL routing keys off `owner`, no special‑casing per app.

## 6. Ownership, scoping, lifecycle

- **Tenant (`org_id`) = the hard isolation wall.** Every overlay doc is tenant‑scoped; **never** cross‑tenant.
- **`app_id` = default namespace + lifecycle binding.** Each app's overlay docs/collection live under its `app_id`; app A cannot see app B's overlay by default.
- **Access/governance owner = the app's owner** (Work SA / dept / org, per the SA‑ownership model). `app_id` is the binding; the owner controls access and survives ownership migration.
- **App‑scoped by default, promotable to org/dept‑shared** when multiple apps genuinely need the same overlay (explicit promote).
- **Durable data → deliberate disposition.** Citra holds the only copy of the *overlay* data, so app archive/delete must prompt for disposition (retain / export / explicit‑delete) — never silently destroy it. (The golden SoR is unaffected — Citra never held it.)

## 6a. Storage & scaling — reuse `smart_app_records`, scale by shape not by splitting

**Reuse the existing `smart_app_records` collection — do NOT create a per‑app `test_<app>_extension` collection.** It already is the schemaless overlay store: a **shared** collection **scoped by `app_id`**, tagged by `kind` (the BA‑defined overlay type), with a schemaless `data` blob and a `record_id` that anchors a row to its SoR record. It is **env‑routed** by the existing `_route_col` (`test_smart_app_records` ⇄ `smart_app_records`) and already has a `data_source` type for reads. Overlay rows go in scoped by `app_id`/`kind`; the env routing gives the test/prod split for free.

**Why a single shared collection scales (millions of rows is fine):**
- **Per‑customer deployment bounds it.** Each customer runs their own deployment, so the collection holds *one customer's* apps' overlay data — not the whole world.
- **Every read is `app_id`‑scoped + indexed**, so it's a selective index scan bounded by the app's slice, independent of total size. Required compound indexes:
  - `(tenant, app_id, record_id)` — detail‑page fetch (a handful of docs)
  - `(tenant, app_id, kind, status, created_at)` — queue/list (sorted + limited)
  - `(app_id, deleted_at)` — cleanup / GDPR sweep
  Hard rule: **no query without a leading `app_id`** (the access layer enforces it).
- **Bounded reads** — always limit/paginate (queue panels cap + virtualize; detail fetches a handful). No full‑collection scans.
- **Lifecycle keeps the hot set small** — soft‑delete (`deleted_at`) + a TTL/archive sweep for terminal rows so history doesn't bloat the working set.
- **Escape hatch:** Mongo sharding on `(tenant, app_id)` (or hashed `app_id`) for extreme scale; rarely needed for per‑customer operational data. If one high‑volume app dominates, promote *that one* to its own collection — an optimization, not the default.

**Why shared beats collection‑per‑app:** thousands of apps → thousands of collections → Mongo collection/index limits, multiplied admin, and broken cross‑app/GDPR sweeps. Shared + `app_id`‑prefixed compound indexes is the standard multi‑tenant pattern.

## 7. The recommend → deliberate → commit interplay

The overlay is where human judgment wraps the AI recommendation, between "recommend" and the governed write:
1. AI produces a recommendation on a record (read from golden SoR).
2. The operator **deliberates on the record** via whatever the builder modeled in the overlay (rationale, assignment, review, status…).
3. When satisfied, the operator **approves** the recommended action → the **only** thing that touches golden is the **governed MCP write**.

An action therefore typically **pairs** a *governed SoR write* (the business‑state change) with an *app‑local overlay write* (the BA‑defined data: who/why/rationale/annotation) — like audit history, but a proper, queryable, BA‑defined overlay. The agent routes each correctly because the `owner` tag tells it which is which.

## 8. Builder behavior — one mode, discovery‑led

There is **one** mode: **discovery‑led** (the SoR grounds the build). The overlay is an **additive, SoR‑anchored extension** the builder defines on top of the discovered SoR — not a from‑scratch data model. Concretely the builder:

1. **Discovers** the SoR datasets/columns/actions from the catalogue (as today).
2. **Detects standalone intent and declines it** — if the BA's ask has no SoR anchor (no underlying system for the records), the builder redirects per §4 rather than inventing a database.
3. **Defines the overlay shape** anchored to the SoR — light elicitation: which extra fields, anchored to which SoR record/operation, types, who can write.
4. **Confirms the overlay shape** — confirms the fields with the BA so the operational layer matches intent. Note there is **no DDL/`CREATE TABLE`** and no migration risk — the Mongo collection is schemaless and lazily created — so this is a *design* confirmation, not a point‑of‑no‑return provisioning gate.
5. **Field‑routing rule** — for each BA‑requested field, decide **governed‑SoR‑write** (a SoR field exists) vs **app‑local overlay‑write** (no SoR field); **never invent an app‑owned field where a SoR field already exists** (anti‑pattern §5.2).

## 9. Provisioning flow (concrete)

```
BA intent
  → builder discovers the SoR (catalogue/MCP)
  → ANCHOR CHECK: is the request in service of operating a SoR?
        no  → decline + redirect (no standalone DBs)
        yes → define the overlay SHAPE in the spec (fields, anchored to a record/operation)
  → CONFIRM the overlay shape with the BA      (design confirmation — no DDL)
  → register the overlay as a citra_app source (logical; shape comes from the spec)
  → NO provisioning step: the Mongo collection is lazy + schemaless, namespaced by
    (tenant, app_id), and resolved per-env by the EXISTING test_ prefix routing
  → builder composes UI (forms/queues/detail) joining golden (live) + overlay
  → at runtime, writes route by owner tag:
        customer_system → governed (recommend→approve→MCP)
        citra_app       → app‑local CRUD (direct, audited, RBAC'd)
```

## 10. Governance & safety

- **Audit:** app‑local overlay writes are audited like every other write (who/when/what), provenance = app‑owned.
- **RBAC / scope:** overlay docs honor the dept/role/scope model; access owner = the app's owner.
- **HITL:** SoR writes stay governed (recommend → approve); overlay writes are direct (audited).
- **No‑egress invariant preserved:** customer SoR data is never copied into the overlay (anti‑pattern §5.1).
- **Test/prod:** the overlay rides the **existing `test_`→prod collection routing** (the env contextvar). Promote is **spec‑only** — there is no overlay schema to migrate. Test interactions write `test_…` overlay docs, prod writes the unprefixed ones; both isolated automatically (see *SmartApp test→prod environments*).

## 11. Suggested phasing

- **Phase 0 — Substrate.** Reuse the existing **`smart_app_records`** collection (shared, `app_id`/`kind`‑scoped, env‑routed via `_route_col` — `test_smart_app_records` ⇄ `smart_app_records`), registered `owner=citra_app`. Add the scaling indexes — `(tenant, app_id, record_id)`, `(tenant, app_id, kind, status, created_at)`, `(app_id, deleted_at)` — and a TTL/archive sweep for terminal rows. **No DDL / provisioning service / per‑app collection.**
- **Phase 1 — Overlay runtime.** Build the **app‑local write path**: a write targeting an `owner=citra_app` field upserts a `smart_app_records` doc via `get_smart_app_records_col()` (env‑routed, so test writes land in `test_smart_app_records`), audited + RBAC'd — instead of a governed MCP write. Detail/queue UI joins golden (live) + overlay docs by `record_id`; provenance display. (Reads already work via the `smart_app_records` data_source.)
- **Phase 2 — Builder.** Discovery‑led overlay definition + anchor check (decline standalone) + shape‑confirm + field‑routing rule.

*(There is no standalone phase — standalone is out of scope.)*

## 12. Open questions

- **Collection namespacing — DECIDED:** single shared `smart_app_records` scoped by `app_id`/`kind` with `app_id`‑prefixed compound indexes (not per‑app collections; see §6a). Residual: the high‑volume trigger for sharding on `(tenant, app_id)` or splitting a single dominant app to its own collection.
- **Shape evolution:** additive field changes are free (schemaless); decide how the spec handles renamed/removed overlay fields vs existing docs.
- **Cross‑app shared overlay:** promote‑to‑org/dept mechanics + access model.
- **Durability/export** for overlay data (Citra holds the only copy).
- **When (if ever) Postgres‑via‑MCP:** the trigger for an overlay that outgrows document CRUD into relational/SQL/NL→query — kept as a future option, not the default.

---

### Related
- *Catalogue scope: dept_sources vs data_catalogue* — the grounding plane we extend with a `citra_app` source.
- *SmartApp test→prod environments* — the `test_`→prod collection routing the overlay rides (no separate schema promotion).
- *Editable plan‑then‑apply (FieldSpec/OptionsSource)* + *No writes from chat* — the governed‑write / HITL machinery overlay writes coexist with.
- *Roles + SA + ownership model* — `app_id`'s owner = Work SA/dept/org.
- *Platform primitives, not features* — why this is a substrate, not a fixed collaboration feature.
