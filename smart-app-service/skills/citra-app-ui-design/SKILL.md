---
name: citra-app-ui-design
description: Phased UI design conversation with the BA — propose pages, accept feedback, iterate. Produces a structured ui_design.md scratchpad consumed by citra-app-spec.
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

# Citra App UI Design

## Purpose
Sit between **Phase 2 (agent design)** and **Phase 3 (AppSpec compose)** and run a **step-by-step UI design conversation** with the Business Analyst. You **propose** a multi-page layout, accept feedback, **iterate**, and freeze a `ui_design.md` scratchpad. `citra-app-spec` then mechanically translates that scratchpad into `app_spec.json` (with `pages[]`, `navigation`, and `navigate` actions). You do **not** write JSON. You design the user journey in plain language.

This skill enables multi-page apps. Older single-page apps fell out of one big `panels[]`; that mode is still legal but is no longer the default. Pick single-page only when the goal really is one screen.

## When to Use
- **🛑 STOP if this is a HEADLESS / decision-API build** (`BUILD_HEADLESS=true`, or the BA wants "just the API / their own UI / no Citra UI" — see AGENTS.md "🔌 HEADLESS"). A headless app has **no UI** — do **NOT** run this skill at all. Return to the build plan and go straight to Phase 3.5, where `citra-app-spec` authors a `headless:true` stub AppSpec with no panels/pages. Running this skill for a headless app (designing queue/detail panels) is the build's #1 mistake.
- App path, after `citra-agent-spec` has produced `agent_spec.json` and the BA has approved the agent's actions.
- For a **pure dashboard** (`BUILD_PRIMARY_PAGE_KIND=dashboard` with no other pages in scope) you may skip straight to `citra-dashboard-spec`, which authors the single dashboard page + narrator directly. But for a **multi-page info app** that includes a dashboard page alongside queues/documents/an assistant (e.g. a CMD briefing), run this skill: design the dashboard page as one page (`kind: "dashboard"`) and the others as standard pages.
- **🛑 STOP for an EMBED build** (`BUILD_PRIMARY_PAGE_KIND=embed`, or the BA wants the decision card rendered inside their own application). An embed is ONE card on ONE page — there are no pages to lay out, no navigation to design, and no queue (the host's own screen owns the list and passes the record id in). Running this skill's multi-page question script against an embed produces exactly the wrong app: pages, nav and a duplicate worklist. Go straight to `citra-embed-spec`.
  - The exception is a **mixed app** — an embed page alongside standard pages an officer also opens in Citra directly. Then run this skill for the standard pages, and author the embed page with `citra-embed-spec`.
- On the edit flow, re-run this skill against the seeded `app_spec.json` to propose changes to an existing app's pages.

## Hard Rules
- **You never write JSON.** This skill produces `ui_design.md` (a structured Markdown plan), not `app_spec.json`. The `citra-app-spec` skill is the only place where JSON is authored.
- **You never propose components or write code.** Pages, panels, navigation — that's the vocabulary.
- **A Smart App is an INTERNAL staff tool, not a public website.** You design the surface the organisation's own officers / analysts / approvers use in the office. **Never** propose a citizen-facing or public intake/filing page (e.g. "a page where the public submits a grievance / files an application"). Public submission happens on a separate public portal — out of scope for a Smart App. If the BA's goal describes citizen filing, say so plainly, record it as out-of-scope, and design only the officer-facing surface: triage/review queues, detail views, dashboards, approvals, agent chat.
- **Ask, then propose, then ask again.** Walk the BA through the canonical question script in 3.1 **one question at a time**. Never dump the whole layout in a single message — that produces lukewarm "looks fine" answers, not real feedback.
- **One question per turn.** Wait for the BA's answer before moving to the next question. State your recommendation in one sentence, then ask. Don't pile up four questions.
- **Persist every BA answer** to `/workspace/build/ui_design.md` **before moving to the next question**. The file is the source of truth — if the pod dies and reseeds, this skill resumes mid-script from the last answered question.
- **Cross-session memory:** at the end of design, call `memory.put()` with a one-paragraph summary of the chosen layout so future edits in another session can recall what the BA prefers without re-asking.
- **The container's tmpfs is your working memory.** `/workspace/build/ui_design.md` survives only this pod. `/workspace/memory/*.md` (durable, restored on cold start) is for cross-session recall. Use both — scratchpad for the live discussion, memory note for the final summary.

## Safety rules (citations)

- **H-01** — Queue actions default to `plan_then_apply`. Q4/Q5/Q11 narration must surface the confirm step; never propose a "one-click commit" queue button.
- **H-02** — Chat surfaces are structurally read-only. Q7 (chat panel) and Q11 (approvals narration) must never offer "let the agent write from chat" as an option — writes only happen from queue actions.

Refer to [citra-safety-rules](../citra-safety-rules/SKILL.md) for the canonical rule list.

## The four sub-phases

Run them strictly in order. Each one ends with the BA accepting (or rejecting + redirecting) before you proceed.

### Sub-phase 3.0 — Inventory
*Goal: enumerate what the agent does, so you know what surfaces are needed.*

Narrate per [`AGENTS.md`](../../AGENTS.md). Inventory is private (the BA doesn't see your working model), but the narration shows progress is being made:

```
> 📐 Reading the agent spec to figure out which pages and panels we need...
> ✅ Inventory done: 2 forms, 1 queue shape, 1 detail shape, 1 chat candidate
```

Re-read `/workspace/build/agent_spec.json` and write `/workspace/build/ui_design.md` starting with:

```markdown
# UI Design — <slug>

## Inventory
- **Actions:** <list every `actions[].name` from agent_spec, one per line, with its purpose in one phrase>
- **Reads:** <every MCP / RAG data source the agent uses, and what it returns>
- **Forms:** <every `actions[].input_schema` becomes a candidate FormPanel>
- **Lists:** <every queue-shaped output becomes a candidate QueuePanel>
- **Detail views:** <every "per-record" output becomes a candidate DetailPanel>
- **Chats:** <does the agent need free-text Q&A? — candidate AgentChatPanel>
```

Don't show this to the BA. It's your working model.

### Sub-phase 3.1 — Ask, then propose, page by page
*Goal: walk the BA through the design **step by step**, asking a single focused question at each step. Do NOT dump the whole layout at once — that overwhelms the BA and produces lukewarm "looks fine" answers instead of real feedback.*

Narrate the transition into this sub-phase:

```
> 📐 I have a layout in mind — let me walk you through it page by page
```

(The questions themselves are regular prose — no `>` prefix. They're the BA's actual decision points.)

**The opener.** Now that the agent is built, send the BA exactly one paragraph:

> "The agent is ready. Now let's design the screens. I'll walk you through a starting layout one page at a time and ask you to confirm or change it before we move on. We can also add a chat panel, embed dashboards or charts, and set the agent to run automatically on a schedule or webhook. Sound good?"

Then run the **canonical question script** below.

> **Per-page proposal & confirm — the BA decides, you don't guess (AGENTS Hard Rule 18).** First agree the **app skeleton** (the page list) in one line. Then work **one page at a time**, and for that page present its applicable script questions **together as a single proposal**, pre-filled from anything the BA already hinted — *"for the [X] page I'd put: … — build this, or change anything?"* Get an **explicit confirmation for that page before `citra-app-spec` composes it**, then move to the next page. This consolidates per **page** (still **never dump the whole app at once** — that's what produces lukewarm "looks fine" answers) and **supersedes one-question-per-turn** for the proposal. Drip a question out one at a time only for a **complex page** where the BA needs to think — and you **must** ask the specifics a proposal can't assume: a **chart's time window + granularity** (daily-over-a-month vs weekly-over-a-quarter), **what the brief should highlight + any comparison** (week-over-week), a **form's write target + approval + validations**. Still append every answer to `ui_design.md`.

#### Canonical question script (single page at a time)

For each question: state your recommendation in one sentence, then ask the question. Append the BA's answer to `ui_design.md` before moving on.

1. **Multi-page or single-page?**
   > *"I recommend a multi-page layout (separate pages for submit, inbox, and detail). The alternative is one long scrollable page. Which fits how your users work?"*

2. **What's the landing page?**
   > *"I'd make the inbox the landing page so people see existing claims first; submission goes on its own page. Or would you rather land on a 'new claim' page?"*

3. **Should the submission form be on its own page?** (per form panel)
   > *"The claim submission form would be its own page at `/home`. Do you want it there, or folded into the inbox as a side panel?"*

4. **Where does the queue belong?** (per queue panel)
   > *"The claims queue goes on the inbox page. Should it stay there alone, or share that page with KPI tiles / a chart?"*

5. **Detail view: separate page or side panel?** (per detail panel)
   > *"Clicking a row opens a detail page at `/record?id=…`. The alternative is a slide-over panel on the same page. Most BAs prefer a separate page so they can deep-link to a claim. OK?"*

6. **Charts / dashboard tiles — and which page?**
   > *"There's a chart of claim volume that pairs well with the queue. I'd put it on the inbox page next to the queue. Alternatives: its own analytics page, or hidden behind a tab. Where do you want it?"*

7. **Chat panel — global or per page?**
   > *"The agent supports free-form Q&A. I'd make it a floating chat that's available on every page (not tied to one). Alternatives: embed it on the detail page only, or skip it. Which do you want?"*

8. **Trigger automation — should the agent run on its own?**
   > *"Right now the agent triages when you open an item. I can also have it run **ahead of time** — on a schedule, on a webhook event, or by polling for new records — so the recommendation is already waiting in your inbox. Want an automatic trigger, or keep it on-open only?"* (This becomes an `app_spec.triggers[]` entry; it publishes deactivated and you activate it in the Auto-Recommend panel.)

9. **Navigation chrome.**
   > *"With 3+ pages I'd use a sidebar nav on the left. Topbar is fine if you'd prefer something less visual. Sidebar OK?"*

10. **After the user submits, where do they land?**
    > *"After submitting a claim, the user lands on the detail page for the new claim. Alternatives: stay on the form (so they can submit another), or jump to the inbox. Which?"*

11. **Approvals — how does the BA review escalations?** (ask whenever the agent emits a "needs human review" decision, or an app trigger precomputes recommendations)
    > *"Items needing your decision show up in one inbox queue, each with the AI's recommendation. You and any manager you add to the app's service account click Approve, Reject, or Cancel there. We can also send an email notification when something's waiting (the email just says 'open the app to review' — there's no approve-from-email). Do you want email notifications on top of the in-app queue?"*
    >
    > **There is ONE recommendation queue.** Every write is universal-approval: the AI only ever produces a recommendation, and the officer's Approve is the only path to a source write (Reject / Cancel close it without writing). Whether an **app trigger** precomputed the recommendation (eager) or the agent produced it on-demand when the officer opened the item (lazy), it lands in the **same** queue and approves the same way. The BA's experience is identical: review the AI recommendation, click Approve / Reject / Cancel.
    >
    > Record the BA's answer (email yes/no). `citra-app-spec` adds the single review queue panel (a `workflow_staging` data source) and the Approve / Reject / Cancel actions.
    >
    > **Authorization is via SA membership** — the BA, their managers, and anyone the SA admin adds as a member all see and can act on the same queue.
    >
    > **Assignment / reassignment is a separate thing** — if the BA wants "leave it for someone else / route to another officer," that's a separate panel the builder composes, not part of this approval queue. Don't fold it into Approve/Reject/Cancel.
    >
    > **Chat is not an approval surface.** Do not offer or imply an "approve from chat" option (chat surfaces are structurally read-only, rule **H-02**). Approve / Reject / Cancel are queue actions. The question here is only queue-action vs chat-as-narrator (narration of *why* an item is escalated) — never chat-as-write.

**Skip questions that don't apply** (e.g. no scheduled automation → skip Q8; no escalations from the agent → skip Q11). Add more questions only when the agent surfaces something the script doesn't cover — and log the new question to `ui_design.md` so it makes it into the next session's playbook.

#### After the script: write the proposal

Once every applicable question has an answer, write the consolidated `## Proposal — v1` block to `ui_design.md` reflecting **the BA's actual answers**, then send the BA a plain-language summary in 3–6 sentences and ask: *"Locked in?"*. If they say yes, jump to 3.3. If they push back on anything, drop into 3.2.

#### Internal decisions you make from the BA's answers
1. **Single-page or multi-page?** Default = multi-page when more than one of {form, queue, detail} is in scope. Single-page when the app is a one-shot wizard or a single dashboard.
2. **Pages and their purpose.** Typical shapes:
   - **Intake app:** `home` (form) → `inbox` (queue) → `record` (detail, `hide_in_nav: true`).
   - **Triage app:** `inbox` (queue, landing) → `record` (detail) → `analytics` (chart + dashboard).
   - **Triage w/ trigger:** `home` (form + chat) → `inbox` (recommendation queue, fed on-demand + by an app trigger) → `record` (detail + approval).
3. **Per-page panels.** 1–4 per page is normal. The grid layout is the default; pick `tabs` only when panels are alternatives the user toggles between, `stack` when each panel needs the full width (long forms), `split` when comparing two side-by-side (form + preview).
4. **Navigation chrome.** Sidebar for >3 pages. Topbar for ≤3. `none` only when the app drives all transitions via in-form actions.
5. **Global chat?** Set `navigation.show_chat_globally: true` when the agent has free-form Q&A that's useful regardless of which page the BA is on; otherwise embed the `agent_chat` panel in one page.
6. **Queue → detail wiring.** Every queue panel that has a detail-page partner gets a row-click action: `{label: "Open", is_row_click: true, navigate: {page: "<detail-page-id>", params: {id: "{row.id}"}}}`. State the field used as the id explicitly so the BA can confirm.
7. **Post-submit navigation.** Form panels that create something usually navigate to the detail page of the new record on success: `on_submit: {agent_action: "...", navigate: {page: "record", params: {id: "{result.id}"}}}`.

Append to `ui_design.md` after the script is done — this is the consolidated record of what the BA actually agreed to: a `## Q&A — v1` block (one line per answered question, verbatim) followed by a `## Proposal — v1` block (Pages with per-page layout + panels, and a Navigation block). **For the exact shape of that consolidated block, see `references/worked-example.md`** (a filled claims-app sample — reproduce the structure with the BA's real answers).

Then send the BA a tight 3–6 sentence plain-language summary of these pages and ask: *"Locked in, or want to change anything?"* If locked, jump to 3.3. If not, drop into 3.2.

### Composing a UI app with triggers + dashboard + chat

UI apps (`kind="app"`) are the only kind that can host **all three** Citra surfaces (chat + dashboard/charts + automation triggers) in one experience. **When the BA mentions a chat panel, dashboard/KPI tiles, charts, multiple dashboards, or automatic (trigger) runs during the question script, read `references/composing-surfaces.md`** — it covers per-page vs global chat, dashboard tiles/pages (one or many, no cap), and automation = AI triggers + the recommendation inbox. If the BA asks for something unsupported, record it in `requirements_unmet` and say so in one sentence — don't invent.

### Sub-phase 3.2 — Iterate
*Goal: capture and apply BA feedback in tight loops.*

Whenever the BA sends feedback:

1. Append a new `## Feedback — <timestamp>` block to `ui_design.md` with the BA's verbatim message.
2. Decide which of these the feedback maps to (most feedback is one of):
   - **Add a page** — append to Pages, update Navigation.
   - **Remove/merge a page** — strike from Pages; warn if it's the landing page or the target of a navigate action.
   - **Move a panel** — note the move.
   - **Change layout of a page** — grid ↔ stack ↔ split ↔ tabs.
   - **Change a navigation target** — e.g. "after submit go to inbox not detail".
   - **Add a tab/sub-section** — usually a layout switch to `tabs`.
   - **Re-order pages in nav** — change `pages[]` order.
   - **Add row-click behaviour** — set `is_row_click: true` on a queue action.
   - **Add per-row buttons** — append to `queue.actions[]`.
3. Bump the proposal version: `## Proposal — v2`, then re-state ONLY what changed (don't re-paste the full plan).
4. Ask the **one** most important follow-up. Examples:
   - *"Should `record` be reachable from the sidebar too, or only from the inbox row click?"*
   - *"On submit, do you want the user to land on the new claim's detail page, or on the inbox so they can see it in context?"*
   - *"For the analytics page — should the chart filter by the same status filter as the queue, or always show all?"*
5. Stop iterating only when the BA explicitly says *"looks good"* / *"ship it"* / *"go"*. Then move to Sub-phase 3.3.

If the same area gets feedback three times in a row, write a `## Decision log` entry summarising what was tried and what landed, then ask the BA which version they want to keep.

### Sub-phase 3.3 — Freeze and persist memory
*Goal: lock the design, signal `citra-app-spec` to translate, and remember for the future.*

Narrate the freeze + memory write so the BA sees what's persisted across sessions:

```
> 📌 Locking in the design as v3...
> 💾 Saving your layout preferences so future edits remember them...
> ✅ Design frozen — ready to translate to JSON
```

1. Append `## Frozen — v<N>` to `ui_design.md` with the final page list, layouts, navigation, and templates for every navigate target — **plus a `### Design language` block** that `citra-app-spec` must obey verbatim:
   ```markdown
   ### Design language
   Tone: <calm ops | bold exec | dense analyst>       ← see citra-design-taste
   Theme tokens: radius=<soft|sharp|round> density=<comfortable|compact>
     surface=<flat|elevated|glass> mode=<light|dark|auto> chart_palette=<calm|vivid|mono|brand>
   Page icons: <page_id>=<lucide-name>, …              ← every nav page gets one
   Hero pages: <page ids that open with a hero band + their headline metric>
   Badge semantics: <status value>=<green|amber|red|blue|slate>, …  ← app-wide, consistent
   ```
   Propose the design language in sub-phase 3.1 (inferred from the app's tone —
   don't interrogate the BA about radii); the BA can veto like any layout item.
   Company identity (`company_name`/logo/primary) is inherited from the
   ontology — never ask the BA to type the company name.
2. Persist a short memory note for future sessions:
   ```bash
   python -c "from citra_toolkit.scratch import memory; \
     memory.put('ui-design-<slug>', '''
     App: <slug>
     Layout: <single|multi>-page, <N> pages: <p1, p2, p3>
     Nav: <sidebar|topbar|none>, default=<page_id>, global_chat=<true|false>
     Key journeys: <one or two sentences on the primary BA flow>
     Last edited: <ISO date>
     ''')"
   ```
3. Hand off to `citra-app-spec` with a one-line message: *"UI design frozen at /workspace/build/ui_design.md, v<N>. Translate to app_spec.json."*

## Scratchpad layout

`ui_design.md` is structured so re-opening it is enough to resume mid-conversation:

```markdown
# UI Design — <slug>

## Inventory                            ← never changes after 3.0
## Proposal — v1                        ← initial proposal
## Feedback — 2026-05-16T10:14:00Z      ← BA verbatim
## Proposal — v2                        ← delta from v1
## Feedback — 2026-05-16T10:16:00Z
## Proposal — v3
## Decision log                         ← only when a topic loops 3x
## Frozen — v3                          ← final, ready for citra-app-spec
```

Each `Proposal — vN` block can omit unchanged sections. Each `Feedback` block must quote the BA's words so future sessions can audit why things look the way they do.

## Reading from memory (edit flow)

If `SEED_APP_SPEC` is set (edit flow), bootstrap `ui_design.md` from the seed instead of starting blank:

1. Convert the seeded `app_spec.json.pages[]` into the `## Frozen — v0` block.
2. Pull any prior memory note via `memory.get('ui-design-<slug>')` and append a `## Prior design notes` section.
3. Ask the BA *one* question: *"This app currently has <N> pages: <list>. What's changing?"*
4. Proceed from Sub-phase 3.2 with the BA's answer.

## Common mistakes
- ❌ Asking the BA "do you want a multi-page app?" with no proposal attached. Always lead with a concrete first cut.
- ❌ Showing JSON to the BA. The JSON is `citra-app-spec`'s job, not yours.
- ❌ Letting the BA design panels you have no agent action / data source for. If the BA asks for a panel that needs a new tool, fall back to `requirements_unmet` in the AppSpec rather than inventing the tool.
- ❌ Forgetting to set `hide_in_nav: true` on detail pages — they shouldn't clutter the sidebar.
- ❌ Adding the same `agent_chat` panel to every page. If it's everywhere, use `navigation.show_chat_globally: true` instead.

## Output

A single file: `/workspace/build/ui_design.md`. The next skill (`citra-app-spec`) reads this and produces the JSON.
