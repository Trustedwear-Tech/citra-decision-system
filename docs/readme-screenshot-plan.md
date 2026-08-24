# Plan: six screenshots for the README

Status: plan. Nothing captured yet. Written 2026-08-17.

The README is 1,291 lines and carries one image — a banner. It does not have an
explanation problem; it has a **proof** problem. Six images fix that, each
placed beside the claim it proves rather than gathered into a hero gallery.

Screenshots, not video. A GitHub reader is scanning to decide whether to keep
reading, and video asks them to commit before they have decided. Video belongs
on the landing page and in a sales call, linked from here in one line.

---

## The six

| # | Claim | Surface | Ready? |
|---|---|---|---|
| 1 | describe it in English, the builder builds it | `:8081` Decision Apps | needs sandbox images |
| 2 | grounded — cites your records | `:8081` loan-application-triage | ready |
| 3 | governed — cannot go off-script | same run, write panel | ready |
| 4 | goes where the decision is made | `:4300` bank-demo | needs the embed bundle |
| 5 | learns human judgement | Memory → clause → provenance | **needs data — see §5** |
| 6 | your data, your infrastructure | authored SVG | authored, not captured |

### 1 — the builder mid-run

Frame the **tool calls**, not the prompt box. A build observed on 2026-08-17 ran
168 of them — probing datasets, reading schemas, validating its spec. That
visible work is what separates this from a box that emits code. Capture 60–90s
in, once several tool results have landed.

Prerequisite: `scripts/quickstart/build-sandboxes.sh`. `start.sh` only WARNS
when those images are missing, so the stack reports healthy and the builder
fails at the first click.

### 2 — a decision with citations

`loan-application-triage`, case `LAN-NEEDLE-001`. Observed output: *"Reject —
income not corroborated and FOIR above cap"* with 5 citations naming **Income
Verification SOP** and **Retail Credit Policy clause**.

Frame the recommendation and the citations **together**. The citation is the
claim; separated, it is just a chat reply.

### 3 — the approval gate

Same run, different crop: `status: pending_approval`, with
`record_credit_decision` **staged and not applied**.

This is the most important image in the set for a regulated buyer, and the one
competitor demos do not have. It shows the AI proposing a write and the system
declining to make it unilaterally.

### 4 — the card inside the bank's own app

`bank-demo` on `:4300`. Capture **two images side by side**: Collections (no
card) and Loan Origination (card). The contrast makes a point one image cannot
— a decision app goes where a decision is made, not everywhere.

Prerequisite: the embed bundle. `citra-app-runtime/public/v1/` is EMPTY in the
tree because `citra.js` is a build artefact. This is the least obvious blocker
in the set.

### 5 — a learned clause with its provenance

**Not capturable from the demo seed as it stands.** See
`docs/seed-learned-clause-plan.md` — the seed publishes apps and SOPs but no
learned judgement, so `smartapp_clauses` is empty. This is the shot that proves
the core differentiator, so the data for it is a task in its own right rather
than something to improvise on the day.

### 6 — architecture

Author as SVG, not a capture: it is a diagram, it should stay sharp at any size
and diff as text. Follow the existing `banner-dark.svg` / `banner-light.svg`
pattern with `<picture>` so it works in both GitHub themes.

---

## Conventions

- **PNG for UI, SVG for diagrams.** Screenshots do not vectorise; diagrams
  should not rasterise.
- **Theme-aware where it matters.** The banner already ships light and dark;
  diagrams should too. UI screenshots can pick one theme and stay consistent.
- **Crop tight.** A full browser window with tabs and a bookmarks bar wastes the
  reader's attention on chrome. Crop to the panel that carries the claim.
- **Real data, never lorem.** Every number visible should be one the stack
  actually produced — 12,001 loan applications, the real citation titles. A
  reader who spots invented data stops believing the rest.
- **Redact nothing that matters, and nothing that does not.** Demo data is
  synthetic; leave it legible.

## Also worth doing

`citra-flows` and `citra-decks` have **zero** images between them.

- **Flows** — one animated GIF of the canvas assembling from a prompt. That
  product is inherently visual and its story is ten seconds long.
- **Decks** — one screenshot: a figure on a slide, and the source record it
  traces to. That is the entire differentiator; without it the README competes
  with Gamma on adjectives.
