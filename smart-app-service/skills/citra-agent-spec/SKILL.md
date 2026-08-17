---
name: citra-agent-spec
description: Author and validate AgentSpec JSON, including sub-agents and actions
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

# Citra Agent Spec

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift. Don't trust a remembered rule over the
> runtime you can read.


## Purpose
Author the **AgentSpec** that powers a Citra Power AI App: system prompt, tools, MCPs, RAG bindings, sub-agents, actions, HITL policy.

## Required envelope — always emit these top-level fields
Every AgentSpec MUST carry these or the publish validator rejects it (422):

```jsonc
{
  "spec_version": "v0",
  "agent_id": "<lowercase-hyphenated, unique; matches AppSpec.agent_id>",
  "name": "<short human title, e.g. 'Theft Triage'>",   // REQUIRED, non-empty
  "system_prompt": "<role + scope + decision rules>",     // REQUIRED, non-empty
  "model_tier": "large",   // default tier for the agent (large = safest)
  "actions": [ /* at least one */ ],
  "tools_v2": [ /* discriminated tools */ ]
}
```

`agent_id` + `name` + `system_prompt` are mandatory and easy to forget — set all three first, before the actions/tools. `agent_id` must equal `AppSpec.agent_id`.

## When to Use
- Phase 2 (Expertise) of a Power AI App build.
- After Phase 1 (Internship) has discovered MCPs, sampled RAG, and clarified vocabulary with the BA.
- When extending an existing app with a new sub-agent or action.

## Hard Rules
- AgentSpec is **JSON only**. **Validate against `/workspace/.openclaw/workspace/schemas/agent_spec.schema.json` (and the AppSpec against its schema) BEFORE saving — this is the single biggest publish‑friction killer.** The schema sets `additionalProperties:false` on nearly every object and `pattern` on every id/name, so the publish gate 422s on *any* invented/extra field or wrong‑format id. Validating locally catches all of it up front; do **not** "validate at publish time."
- **All `id` and `name` fields are lowercase snake_case `^[a-z][a-z0-9_]*$`** — e.g. action `name: "analyze_tamper_event"`, NOT `"Analyze Tamper Event"`. This applies to action `name`, `sub_agents[].id`, tool `name`, and (in the AppSpec) data_source/page/panel/trigger ids. Human‑readable display text goes in `description` (or the AppSpec panel/page `title`), **never** in `name`/`id`. A name with spaces or capitals fails the pattern and blocks publish.
- **Sub-agents are JSON, not pods.** They run as LLM calls at runtime with their own system prompt and tool subset.
- Every action's `delegates_to` must reference an existing `sub_agents[].id`.
- **Model tier is YOUR call per decision complexity** — `large` | `medium` | `small` (the platform maps each to a configured model; legacy `tier_a/b/c` all map to large). The runtime resolves the tier per ACTION: set `model_tier` on an Action to override the agent default for that one decision.
  - **Match the tier to the reasoning the decision demands:**
    - **`large` → high-complexity reasoning** — multi-source synthesis, open-ended judgement, ambiguous inputs, policy/precedent weighing, anything high-stakes. This is also the **default**: a smaller model misreading a decision is the dangerous failure, and an unconfigured smaller tier falls back to large anyway, so when in doubt use `large`.
    - **`medium` → medium-complexity decisions** — moderate, well-structured reasoning over a bounded set of clear rules (e.g. routing / prioritising on a handful of known fields).
    - **`small` → low-complexity steps** — genuinely simple / deterministic work only: a fixed classification, a yes/no on explicit fields, a lookup-and-label.
  - **Pick the tier by how hard the DECISION is — NOT by whether the action writes.** A write being a write is *not* a reason to use `large`: every source write is already governed by plan-then-apply + the officer's explicit approval, so the safety net is the human click, not the model size. Treating writes as "irreversible → must be large" would push *every* action to `large` and defeats the tier entirely. Judge only the reasoning: ambiguous inputs / multi-source synthesis / policy-precedent weighing → `large`; bounded rules over a few known fields → `medium`; a fixed classification or yes/no → `small`. When the reasoning genuinely is hard, keep `large`.
  - Example: a `classify_complaint` action can be `small`. An `update_recovery_status` action that just applies a status the officer reviews is a bounded decision over a few fields → `medium` is appropriate; reserve `large` for when the recommendation itself requires weighing thin precedent or ambiguous evidence — not because it happens to write.
  - **FORCING FUNCTION — assign a tier to EVERY action on purpose.** Before you save, go action-by-action and set `model_tier` with a one-line complexity justification; do NOT leave a trivial classify to silently inherit the agent default (`large`). The target is the **cheapest tier that still reasons correctly** — `large` is the safe floor, not the reflex. A spec where every action is `large`/unset is a smell: re-examine each one and downgrade the genuinely bounded/deterministic decisions to `medium`/`small`. (Tier drives both latency and cost — an all-`large` app is needlessly slow and expensive.)
  - **Do not use the legacy `tier_a/b/c`** when authoring new specs — write `large`/`medium`/`small` explicitly so the intent is readable (the legacy values all silently collapse to `large`).
