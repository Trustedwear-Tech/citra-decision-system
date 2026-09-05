<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Docs site plan — slim the README, publish docs.citra-ai.com

Status: **plan, not built.** Decisions below are settled; the work is not started.

The model is [openclaw/openclaw](https://github.com/openclaw/openclaw): a README
that fits on one screen, and everything else on a docs site the README links to.

**Decided**
- Site lives at **docs.citra-ai.com**, built from this repo, published to a
  `gh-pages` branch by CI. Source of truth stays next to the code it describes.
- The internal plan documents **stay in the repo** but never render on the
  site — they move to `docs/internal/` and are excluded from the build.
- ~~**The README stays long.**~~ **Superseded 2026-08-25** — the README is
  now 228 lines and the detail lives on the GitHub wiki, sourced from
  `wiki/` in this repo. See the section directly below.

---

## Reversed again (2026-08-25): the first screen was fixed, the order was not

The section below argued the README should stay long, and it was right at the
time. Its reasoning turned on one measurement: *"951 lines, and **zero**
visual signal above the fold."* A thin README over a bare top would have read
as a weekend project, so the header was built and the body left alone.

**That premise no longer holds.** The first screen now carries a banner,
five badges, a link row, a contents block and a hero diagram. The credibility
problem that argument solved is solved.

What was left was a different problem, and measuring it is what settled it:

| | then | now |
|---|---|---|
| README | 1,627 lines · 12,416 words · ~62 min | 228 lines · ~6 min |
| "build an app, an API, an embedded UI" | line 1443 — **88% down** | line 87 — **38%** |
| the experiment | 131 lines, near the top | 6 lines and a link |
| Quickstart | 406 lines — 25% of the page | a 3-command block |

The product got ten lines; the experiment got a hundred and thirty-one and sat
first. A reader who stopped after two screens — most of them — saw a research
write-up, which is exactly what the page was not supposed to be.

So the fix was **order and depth, not deletion**. What the section below
correctly insisted on is kept: the argument, the concepts and the architecture
still exist in full and are still linked from where an evaluator lands — they
now live on the wiki, one click away, rather than inline.

And the hard rule below is honoured. **Do not link to pages that do not
exist**: all twelve wiki pages were written and their links verified — README
to wiki, and wiki to wiki — before anything was published. The wiki source
lives in `wiki/` in this repo and reaches GitHub only via
`scripts/sync-wiki.sh`, so a docs change is reviewed in the same pull request
as the code change rather than typed into a browser.

---

## Reversed: the README is not the problem, its first screen was

The first version of this plan said to cut the README to ~90 lines and push
everything to the docs site, modelled on OpenClaw. That was wrong, and the
reason is worth writing down so nobody re-proposes it.

OpenClaw can afford a 50-line README because its first screen carries a banner,
five badges — including **discord 20k online** — a link row, and a contributor
grid. Social proof does the credibility work, so the prose does not have to.
This project has none of that visible yet. A thin README with a bare top would
have read as a weekend project.

Measured before the fix: 951 lines, and **zero** visual signal above the fold —
no banner, no badges, no image, no contents. The three options were not equal:

| | First screen | Depth | Reads as |
|---|---|---|---|
| Before | bare text | 951 lines | serious but unedited; nobody scrolls past screen 2 |
| Original plan | bare text | moved to docs | a weekend project |
| **Done instead** | banner · badges · link row · contents | kept inline | a substantial product |

**Perception is set by the first two screens, not by total line count.** A long
README with a banner and a contents block reads as depth; the same words
without them read as sprawl.

So the header was built and the body left alone: `assets/banner-{light,dark}.svg`
(rendered to PNG so the typography does not depend on the viewer's installed
fonts), honest badges only — CI, Apache-2.0, compose, open weights, Discord — a
link row, and a collapsible contents block. No stars or downloads: they would
show zero and cost more than they earn.

### What still moves to the docs site, eventually

Only genuine reference material, and only once the site exists: `Configuration`,
`Troubleshooting`, and the per-component guides. **Not** the argument, the
concepts, or the architecture walkthrough — those are what convince a technical
evaluator, and they belong where the evaluator already is.

The split still worth holding to: **the website answers *why*, the docs answer
*how*, the README proves it is real.**

And a hard rule learned here: **do not link to a docs site that does not exist.**
Dead links read worse than a long page. The link row above points only at
in-repo anchors and the live website; all 16 were validated against real
headings.

---

## Site information architecture

```
start/          getting-started · quickstart · the-demo · troubleshooting
concepts/       ontology · governed-writes · data-catalogue · fraud-screening
                locale-packs · learned-judgement
build/          point-it-at-your-data · authoring-sources-json · write-actions
                fraud-setup · publishing-an-app
architecture/   overview · services · mcp-is-file-defined · decision-path
                memory-and-learning
integrate/      decision-api · embeddable-ui · openapi
operate/        deployment · configuration · access-control · llm-governance
                monitoring
about/          evidence · vision · roadmap · license
```

`concepts/` is a near-lift of the README's "core concepts" section, which was
written for exactly this purpose. `build/` is the expanded "Pointing it at your
own data" walkthrough.

### Existing docs that map straight in

`change-the-demo` · `deployment-guide` · `access-control` · `llm-governance` ·
`write-actions` · `embed-integration-guide` · `reference-architecture` ·
`smart-app-architecture` · `smart-app-grounding` · `data-sovereignty-and-model-hosting` ·
`memory-sizing-and-retention` · `fraud-detection-coverage-matrix` ·
`test-strategy` · `components/*`

### Everything matching `*plan*.md` does not

28 pre-existing files, 29 counting this one. They carry roadmap, unbuilt
features, internal reasoning and customer names. They are useful to a contributor reading the repo and actively harmful on
a public site, where a reader cannot tell a shipped feature from a proposal.
Move to `docs/internal/`, exclude from the build, leave the content alone.

---

## Tooling

**MkDocs Material.** One `mkdocs.yml`; Mermaid, search and navigation built in;
versioning via `mike`; static HTML out, so it deploys anywhere — which matters
for a project whose argument is sovereignty. The repo is Python-heavy, so
contributors already have the toolchain.

Docusaurus is the alternative if React components or first-class versioned docs
become a requirement. Heavier; not needed yet.

Deploy: GitHub Actions on merge to `main` → build → publish to `gh-pages` →
custom domain `docs.citra-ai.com` (CNAME + DNS).

---

## Diagrams

Authored as **Mermaid in markdown**, so one source renders on GitHub and on the
site. Six earn their place:

1. **The decision path** — case in, recommendation out, approve, ledger.
2. **The governed write** — sequence diagram: model emits
   `{dataset_id, action_id, payload}` → MCP re-checks → bound parameters →
   staged → approved → replayed. This is the differentiator and deserves the
   most care.
3. **Ontology → MCP → discovery → catalogue → builder.**
4. **The learning loop** — correction → cluster → clause → next case.
5. **Service map** — what talks to what.
6. **Deployment topology** — the compose stack.

Separately, and for free: **request DeepWiki indexing** at
`deepwiki.com/Trustedwear-Tech/citra-decision-system`. It auto-generates the
code-level architecture wiki from the repo and refreshes on re-index, so it
covers the "how is this actually wired" question without anyone drawing it.
Link it from the README row.

---

## Sequence

**Phase 1 — sort, no new tooling.** Create `docs/internal/`, move the 28 plan
docs, fix cross-links. Nothing published yet; the repo is tidier either way.

**Phase 2 — the README header. DONE.** Banner (light and dark, SVG source
rendered to PNG), badges, link row, collapsible contents. The body was
deliberately left at full length; see the reversal above. Links point only at
in-repo anchors and the live website, never at the unbuilt docs site.

**Phase 3 — stand the site up.** `mkdocs.yml`, nav, theme, the Actions workflow,
DNS. Publish `start/`, `concepts/`, `build/` first — the pages a new reader
needs. Redirect nothing; there is no old site.

**Phase 4 — fill in.** `architecture/`, `integrate/`, `operate/`, `about/`,
migrating the existing docs listed above and rewriting the ones that were
written as internal notes.

**Phase 5 — diagrams**, and request DeepWiki indexing.

Phases 1 and 5 are independent of the rest and can move at any time.

---

## Open items

- **Banner artwork.** `Citra-UI/assets/citra-logo.png` and
  `Citra-UI/components/brand/OpsMark.js` exist; a wide banner (OpenClaw's is
  roughly 2:1) does not. Needs designing, in light and dark.
- **VISION.md** does not exist. The README's problem narrative is most of it
  already and could move there rather than to the website.
- **A public/private review gate.** Before the first publish, someone reads
  every page that will render for customer names, unshipped-feature claims and
  internal reasoning. This is the one step that should not be skipped.
