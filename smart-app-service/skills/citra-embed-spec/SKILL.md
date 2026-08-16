---
name: citra-embed-spec
description: Author the AppSpec embed PAGE for a decision card the customer drops into their own application (kind="app", page.kind="embed")
metadata:
  category: citra
  tools: [bash]
---

# Citra Embed Spec

> **⚠️ The code is the contract — this skill is the GUIDE, not the source of truth.**
> What the runtime actually accepts, renders, and rejects lives in `citra-system` →
> `runtime-reference/`: `executor/models.py` (the field/enum/required contract),
> `renderer/` (how it displays), `validators/` (what blocks publish). Read
> `citra-system/ARCHITECTURE.md` FIRST (Phase 0). Use this skill for **how to choose
> and shape** things; wherever it restates a field, type, enum, or rule, the **code
> wins** — follow the code and flag the drift.


## Purpose

Author an **embed PAGE** (`page.kind="embed"`) inside a `kind="app"` AppSpec: a
single decision card the customer's own developers drop into a screen they
already have, using the `citra.js` bundle. Same agent, same recommend→approve
loop, same audit and learning — rendered inside *their* application instead of
the Citra shell.

An embed is not a separate artefact. It is an **app whose primary page is an
embed page**, exactly as a dashboard is an app whose primary page is a dashboard
page. `/build` signals it with **`BUILD_PRIMARY_PAGE_KIND=embed`**.


## Where this sits — the third surface

`AGENTS.md` makes you ask every build whether the BA wants a **Decision App**
(Citra UI) or a **headless Decision API** (no UI, they build their own). The
embed is the **middle answer**, and it is usually the one a bank actually wants:

| Surface | Who builds the UI | Cost to them |
|---|---|---|
| Decision App | Citra | none — but their officers leave their own system |
| **Embed** | **Citra, rendered inside their app** | **an afternoon** |
| Headless API | the customer | weeks — case view, recommendation display, accept/reject, reason capture |

**Why this matters, and why you should offer it:** the headless route means the
customer's developers rebuild the reason-capture UI themselves — and that is the
first thing cut under deadline. When it goes, the officer's *why* is never
recorded and the app never learns. The embed keeps that surface ours.


## When to Use

- `BUILD_PRIMARY_PAGE_KIND=embed`, or the BA says any of: *"inside our own
  system / our loan origination screen / our CRM / our agent desktop"*,
  *"our officers shouldn't switch apps"*, *"embed it in our portal"*.
- **Ask if it is ambiguous.** "Integrate with our system" can mean the embed OR
  the headless API. One question: *"Do you want us to render the decision card
  inside your screen, or do you want just the API and you build the UI?"*
- **NOT for** a headless build (`BUILD_HEADLESS=true`) — that has no UI at all.
  Do not author pages or panels; see AGENTS.md.


## The shape of an embed page

**ONE `detail` panel that carries both the record and the trigger. No queue.**

```json
{
  "id": "decision_card",
  "kind": "embed",
  "title": "Loan decision",
  "panels": [
    {
      "id": "card",
      "type": "detail",
      "data_source": "<your data_source id>",
      "id_field": "<the record key column from the catalogue>",
      "actions": [
        { "label": "Review", "agent_action": "review_application" }
      ],
      "sections": [
        { "type": "fields" },
        { "type": "documents", "data_source": "<your document source>" },
        { "type": "agent_timeline" },
        { "type": "comments" }
      ]
    }
  ]
}
```

…with the data source pinned to the host's record:

```json
{ "id": "applications_one", "type": "mcp",
  "ref": "<source>.<table>",
  "filters": { "<key column>": "{param.id}" } }
```

**The decision card is the `RunResultModal`, not a panel.** The officer's
approve/reject with reason codes lives in the modal the runtime opens after an
agent action fires. `detail.actions[].agent_action` fires it.

**Do NOT add a queue.** Earlier guidance said the trigger had to be a one-row
`queue`, because `agent_action` could only hang off a queue action. That is no
longer true — `detail.actions` exists precisely so an embed does not need one.
A queue on an embed page is actively wrong:

- its data source is pinned to `{param.id}`, so it always returns exactly one
  row — the search box, the Cards/Table/Split switcher, the row counter and
  pagination are controls that can never do anything;
- it repeats every field the detail panel below it already shows, so the card
  renders the same record twice — three times counting the host's own screen,
  which is already displaying it. Two renderings of one record, fetched
  separately, can disagree; on a credit screen that is worse than clutter.