- `system_prompt` describes role, scope, and decision rules — not how to use tools (the runtime injects tool hints).

### Write FAST system prompts — the selected record is ALREADY in `inputs`

When an agent action is fired from a queue row, **the runtime passes that
row's full record into the run and injects it verbatim into the agent's
prompt** (under an `Inputs:` JSON block). The agent already HAS the selected
record — its ids and fields are in hand before the first tool call.

So the `system_prompt` must NOT tell the agent to re-fetch the record it was
handed. **The #1 cause of slow, expensive runs (a single "Analyze" click
taking minutes and 10–15 tool calls) is a prompt that says "first read the
case from `<source>`":** the agent dutifully re-reads data it already has, the
read returns broadly because it can't be scoped, and the agent loops re-querying
the same sources. Write the prompt to this shape instead:

1. **Use the provided record.** State plainly: *"The selected record's fields
   are provided in your Inputs — treat them as the authoritative case data. Do
   NOT call `<read tool>` to re-fetch the record; you already have it."*
2. **Fetch only supplementary data, ONCE each.** The agent calls `mcp`/`rag`
   tools ONLY for data NOT already in the record — related rows on *another*
   source (e.g. tamper events for the record's meter) or policy guidance. Scope
   each read with the ids already in the record (`consumer_id`, `meter_id`).
   Say: *"Read each supplementary source at most ONCE, filtered by the ids in
   the record. Never re-query a source you have already read."*
3. **Then decide.** *"Once you have the record plus the supplementary data,
   produce the recommendation — do not gather more."*

Because the agent scopes supplementary reads by ids from the record, **make sure
the queue panel's `columns` include those ids** (`consumer_id`, `meter_id`, …),
not just the human-facing display fields — otherwise they don't reach `inputs`
and the agent can't scope its reads.

**Anti-pattern — NEVER generate an enumerated "gather everything" rule like:**
> "Gather context first. For every case read: the case record from theft_cases;
> all tamper events via consumer_id → meter_id → tamper_events; search the policy
> corpus for the SOP, the Circular, and the Guidelines."

That invites 10+ tool calls and a re-read of data already in `inputs`. The
corrected rule names the record as already-present and caps supplementary reads
at one per source.

- **Every `validate_form` tool's `schema_ref` MUST resolve to a real FormPanel.** The publish endpoint cross-checks `agent_spec.tools_v2[].schema_ref` against `app_spec.panels[]` (and `app_spec.pages[].panels[]`) and rejects the publish if no match. Verify locally before saving — see "Cross-spec sanity check" below.

## Sub-agent decomposition (when to add one)
Add a sub-agent when the goal has a **distinct unit of expertise** with its own:
- domain knowledge (compliance, fraud, risk),
- tool subset (only this expert needs the underwriting MCP),
- or output format (a report writer that produces consistent prose).

Common sub-agents seen across BPO apps:
| Role | When |
|---|---|
| `compliance` | Audit against rules / SOPs / regulations. |
| `fraud_check` | Score for fraud likelihood. |
| `report_writer` | Produce one-page summaries / approvals. |
| `classifier` | Triage / routing / category assignment. |
| `data_collector` | Gather facts from multiple MCPs. |

Stop at 3–5 sub-agents. More than that = re-think your decomposition.

## Safety rules (citations)

### Severity-aware tool selection

- **H-02** — agent_spec for chat-surface agents (any agent reachable from an `agent_chat` panel) must NOT register tools of kind `mcp_action` or `smart_app_invoke`. Chat surfaces are structurally read-only — writes happen from queue actions only.
- **H-04** — Never set `hitl_policy.allow_writes_in_chat`. The field is rejected by the publish validator; chat is read-only by construction (see H-02).
- **Sub-agent tool subset (no rule-id)** — Sub-agent `tools_v2[]` is a strict subset of the parent's. Do not grant a sub-agent a tool the root agent does not itself hold; the publish validator enforces this. (Not a citra-safety-rules entry — X-04 there is the mcp_action allowlist rule.)

Refer to [citra-safety-rules](../citra-safety-rules/SKILL.md) for the canonical rule list.

## Workflow

Narrate as you go per [`AGENTS.md`](../../AGENTS.md). Agent design is one of the longest phases — keep the BA in the loop with status lines between major operations.

1. Confirm the goal's primary actions with the BA in plain language ("What does the app *do*?"). (Question to BA — regular prose, no `>` prefix.)
2. **Narrate** before drafting:
   ```
   > 🧠 Drafting the agent's system prompt in role / scope / rules style...
   ```
   Then draft `system_prompt` in role-scope-rules style.
3. **Narrate** before sub-agent decomposition:
   ```
   > 🧠 Deciding if this needs sub-agents — looking at how distinct the expertise areas are...
   > ✅ One root agent is enough — no sub-agents needed
   ```
   (or `> ✅ Splitting into 2 sub-agents: triage + writer`)
   Decide sub-agents. Write each one's `system_prompt` as if briefing a junior colleague.
3.5. **Pick tools for `tools_v2[]`.** Read `citra-tool-catalogue/SKILL.md` first. Then walk this decision tree:

    - Goal mentions a form / submission / data entry?
        → Add `validate_form` (mandatory whenever AppSpec has a FormPanel).
    - Goal has an image or document that DRIVES A DECISION — assess / judge / grade
      a photo (damage, defect, condition), or pull structured fields from a document
      (report, invoice, ID) and ACT on them, with the officer reviewing each item?
        → **THIS IS THE DEFAULT for any image/document decision app.** Add a
          STRUCTURED analysis tool (one per `task_type`) IF `OCR_ENABLED=true`:
            * an IMAGE task   → `image_analyze`   (e.g. task_type `asset-inspection-defect`)
            * a DOCUMENT task → `doc_extract`     (e.g. task_type `inspection-report`)
          Both return a STRUCTURED `ItemFinding` (fields + recommendation + confidence)
          the officer accepts / rejects PER ITEM, and reject-reasons train that
          `task_type`'s rubric. **Record-bind** them — set `data_source_id` +
          `url_column` + `key_field` so the agent passes a short `record_id`, NEVER a
          raw/signed URL (a copied signed URL corrupts → 403). When an SOP governs the
          judgment, set `sop_source` (the policy/SOP RAG corpus id) so the TOOL fetches +
          caches the live SOP itself — do NOT seed criteria in Mongo. Read
          `citra-agent-spec/references/image-analyze.md`.
          If `OCR_ENABLED=false`, push to `requirements_unmet`.
          ⚠️ Do NOT use `vision_ocr` here. "analyze the photo" / "extract from the
          report" / "recommend pass-fail" ⇒ `image_analyze` / `doc_extract` — NOT
          `vision_ocr` (which has no structure, no per-item review, no learning).
    - Goal needs ONLY raw OCR text off an image/PDF — the agent reads the text and
      reasons over it ITSELF, with NO per-item judgment, review, or learning (rare)?
        → Add `vision_ocr` IF `OCR_ENABLED=true`. Read `citra-ocr/SKILL.md`. If
          `OCR_ENABLED=false`, push the upload requirement to `requirements_unmet`.
    - Goal has an API / System-of-Record CHECK that DRIVES A DECISION and the
      officer should review EACH check on its own — a loan that must clear a
      credit-bureau check + an identity-verification match, a KYC/onboarding that
      runs a sanctions / watchlist lookup, etc. The primitive is region-neutral;
      the check just differs by market — a credit check is CIBIL / Experian in
      India, Experian / Equifax / TransUnion (FICO) in the US; an identity match
      is Aadhaar / PAN in India, SSN / driver's-license verification in the US.
      ("Credit check looks good [accept/reject], identity match looks good
      [accept/reject]" + one overall application approve/reject)?
        → Add a `check_evaluate` tool **(one per check `task_type`)** fed by the
          `mcp` read for that API: the agent calls the `mcp` read (e.g.
          `bureau.credit`), then passes its result as `data` to `check_evaluate`,
          which returns a STRUCTURED `ItemFinding` (modality `api`) the officer
          accepts/rejects PER CHECK — and reject-reasons train that check's
          `(api, task_type)` rubric, exactly like image/doc findings. Give each
          check a DISTINCT, region-neutral `task_type` (`credit-check`,
          `identity-match`, `sanctions-screen`) — `task_type` is the unit of both
          review and learning. Set `sop_source` to the acceptance policy (the
          market-specific thresholds live in the SOP/rubric, not the tool). Two modes:
            * `mode="llm"` (default) — for grey-area checks (name/identity match,
              a bureau flag officers override, a borderline score with mitigants);
            * `mode="rule"` + `rule_expr` (e.g. `"credit_score >= 700"`) — for a
              fixed-threshold check; NO LLM call, and a broken rule fails to
              `flag` (manual review), never a silent pass.
          In `system_prompt`, mandate the sequence: read the API via its `mcp`
          tool → call the matching `check_evaluate` with the result → after all
          checks, give the overall recommendation. Do NOT reuse `consistency_check`
          for this (that's record↔artifact fraud screening, not per-API review).
    - If you added ANY `image_analyze`/`doc_extract`/`check_evaluate` tool → **ask
      the BA** for the per-item review gate and set `item_review_gate` on the
      AppSpec (`hard` default / `soft` / `none`). `hard` blocks record-Apply until
      every image, document, AND API check is reviewed. See
      `references/image-analyze.md`.
    - **Fraud & Consistency Screening — PROPOSE it when the stakes warrant it.**
      If the app decides a disposition over MONEY or ASSET VALUE (approve / reject /
      pay / settle — claims, loans, reimbursements, disbursements, KYC) AND the case
      carries identity fields plus supporting documents/images → PROPOSE screening to
      the BA (never add silently; never for simple routing / FAQ / status apps).
      **KNOW: the source ONTOLOGY overrides you in BOTH directions at publish.**
      `autowire_fraud_roles` runs on every publish: a dataset whose sources.json
      opted IN (`fraud_screening.applies:true`, or artifact-role columns) gets a
      `fraud_screen_<ds>` consistency_check auto-created/wired even if you added
      none; a dataset opted OUT (`applies:false`) gets its `url_columns` CLEARED
      even if you wired them. Your propose-to-the-BA rule governs the judgment
      call in the middle (no ontology signal either way):
        * declare a `consistency_check` tool (local, free — cross-checks the record's
          claimed values vs doc/image-extracted values + format/checksum validators,
          and links the case's identifiers into the cross-case entity index: the
          agent passes `record_id` and gets `entity_signals[]` back — same phone /
          VIN / invoice-no appearing on OTHER cases, or one identifier carrying many
          names). For SoR-side velocity ("how many claims from this phone in 90
          days"), add a dataset-bound `mcp` read tool and use `filters` for the
          exact lookup — do not NL-query it. If the corporate subscribes to an
          external fraud registry (ClaimSearch / bureau) it is registered by IT
          as a `rest_api` live-passthrough source on the dept-MCP — declare the
          matching `mcp` read tool, query it with the case's identifiers, and
          nest its raw matches under the key `external_registry_matches` inside
          the signals passed to `fraud_synthesis` (scored as the highest-
          precision signal). NEVER call an external registry directly — corporate
          credentials and FCRA-grade audit live at the MCP layer;
        * in `agent_spec.system_prompt`, mandate the screening sequence: read the
          record FIRST → pass FULL case context in every `image_analyze`/`doc_extract`
          `query` (claimed asset, incident, date, location, amount, identifiers to
          verify — the `query` argument is REQUIRED; a context-free call analyzes
          blind) → run `consistency_check` on claimed-vs-extracted → cite every
          mismatch, every `entity_signals[]` hit, every `exif_findings[]` signal (photo predates incident / GPS far from site), and every `artifact_flags` signal
          (EXACT duplicate artifact across cases, `phash_near_dups` — same picture
          re-encoded, `image_index.near_duplicates` — cropped/re-shot copies from
          other cases, metadata anomalies like modified-after-creation or editing
          software) as EVIDENCE in the recommendation with severity. The
          ontology may ALSO have armed (autowired, never authored by you):
          `payment_findings[]`/`payment_verified` (submitted payment proof
          verified BY KEY against the declared ledger — ref-not-found / amount /
          date / wrong-party), `verify_findings[]`/`verifications[]` (generic
          document-vs-dataset checks), and `date_rule_findings[]` (declarative
          date rules on the record's own values). Cite each finding's `why`
          VERBATIM — the sentences are written officer-ready. Cite the POSITIVE
          verdicts too: `payment_verified:true` / a verified check means the
          customer's document matches the system of record — lead the
          recommendation with that when resolving in their favor. If the app
          extracts BANK STATEMENTS, pass the extracted rows (in document order)
          as `statement_rows` to `consistency_check` — the running balance is
          reconciled automatically (repeated chain breaks = fabricated-statement
          evidence; single breaks are treated as OCR noise);
        * also declare a `fraud_synthesis` tool and mandate in `system_prompt`
          that the agent calls it LAST, passing `record_id`, a full case-context
          summary, and ALL collected signals verbatim (the consistency_check
          output + each finding's `artifact_flags`). Calling it is always safe —
          it gates itself server-side and only spends a reasoning pass when the
          deterministic severity score crosses the gate (or a small audit
          sample). Cite its `key_indicators` / `benign_explanations` /
          `recommended_checks` in the recommendation;
        * screening findings are evidence for the OFFICER — the agent must never
          auto-reject on them (universal-approval invariant). Officer fraud-flag
          feedback (confirm/dismiss with a reason via the item-feedback endpoint,
          `modality="case"`, `task_type="fraud-screening"`) trains the fraud
          case rubric the synthesis reads.
      Rationale + the full taxonomy: `docs/fraud-detection-primitives-plan.md`.
    - Goal needs enterprise lookups (policy, customer record, claim history)?
        → Add `mcp` entries (`kind: "mcp"`) — **one per DATASET the agent reads,
          not one per source.** A single source-wide tool forces every lookup
          through the slow NL→SQL planner and makes the agent re-query the same
          table with different wording. Instead give each read its own dataset-
          bound tool and set `dataset_id` (the catalogue `<source>.<table>`) +
          `dataset_kind` (sql | mongodb | …) on it. That unlocks the **keyed-read
          fast-path**: for an EXACT lookup by a known id, the agent passes
          `filters: {column: value}` and the runtime serves it via the structured
          `/run_query` (no planner LLM — ~ms instead of 10–30s). In the
          `system_prompt`, tell the agent: *"When you already know a record's
          id/key, look it up with `filters`, NOT a natural-language `query`."*
          Use NL `query` only for fuzzy/semantic search or aggregation.
        - **Mandated checks (bureau / KYC / sanctions).** A dataset the IT team
          marked `mandatory_when_used` in the catalogue defaults its bound mcp
          tool to `required: true` automatically (you do NOT set it) — the
          read-before-write gate then refuses to stage a write unless that lookup
          ran, and the prompt tells the agent to run it first. Just BIND the tool
          (`dataset_id` set) so the auto-wiring applies; set `required: false`
          explicitly only to deliberately opt this app out of an otherwise
          mandatory check.
    - Goal needs to consult policy SOPs / regulations / handbooks?
        → Add `rag` entries.
    - **Goal's action is supposed to *change a record* — route it, flag it,
      record a verdict, approve it?** A read-only agent produces a
      recommendation but the record never moves, so the queue never
      changes. → Add an **`mcp_action`** tool (see below) and instruct the
      agent in `system_prompt` to call it once the decision is final.
    - Goal triggers a side-effect (send a letter, kick off a process)?
        → Model it as a catalogued **`mcp_action`** write the dept-MCP exposes
          (audited + plan-then-apply). SmartApps do not call workflows.
    - Cross-validator rules to remember:
        * `validate_form` must come BEFORE `vision_ocr` in `tools_v2[]`.
        * `system_prompt` must mention `validate_form` (case-insensitive).
        * Every `validate_form.schema_ref` must match a FormPanel.id or .schema_ref in the AppSpec.

### `lookup_judgement` — the evidence behind a learned judgement

Add this to **every decision agent whose app can learn** (any app with a
review/approve loop). One entry, no configuration:

```json
{ "kind": "lookup_judgement", "name": "lookup_judgement",
  "description": "Fetch the cases behind a learned judgement — what officers wrote, what they changed, and how often it held up." }
```

Learned judgements are already injected into the prompt for every matching
case, and each injected line is self-sufficient: the rule, the concrete move
officers made (`decision = verify_employment`), and how many stand behind it.
The agent can act correctly **without ever calling this tool**.

The tool answers the other question — *why does this judgement exist* — from
the source corrections in the officers' own sentences, each with its case and
what was changed. That material is far too large to inject on every run, and
too valuable to leave unreachable. Tell the agent in `system_prompt` to use it
**before overruling a judgement**, or when a judgement's wording is too vague
to act on.

Local, read-only, no cost gate, no dept-MCP. Omitting it is safe — the runtime
only mentions the tool when the agent declares it, so an app without one is
never told to call something it does not have.

### `mcp_action` — write tools (state-changing actions)

`kind: "mcp"` only **reads**. To let an agent action actually change
record state, add a `kind: "mcp_action"` tool bound to a catalogue
**write action**. `citra-mcp-discover` lists each source's
`write_actions[]` — every one has an `id`, the `dataset_id` it runs on,
and an `input_schema`. Wire it:

```jsonc
{
  "kind": "mcp_action",
  "name": "route_grievance",
  "description": "Persist the triage routing on a grievance.",
  "source_id": "urban_grievances",
  "dataset_id": "urban_grievances.grievances",
  "action_id": "route_grievance",
  "input_schema": { /* copied verbatim from the write action */ }
}
```

Then the `system_prompt` MUST instruct the agent to call it — e.g.
*"Once you have decided the owning department, priority and SLA, call
the `route_grievance` action to persist the routing."* Without that
sentence the agent treats the write tool as optional and the record
never changes. If the goal is "review and decide", a write tool is
almost always required — a Smart App that only *recommends* is rarely
what the BA asked for.

#### `editable_fields` — let the officer OVERRIDE the recommendation

When the BA says *"the agent should recommend, but the officer can change
the assignee / priority before applying"*, add **`editable_fields`** to the
`mcp_action` tool. In the plan-then-apply modal these payload fields render
as controls (the LLM's value is the **default**); the officer can change them;
Apply re-validates the edited payload via `dry_run` and audits the delta.
Each `editable_fields[].name` MUST be a property in the tool's `input_schema`.

```jsonc
"editable_fields": [
  { "name": "assigned_to", "label": "Assign to", "control": "select",
    "help": "Override the recommended officer",
    "options": {                       // prepopulates the combo
      "kind": "data_source",           // live DISTINCT values from a dataset
      "data_source": "ds_officers",    // a declared app_spec data_source id
      "value_column": "officer_email", "label_column": "officer_name",
      "filter": { "division": "${record.division}" }   // optional, row-scoped
    } },
  { "name": "priority", "label": "Priority", "control": "select",
    "options": { "kind": "static",     // a fixed list (matches an input_schema enum)
      "values": [ {"value":"low","label":"Low"}, {"value":"medium","label":"Medium"},
                  {"value":"high","label":"High"} ] } }
]
```

Rules: `options.kind` is `static` (a literal list), `data_source` (live DISTINCT
column values — the picker), or `agent` (the LLM proposes candidates inline).

**Choosing the kind — this matters, do not get it wrong.** If the field's allowed
values come from the DATA and can change over time — people / officers / crews,
teams, divisions, locations, vendors, accounts, any *growing* category list — use
**`data_source`** (give it the `data_source` id + `value_column`). The runtime
then resolves the live `SELECT DISTINCT` on every combo open, so new values appear
immediately. **NEVER hardcode such a list into `static`** — that freezes it at
build time and goes stale the moment the source data changes (a new officer would
never show up). Use **`static`** ONLY for a genuinely fixed, SOP-defined enum
(e.g. priority `low/medium/high`, a status lifecycle). When in doubt, prefer
`data_source`. Do **not** copy the catalogue's `distinct_values` preview into a
`static` list — that preview is a sampled, capped hint, not the source of truth.

`control` may be omitted (inferred from the input_schema field type). Omit
`editable_fields` entirely for actions the officer should only approve/cancel
verbatim — undeclared fields are not editable, so the modal stays read-only.

> **A declared editable field with NO resolvable `options` renders LOCKED, not
> free-text.** Governed override is allow-list-only by design — an officer can
> never type an out-of-list value (the approve path rejects it with
> `override value … is not an allowed option`). So a field you put in
> `editable_fields` but give **no** `options` (and no free-text `control`) shows
> as *"Locked (set by the agent)"* — the exact opposite of what you intended.
> If a field is in `editable_fields`, it MUST carry an `options` source.
>
> **This bites hardest on the DISPOSITION field** — the verdict the agent sets
> (`status` pass/repair/fail, `decision` approve/reject/route_to_siu, claim
> outcome). That is the single field an officer most often needs to override
> (e.g. the model flags fraud → recommends `fail`, but the officer reviews and
> approves the claim → `pass`). Whenever an `mcp_action`'s `input_schema` marks
> a field as an `enum`, and that field is officer-overridable, its
> `editable_fields` entry MUST declare `"control": "select"` **and** a `static`
> `options` list mirroring the enum values.
>
> **Platform safety net (do not lean on it):** when an editable field declares
> neither `control` nor `options` but the tool's own `input_schema` constrains
> it with an `enum`, the runtime auto-derives `control:"select"` + a static
> options list from that enum at plan time (and Apply validates against the
> same list). The spec linter flags the omission as
> `enum_field_missing_options`. Declare options explicitly anyway — you control
> the labels (e.g. "Route to SIU" instead of "Route_To_Siu") and can offer a
> deliberate subset of the enum.

4. **Narrate** before tool selection finishes:
   ```
   > 🛠️ Picking the tools the agent will use — looks like validate_form, vision_ocr, mcp.claims.lookup_policy, rag.claims
   ```
5. Define `actions[]`. Each action = one entry point invoked from an AppSpec panel.
6. Define `input_schema` (JSON Schema). This drives the form panel.
7. **Set `hitl_policy` if the BA wants approvals.** See "HITL policy" below — it's optional. The platform does NOT auto-gate writes.
8. **Narrate** the file write + validation:
   ```
   > 📝 Writing the agent spec...
   > ✅ Spec validates against schema and Pydantic model
   ```
   Write `~/workspace/build/agent_spec.json`. Validate (see `citra-app-spec` for the snippet).
9. **Cross-spec sanity check** — verify every `validate_form` tool resolves to a real FormPanel **before** running self-test. The publish endpoint enforces this; failing locally is cheaper than failing at /publish.
   ```bash
   python3 << 'PY'
   import json
   agent = json.load(open("/workspace/build/agent_spec.json"))
   app   = json.load(open("/workspace/build/app_spec.json"))

   # Collect every panel.id + panel.schema_ref the AppSpec advertises,
   # across both flat panels[] and pages[].panels[].
   def walk_panels(spec):
       for p in spec.get("panels") or []:
           yield p
       for page in spec.get("pages") or []:
           for p in page.get("panels") or []:
               yield p

   form_ids = set()
   form_schema_refs = set()
   for p in walk_panels(app):
       if p.get("type") == "form":
           form_ids.add(p.get("id"))
           if p.get("schema_ref"):
               form_schema_refs.add(p["schema_ref"])

   bad = []
   for t in agent.get("tools_v2") or []:
       if t.get("kind") != "validate_form":
           continue
       ref = t.get("schema_ref")
       if ref not in form_ids and ref not in form_schema_refs:
           bad.append((t.get("name"), ref))

   if bad:
       print("FAIL: validate_form schema_ref(s) don't resolve to any FormPanel:")
       for name, ref in bad:
           print(f"  - tool {name!r} → schema_ref={ref!r}")
       print(f"  available FormPanel ids: {sorted(form_ids)}")
       print(f"  available FormPanel schema_refs: {sorted(form_schema_refs)}")
       raise SystemExit(1)
   print("OK: every validate_form tool resolves to a FormPanel")
   PY
   ```
   If this fails: either rename the tool's `schema_ref` to match the FormPanel id, or add a FormPanel with that id to `app_spec.json`. Re-run the check before saving.
10. Run `citra-self-test` against sample inputs. (The self-test skill emits its own narrations during the run.)

## HITL policy

Approval is **universal** — every source-system write the agent makes pends in the officer's recommendation queue and commits only on Approve (the mechanism is **Approval flow** below). So you do **not** set per-action `approval_required` and you do **not** set `thresholds`: there is no auto-execute and no severity/amount exception, which makes those knobs moot. Chat is read-only by construction (H-02 / H-04) — route any write to a queue action or a form `on_submit`, never chat.

The one optional knob is **`hitl_policy.approvers`** — an array of `user_id` strings that restricts WHO may Approve (empty/missing = any tenant member except the run's own actor). Set it only when the BA names specific sign-offs; use real user_ids from the workspace member list.

```json
"hitl_policy": { "approvers": ["u_finance_lead"] }
```

**Unattended / overnight / trigger-fired runs still pend** — the officer reviews the queue. There is no "no-approval" mode; a wrong write is corrected by a normal compensating write or an IT source fix.

---

## Approval flow

**You declare no approve/reject actions — the platform owns the write path. There are THREE execution modes; the BA picks (see AGENTS.md "EXECUTION MODE"), and you build the one they chose. Do NOT collapse all three into recommend-only.**

1. **On-demand** — the officer clicks a queue action; the agent runs, its writes are captured as `planned_writes` and **staged** into the one queue (`smartapp_workflow_staging`); the officer clicks **Approve/Reject/Cancel**; Approve replays the writes. The click is the only path to the source write.
2. **Auto-recommend** — an **app trigger** (schedule/webhook/poll) runs the SAME agent *ahead of time* and pre-stages the recommendation into the SAME queue; the officer still approves. (Trigger with **no** `auto_process_policy`.)
3. **Auto-process** — the BA opts in to bounded autonomy: a trigger with **`execution_mode="auto_process"` + an `AutoProcessPolicy`** (see `citra-safety-rules` "Auto-process" and `models.py` `AutoProcessPolicy`). The policy engine — **deterministic, no LLM** — auto-**commits** the writes that clear the BA's bound (`auto_commit_when`, `value_cap`, `confidence_min`, `rate_limit_per_hour`) and **fails closed to the human queue** on any miss/error. Whatever the policy doesn't clear pends like mode 2.

So "nothing auto-executes" is true for modes 1–2 only. **Mode 3 auto-commits within the BA's deterministic bound** — when the BA asks to "auto-process X under ₹N / auto-approve clear-cut cases," that is mode 3: **ask for the bounding conditions and wire `triggers[].auto_process_policy`** (see `citra-app-spec` Triggers). Do not silently downgrade it to auto-recommend — and if you ever can't honor a stated auto-commit ask, say so plainly; never imply auto-commit while building recommend-only.

What this means for the AgentSpec (all three modes):
- **Author the agent's normal decision action(s)** (e.g. `process_claim`) with their `mcp_action` tool(s). The agent's job is the same in all modes — propose the write with `decision` / `reasoning`. You do **not** write `approve_`/`reject_` actions and you do **not** set `approval_required`.
- The mode is set on the **AppSpec** (a queue action = on-demand; a trigger = auto-recommend; a trigger + `auto_process_policy` = auto-process) — NOT on the AgentSpec. The same agent powers all three.
- On-demand and trigger-precomputed recommendations land in the **same** queue and approve the **same** way; auto-process commits the cleared ones directly and queues the rest.

**Assignment / reassignment is a separate capability** the builder composes as its own panel + action — it is **not** part of this approval flow. Don't add assign/route logic to the agent's approval path.

## Output
A single `agent_spec.json` at `~/workspace/build/agent_spec.json`.
