# Plan: recording the product demos

Status: plan. Nothing recorded yet. Written 2026-08-17.

Three videos — Decision System, Flows, Decks — each showing the product doing
real work against real data, not a slideshow.

---

## The constraint that shapes everything

**The builder takes 20–30 minutes.** Measured today, not estimated: building one
Decision App against a fresh schema ran ~29 minutes and 168 tool calls before it
published. A decision run is 26–130 seconds. A full stack build from scratch is
30–40 minutes.

Nobody watches a 29-minute build. So every demo involving the builder has to
decide, up front, which of these it is:

| approach | good for | cost |
|---|---|---|
| **Cut to the result** — start the build, hard cut, show the published app | honest, short, easy | viewer does not see it work |
| **Time-lapse** — screen-record the whole build, speed to ~60s | shows it is really working | one long take, any failure wastes it |
| **Pre-built replay** — build beforehand, walk the transcript and the result | most controlled | least convincing unless the transcript is shown |

Recommendation: **time-lapse the build once, well**, and reuse that clip. It is
the single most persuasive artefact you have and it only has to survive one
good take.

---

## Video 1 — Decision System

Two cases, as agreed. They answer different objections and should stay separate.

### Case A — the embedded card in someone else's app

**What it proves:** the decision goes to where the work already happens. A bank
does not adopt a new UI; it adds a script tag.

`bank-demo/` is built for exactly this — a pretend bank with three business
lines where **only Loan Origination carries a card**, deliberately. That
restraint is the point and should be said out loud on camera: a decision app
goes where a decision is made, not everywhere.

Shot list:

1. Open the bank's own app. Show Collections and Motor Claims — no Citra.
2. Open Loan Origination. The card is there, inside their page.
3. Open a file. The card recommends, cites the policy clause and the bureau
   pull, and stages the write behind an approval.
4. Show `components/CitraCard.tsx` — load script, `init({ getToken })`,
   `mount({ embed, recordId })`. No npm package, no build step, no shared React.
5. Show `app/api/login/route.ts` and say why it is server-side: the officer's
   password never leaves the bank's server, and the Citra token lands in an
   httpOnly cookie no page script can read.

Point 5 is the one a bank's security reviewer cares about and every competitor
demo skips.

### Case B — build an app in Citra, run it in Citra

**What it proves:** the thing was not hand-built for the demo.

1. Show the data first — the acme-bank catalogue, real tables, real row counts.
2. Describe the app in plain English. Start the build.
3. **Time-lapse.** Show the agent probing datasets, reading the schema,
   validating its spec. Let the tool calls be visible; that is the evidence.
4. The published app opens. Run one decision.
5. Show the citation resolving to the policy library, and the write staged as
   `pending_approval` rather than applied.

If there is time for one more beat, the strongest is the **fleet-ops** case:
the same builder against a maintenance schema it has never seen, proving no
banking logic is baked in. That is the answer to "you built this for banks".

---

## Video 2 — Flows

Later, per the agreed order. Its own README already names the demo:

> *Every morning, pull yesterday's failed payments from Postgres, ask the model
> which are worth retrying, write those to a sheet and email me the rest.*

The three beats the product is built around — draft by describing, debug by
running, ship it — are the video. The debug beat is the differentiator: show a
node failing, then show the raw input and output of every node that ran before
it. Everyone demos the happy path.

Flows is also the cheapest to record: Mongo, Redis and MinIO only. No Milvus,
no MCP, no per-tenant Postgres.

## Video 3 — Decks

Last. The differentiator is not that it makes slides — everything makes slides.
It is that **every figure traces to a source**. So the shot that matters is
clicking a number and landing on the record it came from. Without that beat
this video competes with Gamma and loses.

---

## Before recording anything

Learned from a full clean-room run today; every one of these actually broke.

- **Record from a fresh clone of `main`**, not a working tree. `ds-clean2`
  currently drifts from the published tree by 8 files, and a demo of code that
  is not what viewers can download is worth less than no demo.
- **Build the images first.** PyPI read-timeouts needed retries twice today.
  Never on camera.
- **Build the embed bundle.** `citra-app-runtime/public/v1/` is EMPTY in the
  tree — `citra.js` is a build artefact. Case A cannot start without it, and
  this is the least obvious prerequisite here.
- **Build the sandbox images** (`build-sandboxes.sh`) or the builder cannot
  spawn a pod. `start.sh` only WARNS when they are missing, so the stack looks
  healthy and Case B fails at the first click.
- **Seed and verify BEFORE the take**: 4 published apps, 12,001 loan
  applications, the SOP library returning citations. Check the decision runs
  end to end once, then reset.
- **Have the LLM key in `.env`** including the tier keys. A missing
  `LLM_LARGE_API_KEY` produces a 401 mid-decision.
- **Watch the clock on Milvus.** It is the heaviest container; give it time to
  report healthy before the first take.

## Order

1. **Decision System, Case A** — shortest, most polished, least moving parts.
2. **Decision System, Case B** — needs the time-lapse take.
3. **Flows** — cheapest stack, self-contained story.
4. **Decks** — last, and only worth doing with the traceability beat.

Case A first is deliberate: it is the one that can be recorded today, and
getting one finished video out is worth more than three half-planned ones.
