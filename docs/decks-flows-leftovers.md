<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Leftovers from decks and flows — marked for deletion

Presentations and workflow automation were cut out of this repository into
their own products, [citra-decks](https://github.com/Trustedwear-Tech/citra-decks)
and [citra-flows](https://github.com/Trustedwear-Tech/citra-flows). Some of
their UI stayed behind.

This file records what is still here so it can be removed deliberately, rather
than discovered by a stranger reading the code. Nothing below is scheduled;
it is a list, not a plan.

**One correction to the assumption that prompted this list: the largest item
is not invisible. It is on a live tab.** The rest genuinely is unreachable.

---

## 1. `Citra-UI/components/HowToUseModal.js` — 583 lines, REACHABLE

The in-product help. Every one of its eleven sections belongs to a product
this repository no longer contains:

| Section | Belongs to |
|---|---|
| Welcome to Citra AI — *"The Future of Content Design"*, *"Living Deck"* | decks |
| Getting Started, Teams & Data Stores | decks (and Teams is deprecated) |
| Productivity Suite, Research Tools, Meetings & Audio | the retired consumer product |
| Agent Builder, Node Types & Categories, How Data Flows, Connections & Secrets, Workflow Examples | flows |

Not one section covers Decision Apps, the ontology, the decision loop, or
judgement memory. The help for a decision system documents decks and flows.

It is wired end to end and a user can open it today:

```
RibbonMenu.js:122   { id: 'help', label: 'Help', … }        ← tab is live
                    (the tabs either side of it are commented out; this one is not)
RibbonMenu.js:298   <RibbonButton label="How to use" onPress={onShowHowToUse} />
App.js:8214         handleShowHowToUse → setShowHowToUseModal(true)
App.js:15246        <HowToUseModal … />
```

**Two ways out.** Removing the entry point is a few lines — drop the `help`
tab entry and the `onShowHowToUse` prop, and the modal becomes dead code that
can be deleted with the rest. Writing real help for this product is a
different piece of work. Shipping help for the wrong product is worse than
shipping none, so the entry point should go first whichever way it ends.

## 2. `Citra-UI/components/ProductTour/TourSteps.js` — 249 lines

The guided tour, anchored to `research-tab`, `productivity-tab` and the other
ribbon tabs that are now commented out. Reached from `TourButton`, which sits
in the same `help` tab as the modal above.

## 3. `Citra-UI/components/ui/RibbonMenu.js` — 579 lines

Not itself a leftover, but it carries the two entry points above and a block
of commented-out tabs (`pages`, `connection`, `customize`, …) from the older
product. The commented tabs can go; the file stays.

## 4. `Citra-UI/App.js` — the "Data Store" vocabulary

"Data Store" appears across sixteen files. It is the decks-era name for what
this product calls a source or a dataset. Renaming is cosmetic and risky at
this size; it is listed so nobody assumes it is a distinct concept.

## 5. `Citra-UI/dist/`

A built bundle carrying all of the above. Untracked and gitignored — it is a
local build artifact, not a leftover to delete. Listed only because it shows
up in every grep for these terms and wastes the next person's time.

---

## Already cleaned

Recorded so the same ground is not re-searched:

- **`MobileIntroScreen.js`** — was still selling the consumer memory vault
  ("Your Private AI-Searchable Citra Vault", slides for lawyers and doctors)
  to every phone visitor. All eight carousel slides rewritten, both videos
  removed.
- **`SignUpScreen.js`** — the sign-in popup read *"Citra is your intelligent
  partner for content creation"* and *"upload and manage vault data"*. The
  landing page and the screen one click later described different products.
- **`IntroScreen.js`** — `FeatureShowcase`, `DiagramPreview`,
  `MediaPlaceholder` and `SocialProofSection` are unreachable and mention
  diagrams and mindmaps; `SocialProofSection` also carried fabricated metrics
  and was deleted. The remaining three are in the dead-code list, not this one.