**Do NOT use the `detail` `approval` section.** It reads the
`smartapp_pending_runs` collection, which nothing writes to;
`_stage_recommendation` writes to `smartapp_workflow_staging`. It renders
"Nothing awaiting approval" always. None of the production apps use it.

**`data_source`, NOT `linked_to`.** A detail panel normally reads its record
from the queue row the officer clicked. An embed has no queue and nothing is
clicked, so the detail binds its source **directly** and matches `id_field`
against the record id the host passed in. Publish REJECTS a detail panel that
sets both, or neither.

**Publish REJECTS an embed page with no `agent_action` anywhere.** A card that
cannot run the agent shows a record the host already has, produces no
recommendation and captures no reason — so it can never learn. It renders
perfectly, which is exactly why the rule exists.

`id_field` is the real key column from the dataset catalogue — whatever it is
called there. Never invent a name, and never assume `id`.


## Hard rules

- **The `detail` action is the point — never omit it.** It is what runs the
  agent, and the modal it opens is where the officer's reason code is captured.
  No action means no recommendation, no correction, no clause, no memory —
  **publish rejects an embed page without one**. A card without one is a
  *viewer*; if the BA only wants a viewer, say so plainly and confirm that is
  really what they want.
- **Do NOT author a `detail.approval` section.** It reads a collection nothing
  writes to and always renders "Nothing awaiting approval". Verified against a
  live stack; no production app uses it.
- **The action MUST declare `anchor_read`.** Without it the embed never learns.
  An embed passes only a record ID, so with no anchor read the runtime derives
  facets from the run's inputs — which carry the id and nothing else — and every
  correction lands with `case_facets: []`. Uncoded evidence can reinforce an
  existing judgement but can never author a new one, and it fails SILENTLY.
- **Do NOT author `case_signature.reason_codes`.** Deprecated — the officer
  writes a free-text reason (min 10 words) on both the reject and the
  override-then-apply path, and clustering partitions on `contested_fields`.
  A taxonomy would only reintroduce decline reasons on a correction surface.
  See `citra-app-spec` → case-signature §1b.
- **No `chart` or `map` panels.** The embed bundle excludes the charting and
  mapping libraries, so these cannot render — **publish rejects them on an embed
  page**. If the BA wants a trend beside the decision, that belongs on a
  dashboard page of the same app.
- **No navigation, one page.** The host owns the address bar; the embed cannot
  navigate. A `navigate` action to another page logs an error and does nothing.
  Design the whole decision to complete in this one card.
- **One record, never a list.** Filter the data source to `{param.id}` so the
  card shows exactly the record the host passed. Do NOT list other cases: the
  customer's own screen already does that, and duplicating it is the quickest
  way to make the card look like a foreign app bolted on.
- **Do not author theme colours.** The host passes `theme` at mount time
  (`primary`, `accent`, `font`, `radius`, `density`) so the card matches *their*
  application. Anything you hardcode fights them.
- **Same governance as everywhere.** Plan-then-apply, the policy gate, the
  DecisionRecord, the audit ledger, the outcome poller — all identical. An embed
  adds no new write path (H-01). The card is a surface, not an exception.


## What to tell the BA at the end

The BA is not the person who installs this — their **developer** is. Hand over
plainly:

> "Your embeddable card is published. In **My Apps**, open the card and click
> **Export** — you'll get a short HTML snippet with the embed id already filled
> in. Send that to whoever owns the screen you want it on; it's about ten lines
> and needs no build step. It'll run against test data until you promote it."

Two things they will ask, so answer them before they do:

- **"Does it need our React/Angular/whatever?"** No. It is one `<script>` tag
  and one `<div>`; the bundle carries everything.
- **"Will it look like ours?"** Yes — colours, font, corner radius and density
  are passed in at mount, and the card is style-isolated so nothing leaks either
  way.


## Worked example — loan application triage

The BA wants officers to keep working in the bank's loan origination system, not
a Citra tab. Their screen already shows one application at a time.

1. **Agent** (Phase 2) — unchanged. Same tools, grounding, policy gate.
2. **Page** — one embed page, one detail panel bound to the applications dataset
   via `data_source`, `id_field: "application_id"`.
3. **Sections** — `fields` (the application), `documents` (the income proofs,
   each with accept/reject), `agent_timeline` (why the agent recommended what it
   did), `approval` (Approve / Reject with the reason codes), `comments`.
4. **Reason codes** — from the BA: `income_not_corroborated`,
   `revenue_vs_tax_mismatch`, `dsa_sourced_needs_verification`, …
5. **Handoff** — the export snippet; their developer drops it beside the
   applicant header on the screen they already have.

The officer never leaves the bank's system, and every reject they record feeds
the same clause memory as any other surface.
