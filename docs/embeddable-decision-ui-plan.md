<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Embeddable Decision UI — plan

**Status:** Phases 0–5 built (§11–§14) and exercised against a real stack (§16),
which caught a CORS defect that would have broken every integration. **NOT
shippable yet** — the data plane is still unproven; see §15. Nothing is deployed; everything is local and
uncommitted.
**Owner:** Rohit
**Last updated:** 2026-07-30

---

## 1. What we are building

A bank drops a Decision App into a screen they already have. Their developer writes
plain JavaScript — no framework, no build step, no npm install:

```html
<div id="citra-decision"></div>

<script src="https://citra.theirbank.internal/v1/citra.js"></script>
<script>
  const citra = Citra.init({ getToken: () => myApp.citraToken() });

  citra.mount('#citra-decision', {
    embed:    'loan-application-triage',
    recordId: currentApplication.id,
    theme:    { primary: '#0b5fff', font: 'Inter', radius: 6, density: 'compact' },
    onDecision: (d) => {
      // { caseId, action: 'reject', reason: 'revenue_vs_tax_mismatch',
      //   reasonText: 'Revenue vs tax filing mismatch', recommendationId, correlationId }
      refreshCaseHeader(d);   // their own screen reacts
    }
  });
</script>
```

The developer gets that snippet by exporting from the **My Apps** card, the same
place an app's URL is surfaced today. The export is performed by the runtime (§7).

What renders is the **whole card** — recommendation, evidence and clauses, per-item
accept/reject on documents and images, the proposed writes with editable fields, and
the final approve/reject with reason capture. A decision API without the reason
capture is worth very little: the officer's *why* is what the memory layer runs on,
and owning that surface is the point of shipping UI at all.

### Why this matters commercially

The first objection to any API sale is "how long will my team take to integrate?"
Headless means their developers build a case view, a recommendation display, an
accept/reject flow and a reason field — weeks on someone else's roadmap. This turns
that into an afternoon, and it guarantees the reason capture exists, which a
customer-built UI would cut first.

---

## 2. Nothing is hardcoded — the builder composes the embed

**This is the governing constraint.** There is no "Citra decision card" design baked
into the bundle. The embed renders whatever the builder composed, exactly as the
Triage pop-up does today.

The composition is real and already in the spec. Page-level panels
(`PanelRenderer.tsx:129–163`):

```
form · queue · detail · dashboard · hero · stat_strip · timeline · chart
agent_chat · document_view · markdown · notice · calendar · map · filter_bar · notifications
```

…and a `detail` panel is itself composed of sections (`PanelRenderer.tsx:3541–3654`):

```
fields · attachment · markdown · documents · agent_timeline · approval · agent_chat · comments
```

So the Triage pop-up an officer sees is a `detail` composition: `fields` for the
record, `documents`/`attachment` carrying the per-item accept/reject, `agent_timeline`
for the reasoning, `approval` for the final accept/reject with the reason-code picker,
`comments` for the audit conversation. **All of it builder-authored, none of it
hardcoded.** The embed is the same composition rendered somewhere else.

This is why the embed must reuse `PanelRenderer` rather than get a purpose-built
card — see §3.

---

## 3. The framework question — answered

**Concern:** the runtime is React; bank developers need plain JavaScript that drops
into any web stack (Angular, Vue, JSP, .NET Razor, jQuery, plain HTML).

**Answer: React is an implementation detail inside the bundle. The bank never sees
it.** `citra.js` is a self-contained IIFE that ships its own renderer, mounts into a
shadow root, and exposes an imperative plain-JS API. The host page needs no bundler,
no React, and no awareness React exists. This is the Stripe Elements model: their
internals are their business; the developer writes `stripe.elements()`.

Nothing in the host stack conflicts — the shadow root blocks style collisions in both
directions and React's DOM sits entirely inside it.

### Options considered

| Option | Verdict |
|---|---|
| **A. Bundle React 18** | **CHOSEN and shipped.** 108.3 KB gzip, 9/9 checks. What the renderer is developed and tested against daily. |
| B. Preact + `preact/compat` | Measured in Phase 1 at 73.4 KB gzip, also 9/9 — then **removed**. 35 KB does not justify a second rendering library whose divergences would surface in whichever panel is written next. See §11. |
| C. Rewrite the card in vanilla JS / web components | Rejected. Contradicts §2 — the design is builder-authored, so there is no fixed card to rewrite. It would also fork the renderer, and the fork drifts. |
| D. iframe from our origin + `postMessage` | Rejected as default: theming fidelity suffers, height needs constant negotiation, and it reads as a foreign panel — the thing enterprise UX teams block. Kept as a documented escape hatch (§10) for banks whose policy forbids third-party JS in the page. |

### Bundle weight — corrected

Excluding charts and maps changes the picture substantially.

- **Leaflet is already code-split** — `LeafletMap` is behind `dynamic()`
  (`PanelRenderer.tsx:4928`), so a build without the `map` panel never fetches it.
  No work needed.
- **echarts is pulled in by THREE *static* imports**, so tree-shaking will not drop it:
  - `PanelRenderer.tsx:20` → `ReactECharts`, used by the `chart` panel (line 4548)
  - `PanelRenderer.tsx:10` → `KpiSparkline`, which statically imports
    `echarts-for-react` (`KpiSparkline.tsx:6`), used by KPI rendering at line 4468
  - `src/lib/executiveTheme.ts:15` → `import * as echarts` for one
    `registerTheme()` call, in a module that also exports the number and currency
    formatters ordinary panels use. **Found by the Phase 1 build's verification
    step, not by reading the code** — which is the argument for verifying the
    exclusion against the module graph rather than trusting the alias list.

  **Exclude it with a build-time alias in the embed build, NOT by making these
  imports lazy.** See below — this is a deliberate reversal of the obvious approach.

#### Why alias instead of lazy boundaries

Converting those two imports to `React.lazy`/`dynamic` would change the *shared*
renderer, which means changing how charts behave in dashboards and apps that work
today. Three findings make that a bad trade:

- `PanelRenderer` is `"use client"`, so charts are already client-rendered — the lazy
  conversion buys the existing surfaces nothing.
- `KpiProgress` (`KpiSparkline.tsx:86`) is **pure CSS/DOM with no echarts**. A
  module-level lazy boundary would defer it for no reason.
- **`e2e/runtime.spec.ts` contains no chart or sparkline assertions.** There is no
  automated safety net that would catch a chart regression, so a change to the shared
  path would be verified by hand or not at all.

The alias approach instead leaves the runtime build **byte-identical**:

```
// embed build only
resolve.alias['echarts-for-react'] = './embed/stubs/echarts.tsx'
```

- **Runtime / app / dashboard build:** unchanged. No code edit, therefore no risk.
- **Embed build:** aliases the *package*, so it catches both static import sites at
  once — `PanelRenderer.tsx:20` and the one inside `KpiSparkline`.
- The stub renders the loud "panel not supported in this surface" failure, which is
  the behaviour the allowlist wants anyway (§4). Belt and braces: the allowlist
  should already have rejected the panel at publish time.

Phase 1 must **verify** the alias actually drops echarts — grep the built bundle and
check the size, rather than trusting the config.

With echarts out, the renderer plus CSS plus React is the whole bundle. That also
makes Preact's saving proportionally material rather than noise, which is why B moves
from "later optimisation" to "evaluate in Phase 1". All figures are estimates to
confirm against a real build.

---

## 4. Architecture — one renderer, three mounts

```
                    AppSpec in Mongo (builder output)
                                 │
                    ┌────────────┴────────────┐
                    │      PanelRenderer      │
                    └────────────┬────────────┘
          ┌──────────────────────┼──────────────────────┐
   Citra-UI shell         app-runtime page        citra.js embed
   (our product)          (our product)          (customer's page)
```

**The load-bearing decision in this plan.** A copied card drifts, and the
reason-capture flow would be the first casualty. One renderer, three mounts.

### How portable is PanelRenderer, really?

It is 4,976 lines, which sounds fatal. It is not. Its entire Next.js coupling is
**two imports, four symbols**:

```
src/components/PanelRenderer.tsx:5  import { useRouter, usePathname, useSearchParams } from "next/navigation";
src/components/PanelRenderer.tsx:6  import dynamic from "next/dynamic";
```

| Symbol | Embed shim |
|---|---|
| `useRouter` | no-op push/replace — an embed has no URL to own |
| `usePathname` / `useSearchParams` | read from mount options; the host owns the URL |
| `dynamic` | `React.lazy` + `<Suspense>` |

The API layer is a **single chokepoint**: `lib/runtimeFetch.ts` is the mandated path
for every browser→API call. The embed swaps that one file — absolute URL to the Citra
origin instead of same-origin `/api`, and the token from `getToken()` instead of the
`?_t=` URL param.

Styling already fits: `globals.css` is 2,033 lines of **pure `--citra-*` custom
properties with no Tailwind**, and per-app theming already works by overriding them.
`theme: { primary, accent, font, radius, density }` maps onto variables that exist
today, and the CSS inlines into the shadow root unchanged.

### Panel allowlist — it is a blocklist of two, and it needs no builder change

The spike settled this, and the answer is much smaller than expected. Measured
against a real published app rather than reasoned about:

| Panel | In an embed | Evidence |
|---|---|---|
| `chart` | **Cannot render** — echarts is aliased out | Shows the loud notice; proven by test |
| `map` | **Cannot render** — leaflet is aliased out | Same path |
| `stat_strip`, `dashboard` | **Work.** Only the *optional* sparkline degrades | `KpiSparkline` is conditional on `kpi.spark.length > 2` (`PanelRenderer.tsx:4467`); the value, target bar and label are plain DOM |
| `queue`, `detail` (+ all sections) | **Work**, with live data | Rendered in the spike |
| `form`, `markdown`, `notice`, `timeline`, `document_view`, `agent_chat`, `filter_bar`, `notifications`, `hero` | **Work** — no excluded dependency | Nothing in their import path is aliased |

So there is no technical case for a restrictive allowlist. Two panels genuinely
cannot render, and **both already fail loud at render time** — a visible "this panel
can't be shown here" plus a named console error, shipped and tested in Phase 1.

### The guidance is a SKILL, not a validation rule

More importantly, validation is the wrong mechanism entirely. The builder is an LLM
agent steered by **skills**, and this platform already has the exact precedent:

- There is **no `dashboard` build kind**. A dashboard is an app whose primary page is
  `page.kind="dashboard"`, signalled by `BUILD_PRIMARY_PAGE_KIND=dashboard`, and what
  the builder should *do* about that lives in the `citra-dashboard-spec` skill plus a
  "Hard rule for a dashboard page" block in `builder-workspace/AGENTS.md`.
- That block is pure guidance — *"on a dashboard page the builder authors only
  KPI/chart/markdown panels; queues/forms/detail belong on standard pages"* — and it
  is enforced by the builder understanding it, not by a validator rejecting it.

An embed page is the same shape of thing. **We tell the builder what an embed surface
is; it decides which panels serve a recommendation card.** That is both less code and
a better fit: a rule can only say no, whereas a skill can say *what good looks like*.

### The surface question already exists — embed is its third answer

`AGENTS.md` already makes the builder ask, every build:

> *"(a) a **Decision App** with a Citra UI … or (b) a **headless Decision API** — no
> UI at all, just `/run` + `/approve` that you plug into your own front-end?"*

Headless is already built (`BUILD_HEADLESS=true`, `"headless": true`, no panels).
**The embed is the missing middle option**: their front-end, but they don't build the
UI. So this is not a new concept bolted on — it completes a choice the builder is
already offering, and the plan's own §1 ladder finally matches what the product asks.

---

## 5. Public JavaScript API

```js
Citra.init(options) → CitraInstance
```

| Option | Type | Notes |
|---|---|---|
| `getToken` | `() => string \| Promise<string>` | **Preferred.** Called before each request; lets the host refresh without remounting. |
| `clientToken` | `string` | Simpler alternative. Static, so it dies with the token. |
| `baseUrl` | `string` | Defaults to the origin `citra.js` was served from. |
| `onError` | `(err) => void` | Instance-wide error sink. |

```js
citra.mount(selectorOrElement, options) → CitraMount
```

| Option | Type | Notes |
|---|---|---|
| `embed` | `string` | Builder-published embed id. **Required.** |
| `recordId` | `string` | The record the decision is about. **Required.** |
| `theme` | `{ primary, accent, font, radius, density }` | Maps onto `--citra-*`. |
| `onDecision` | `(d) => void` | Fires after a committed decision. |
| `onItemDecision` | `(d) => void` | Fires on a per-item accept/reject (documents, images). |
| `onRecommendation` | `(r) => void` | Fires when the recommendation renders. |
| `onError` | `(err) => void` | Per-mount. |

```js
mount.update({ recordId })   // re-target without remounting (list → detail navigation)
mount.refresh()              // re-run
mount.destroy()              // unmount, remove shadow root, drop listeners
citra.destroy()              // tear down every mount
```

`onDecision` payload:

```js
{ caseId, recordId, action: 'approve' | 'reject' | 'cancel',
  reason: 'revenue_vs_tax_mismatch',        // reason CODE from case_signature
  reasonText: 'Revenue vs tax filing mismatch',
  recommendationId, correlationId, appliedWrites: [...] }
```

Multiple independent mounts per page are supported; each owns its own shadow root.

---

## 6. Auth and record identity

Settled, and unchanged by this plan:

1. The bank's app runs OIDC/PKCE against **their own** IdP and POSTs the ID token to
   `POST /api/auth/oidc`, receiving a Citra JWT. No second login.
2. That JWT is what `getToken()` returns.
3. The embed sends `recordId` + the JWT to `POST /apps/{slug}/run`, then
   `POST /apps/{slug}/run/{correlation_id}/approve`. Both endpoints exist and are what
   the acme-bank demo already runs on.

**Row-level access is enforced at the dept-MCP by the officer's departments** — an
officer who names a record outside their scope gets nothing back. This is the
mechanism Phase 0 exists to feed: without group→dept mapping every SSO officer lands
with `dept_ids: []` and sees nothing at all.

Token TTL stays at the deployment default (7d) — both surfaces are internal to the
bank. A short-TTL flavour is cheap if a customer asks (`tokenService.generateToken`
already accepts `opts.ttlSeconds`, `tokenService.js:87`) but is not on the critical
path.

---

## 7. Export, hosting and versioning

### There is no per-app build — not today, not for embeds

Worth stating plainly because it is easy to assume otherwise: **the runtime does not
compile anything per app.** `src/app/[slug]/[[...pagePath]]/page.tsx` is
`export const dynamic = "force-dynamic"` and calls `fetchAppDetail(slug)` at request
time. The AppSpec is **data** in Mongo; `PanelRenderer` interprets it in the browser.
Publishing an app compiles nothing — it promotes a document.

The embed inherits this exactly:

| | App / Dashboard (today) | Embed (proposed) |
|---|---|---|
| Compiled artefact | runtime container image | `citra.js` |
| Compiled how often | **once, in CI** | **once, in CI** |
| Per-app artefact | none — the spec is data | none — the spec is data |
| On publish | spec promoted test→prod | spec promoted test→prod |
| BA receives | a URL | a URL + a snippet |
| Charts stripped | n/a | **once, in CI** (the alias) — not per app |

So "export" copies **~10 lines of text** with the embed id and base URL filled in. It
is instant, and it involves no build system. Downloading that snippet as a file is
fine; generating a bespoke per-app JS bundle is not (see below).

### Where the binding lives

The binding between script and app spec is a **parameter in the snippet**, not
something compiled into the bundle:

```js
citra.mount('#citra-decision', {
  embed: 'loan-application-triage',   // ← the binding
  recordId: currentApplication.id
});
```

Three layers, and only the middle one is per-app:

| Layer | Per-app? | Produced by |
|---|---|---|
| `citra.js` | No — identical for every app | our CI, once |
| The snippet (`embed` id + `baseUrl`) | **Yes** | the Export action on the My Apps card |
| The spec | Yes | fetched at mount using that id |

**The developer never looks anything up.** They do not open the app card, read a spec
id and transcribe it — the Export action emits the snippet with the id already
filled in. Copy, paste, done. The alternative (baking the spec into a per-app bundle)
is what §"The export is a publish" rejects.

### Two build targets, one source tree

`citra.js` is **not** built by Next.js — Next cannot emit a standalone IIFE for
third-party pages. It is a second build target over the same components:

| Target | Tool | Output | echarts |
|---|---|---|---|
| Runtime app | `next build` | the existing app | real (unchanged) |
| Embed | esbuild / rollup | `citra.js` IIFE | aliased to a stub |

Both run in CI and both ship inside the runtime container image, which then serves
`/v1/citra.js` as a static asset. The Next.js server only *serves* the file; it does
not generate it.

### The export is a publish, not a code generator

Decision: **one generic, versioned bundle for every app; the export publishes the
app's embed spec and returns a prefilled snippet.**

The alternative — generating a bespoke `citra-loan-triage.js` per app — fails two
ways, and both are expensive to discover late:

- **Patching.** A security fix would require re-exporting and re-deploying every
  customer app, coordinated with every bank's release cycle.
- **Staleness.** A BA's change in the builder would not reach the bank until a
  developer re-pasted the script.

With a generic bundle the spec is fetched at mount, so builder changes propagate the
moment they are promoted, and a bundle fix ships once.

### Surfaces the runtime owns

| Route | Purpose |
|---|---|
| `GET /v1/citra.js` | Stable major-version pointer, short cache TTL |
| `GET /v1/citra-1.4.2.js` | Immutable build, long cache — banks that pin, pin here |
| `GET /api/embed/{embedId}/spec` | The published embed spec |
| `POST /api/apps/{slug}/embed/export` | Publishes the embed + returns the snippet for **My Apps** |

Breaking changes mint `/v2/`; `v1` keeps working. Served by the runtime container
behind Traefik as static assets, from the customer's own deployment — their example
URL, `https://citra.theirbank.internal/v1/citra.js`, is right. In a single-tenant
private cloud the script should not come from a vendor CDN: it avoids a CSP argument,
avoids a cross-origin dependency in their change-control review, and keeps
availability in their hands.

### Implementation — hosting the script

The runtime is `output: "standalone"` with no `middleware.ts` and no `public/`
directory in the repo (the Dockerfile creates an empty one and copies it into the
runner stage). That shapes the work into four small, separable pieces.

**1. Second build target → `public/v1/`**

```jsonc
// package.json
"scripts": {
  "build:embed": "node scripts/build-embed.mjs",   // esbuild → public/v1/
  "build":       "next build"
}
```

`scripts/build-embed.mjs` runs esbuild with `format: 'iife'`, `globalName: 'Citra'`,
the `echarts-for-react` alias (§3), and the version from `package.json`. It emits
both files:

```
public/v1/citra-<version>.js   ← immutable
public/v1/citra.js             ← same bytes, the stable pointer
```

**2. Dockerfile — one line**

```dockerfile
RUN npm run build:embed && npm run build
```

`public/` is not part of Next's standalone trace, which is exactly why the Dockerfile
already has `COPY --from=builder /app/public ./public`. That existing line ships the
bundle with no further change.

**3. Cache headers → `next.config.js`**

```js
async headers() {
  return [
    { source: "/v1/citra.js",
      headers: [{ key: "Cache-Control", value: "public, max-age=300" }] },
    { source: "/v1/citra-:version.js",
      headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }] },
  ];
}
```

**4. CORS → a new `src/middleware.ts`**

Necessary, not optional: the embed POSTs to the runtime's `/api/*` routes with
`Authorization` and `Content-Type: application/json`, which triggers a preflight.
`headers()` in `next.config.js` sets response headers but does **not** answer an
`OPTIONS` request, and Next route handlers do not do so automatically. One middleware
covers all 13 API routes; editing each route file would drift.

```
OPTIONS /api/*  → 204 + CORS headers
any     /api/*  → append Access-Control-Allow-Origin: *
                            Access-Control-Allow-Headers: Authorization, Content-Type
                            Access-Control-Allow-Methods: GET, POST, OPTIONS
```

Allow-all is safe here because auth is a bearer token in a header rather than a
cookie — the wildcard is only illegal on credentialed requests, and it grants nothing
without a valid officer JWT.

### Implementation — generating the snippet

New runtime endpoint:

```
GET /api/embed/{slug}/snippet
→ { embedId, scriptUrl, version, snippet }
```

The **runtime** builds this string, not Citra-UI, because only the runtime knows all
three inputs: its own public base URL, the version of the bundle it is currently
serving, and whether the app actually has an embed page published. Templating it in
the UI would let all three drift from reality.

- **Validation is the point.** 404 when the slug is unknown; **409 when the app has
  no published `page.kind='embed'`**. Handing a developer a snippet that renders
  nothing is the failure mode worth designing out — they would blame the integration,
  not the missing page.
- **Base URL** comes from an explicit env var (`EMBED_PUBLIC_BASE_URL`) and **fails
  loud when unset**, matching `envConfig.citraServiceUrl`'s refusal to fall back to
  localhost. Deriving it from `Host`/`X-Forwarded-*` is tempting but produces a
  snippet that silently works in dev and points at the wrong host in prod.
- **`embedId` is the app slug** in v1 (one embed page per app). Multiple embed pages
  per app would need a compound id — deferred, not designed.

**My Apps card** gets Copy and Download, both calling that endpoint; Download saves
`citra-embed-<slug>.html`. *To confirm:* whether Citra-UI can reach the runtime
directly, or should proxy via smart-app-service (which already resolves runtime URLs
— `main.py:1119`).

### Test → prod

Because the spec is fetched live, an embed publish is a **production change to a
bank's screen**. It must ride the existing test→prod promote path, not go live on
save. A BA editing a test app must not alter what an officer sees in production.

**There is only one runtime, and it is prod.** Test↔prod is not a second deployment —
it is a store distinction inside the same prod database, resolved server-side per
request by `resolve_app_environment(slug)` (`main.py:303`): present in the prod apps
collection → prod; else present in `test_` apps → test; else prod, fail-closed.

That gives the embed a property worth relying on: **the snippet is identical before
and after promotion.** The same `embed` id resolves to the test spec and test MCP
while the app lives only in `test_` collections, and to the prod spec and prod MCP
the moment it is promoted. The bank's developer pastes once, never re-downloads, and
promotion is still the gate that moves them onto prod data.

**That is fine for apps and wrong for embeds.** An app is opened from Citra, so a
flip on promote is invisible and harmless. An embed is pasted into someone else's
codebase: a bank's UAT page and production page must be able to point at different
environments *at the same time*, or they cannot test a change before it reaches
officers. Resolved by §7a below.

### CORS

Allow-all on the runtime, as decided. Worth recording *why* that is safe here: auth
is a bearer token in an `Authorization` header, not a cookie, so `Access-Control-Allow-Origin: *`
is legal (the wildcard is only forbidden with credentialed requests) and grants no
access on its own — every call still needs a valid officer JWT, and a foreign origin
cannot read one. It removes the single most likely cause of a silently failed first
integration.

### Fonts

`--font-sans` resolves `var(--font-inter)` from `next/font`, which does not exist
outside Next.js. Inline the face as a data URI or fall back to the system stack — a
bank CSP will block a font CDN.

---

## 7a. Environment binding for `embed` and `api` apps

### The problem

`resolve_app_environment(slug)` resolves by store, prod-first. Promote **copies**
test→prod and leaves the test row in place (`main.py:3819`), so after a promote the
same slug exists in both stores and always resolves to prod. For an app that is
correct and invisible. For an embed pasted into a bank's codebase it means their UAT
page and their production page cannot point at different environments — the moment a
BA promotes, both flip, and the bank loses the ability to validate a change before
officers see it.

### Decision

**For `embed` and `api` app types only, promotion produces a separately addressable
production artefact with its own stable key. `app` and `dashboard` keep today's
behaviour unchanged.**

This is the industry norm rather than an invention — Stripe ships test and live keys,
Plaid ships sandbox/development/production. Any developer integrating an external
surface expects two credentials, and expects them to coexist.

There is also partial precedent in our own code: promote already strips a `_preview`
suffix when computing `prod_slug`, so a `foo_preview` test artefact and a `foo` prod
app coexist and each resolves correctly today. This formalises that, makes it
unconditional for external types, and stops relying on a naming convention.

### Environment lives in the key, not in a parameter

Embed and API artefacts get an explicit environment-tagged key rather than reusing
the slug:

```
emb_test_7f3a9c…      →  test spec,  test MCP
emb_live_9c21b4…      →  prod spec,  prod MCP
```

The runtime resolves key → (environment, slug), so there is no store-order ambiguity
and no slug collision.

**The rejected alternative matters here:** keeping one key and letting the caller pass
`env: 'test'` would mean a production page could read test data by editing one string
in the browser. Environment must be a property of the credential, not a choice the
caller makes. The prefix also makes a mistake visible in the bank's own code review —
a `_test_` key on a production page is wrong on sight.

### Where the key lives, and how it travels

**Stored** as a field on the app document, alongside the spec — so it is promoted,
versioned and backed up by the machinery that already exists:

| Store | Key | Minted |
|---|---|---|
| `test_` apps collection | `emb_test_…` | first publish to test |
| prod apps collection | `emb_live_…` | first promote — **then never re-minted** |

Both rows coexist, which is the entire point: the bank's UAT page and production page
each hold a different key and each resolves to its own environment.

**Obtained** by the BA from the My Apps card (§7 Export) and handed to the bank's
developer. Two exports over an integration's life — a test snippet before promotion,
a live snippet after — and nothing after that, because re-promotes keep the same key.

**Passed** as a mount parameter in the bank's own source:

```js
citra.mount('#citra-decision', { embed: 'emb_live_9c21b4…', recordId: … });
```

**It is an identifier, not a credential** — the equivalent of Stripe's *publishable*
key. It sits in page source, visible to anyone who views it, and grants nothing on
its own. Two separate things do two separate jobs:

| The key | The officer's JWT |
|---|---|
| *which* app spec + *which* environment | *who* the user is and *what* they may see |
| public, in page source | per-session, from the IdP exchange |
| stable for years | short-lived |

Nothing about access control rests on the key staying secret. It is still revocable
(below), but that is for retiring an integration, not for defending one.

### Rules this must obey

| Rule | Why |
|---|---|
| **The `emb_live_` key is STABLE across re-promotions.** | If a promote minted a fresh key, every bank would re-paste their snippet on every release. Promote already preserves `app_id` and bumps `version` when a prod row exists (`main.py:3974`) — extend the same treatment to the key. **This is the one part of the proposal that would be a serious defect if implemented as "a complete new copy each time".** |
| Promote overwrites the prod spec, keeping the key | Same semantics as today's app promote, including the prior-version snapshot. |
| The prod copy is not directly editable | Edits happen in test and flow through promote — matches promote overwriting the prod row wholesale today. |
| Archiving the test app must not archive the prod embed | Otherwise a bank's live page goes dark when a BA tidies up. |
| A key can be revoked independently | A leaked key must be killable without unpublishing the app. |
| Test corrections must not reach prod clause memory | Env routing already separates `test_` collections, so this should fall out — **but it must be verified, not assumed.** A BA experimenting in UAT polluting the judgements a prod officer relies on would undermine the whole memory story. |

### What already works — the builder never touches prod MCP

Checked, because the same question applies to apps and dashboards today. The builder
path is **already correct** and needs no change:

`POST /apps/{slug}/edit` (`main.py:6375`) loads the source app by store — it may be a
live prod app — and then calls `_bind_build_env()` **immediately before spawning the
pod** (`main.py:6443`). `_resolve_build_env()` returns `"test"` whenever a test
environment is configured, unconditionally. So editing a promoted prod app starts a
*fresh test build*, against test MCPs, writing to `test_` collections, which the BA
then re-promotes. The code comments state this intent explicitly.

So: **open a prod app in the builder → you are editing in test.** That is the desired
behaviour and it is what happens.

### The one path that writes straight to prod

`PUT /apps/{slug}/spec` (`main.py:4377`) — the hand-edit / review-and-edit surface —
binds with `_bind_app_env(slug)` and then `replace_one({"slug": slug}, …)`. For a
promoted app that **writes the prod row directly**, with no test cycle. This is
deliberate, not a defect: the docstring says "env-routed (test vs prod store)", it
snapshots the prior version, bumps `version`, and is owner/admin gated with rollback
available.

For an app or dashboard that is a reasonable admin escape hatch — the blast radius is
inside Citra and it is fully reversible.

**For `embed` and `api` it is not acceptable**, because the blast radius is a bank's
live screen and the change reaches their officers with no opportunity to test. Add to
the rules table above:

| Rule | Why |
|---|---|
| `PUT /apps/{slug}/spec` is **refused for `embed`/`api` apps resolved to prod** | A direct prod spec edit changes a third party's production surface with no test cycle. These types must change only via test → promote. Fail loud with a 409 naming the promote path — not a silent no-op. |

### Work

1. `AppKind` gains `embed` and `api` (today `AppKind = "app"` only).
2. Key mint + storage on publish (test) and promote (prod), stable on re-promote.
3. Runtime resolves key → (env, slug) instead of slug → env, for these types only.
4. The snippet endpoint (§7) emits the key for the environment being exported, so a
   BA gets a test snippet before promotion and a live snippet after.
5. Refuse `PUT /apps/{slug}/spec` for prod-resolved `embed`/`api` apps (409, naming
   the promote path).
6. Verify learning isolation between the two environments end to end.

No change is needed to `POST /apps/{slug}/edit` — it already forces every build into
test regardless of where the source app lives.

Estimated 3–4 days, and it is a **prerequisite for any pilot** — a bank cannot
integrate a surface they have no way to test against.

---

## 8. Work plan

| Phase | Work | Size | Gate |
|---|---|---|---|
| **0. Group→dept mapping** | ✅ **Done.** `oidcGroupMap.js`, wired into `oidcAuthService`, boot-time dept validation. 30/30 checks pass. | — | done |
| **1. Portability spike** | ✅ **Done** — see §11. Every Next dependency aliased, bundle built and measured, 9 headless checks green on both variants, `src/` untouched. | — | met |
| **2. Custom element + API** | ✅ **Done** — see §13. `Citra.init` / `mount` / `update` / `refresh` / `destroy`, `<citra-decision>`, per-mount state, callbacks off the API layer. | — | met |
| **3. Builder support** | ✅ **Done** — see §12. `PageKind='embed'`, standalone `DetailPanel.data_source`, `citra-embed-spec` skill, `AGENTS.md` surface question + hard rules, 9 publish tests. | — | met |
| **4. Export + serving** | ✅ **Done** — see §14. Build target wired into the Dockerfile, cache headers, CORS middleware, spec + snippet endpoints. |  — | met |
| **4a. Environment binding** | ✅ **Done** — see §14. Stable `emb_test_`/`emb_live_` keys minted on publish/promote, key→(env, slug) resolution, direct prod spec edits refused for external surfaces. | — | met |
| **5. Pilot hardening** | ✅ **Done** — visible on-card failure state, [embed-integration-guide.md](embed-integration-guide.md). | — | met |

Roughly 17–22 days. Phase 1 is the only phase with real uncertainty, and it exists to
convert that uncertainty into a number before committing to the rest. CORS work is
gone (allow-all) and the old "bundle diet" phase has collapsed into Phase 1 now that
charts and maps are excluded.

---

## 9. Risks and open questions

**The renderer resists extraction.** Mitigated by Phase 1 being a spike whose whole
job is to find out in days. If it resists, option D (iframe) becomes the fallback and
we lose theming fidelity, not the product.

~~The allowlist is an unmade product decision.~~ **Settled by the spike** — only
`chart` and `map` cannot render, both already fail loud, and builder enforcement is
deferred rather than built. See §4.

**Browser floor.** `--ring` uses `color-mix()`, and shadow DOM + custom elements need
a modern engine. Some banks run older Edge than anyone would like. Confirm the
customer's floor before Phase 2.

**Host page reaching into the shadow root.** Open shadow roots are reachable from
host JS. Acceptable — the host is the bank's own trusted application — but it should
be a stated assumption in the integration doc, not an unexamined one.

**Live spec fetch means builder changes hit production instantly.** Addressed by
riding the test→prod promote path (§7), but it deserves explicit thought about who is
allowed to promote an embed a bank depends on.

**No automated chart coverage.** `e2e/runtime.spec.ts` asserts nothing about charts or
sparklines. This plan is deliberately structured to avoid touching the chart path at
all (the alias in §3), so it is not a blocker here — but it means any *future* change
to that path is unguarded, and it is the reason the alias was chosen over lazy
boundaries. Worth closing independently of this work.

---

## 11. Phase 1 results (built 2026-07-30)

**The spike succeeded.** The runtime's own `PageBody`/`PanelRenderer` renders a real
published AppSpec outside Next.js, inside a shadow root, on a `file://` page, with
live data — and `src/` was not modified at all.

### What was built

| File | Role |
|---|---|
| `embed/shims/navigation.tsx` | `next/navigation` — a real in-memory param store, not a no-op |
| `embed/shims/dynamic.tsx` | `next/dynamic` → `React.lazy` + `Suspense` |
| `embed/shims/runtimeFetch.ts` | same-origin `/api` + `?_t=` → absolute origin + `getToken()` |
| `embed/shims/react-dom.ts` | redirects `createPortal(document.body)` into the shadow root |
| `embed/stubs/{echarts,echarts-core,react-leaflet,empty,unsupported}` | excluded panels, failing loud |
| `embed/embed-reset.css` | re-applies the `<body>` base that a shadow root has no `<body>` for |
| `embed/entry.tsx` | shadow root, CSS injection, theme → tokens, mount/destroy |
| `scripts/build-embed.mjs` | esbuild IIFE + the alias plugin + exclusion verification |
| `e2e/embed.spec.ts` | 9 headless checks, runnable against either variant |

### Measured

| Variant | Raw | Gzip | Suite |
|---|---|---|---|
| **React 18 — shipped** | 392.2 KB | **108.3 KB** | 9/9 |
| Preact + compat (measured, then removed) | 277.7 KB | 73.4 KB | 9/9 |

Both verifiably excluded echarts and leaflet — checked against the esbuild **module
graph**, not by substring-matching the output (that reports a false positive on the
CSS comment "themed recharts tooltip").

**Decision: React 18.** Preact was 32% smaller and passed the identical suite, but
35 KB gzip does not justify a second rendering library. React is what the renderer is
developed and tested against daily, and `preact/compat`'s divergences would surface
in whichever panel someone writes next — long after the size win was banked. The
Preact dependency and build path have been removed; the measurement is recorded here
so the question does not need re-litigating.

*(A first Preact measurement came out LARGER than React. The `react-dom` shim was
pulling in real react-dom alongside `preact/compat`. Worth recording because that
number, taken at face value, argues for exactly the wrong decision.)*

### Three findings that would each have shipped a broken card

**1. `:root` matches nothing inside a shadow root.** `globals.css` declares *every*
design token in one `:root` block. Injected as-is, all of them — `--citra-primary`,
`--citra-fg`, the elevation and radius scales, `--font-sans` — are undefined, every
rule reading them becomes invalid, and the card inherits the host page's colours and
typography. It still *renders*, so nothing looks broken until a customer says it
looks foreign. `entry.tsx` rewrites `:root` → `:host` and throws if that rewrite ever
stops matching.

**2. A THIRD echarts import.** Beyond `PanelRenderer.tsx:20` and `KpiSparkline.tsx:6`,
`src/lib/executiveTheme.ts:15` does `import * as echarts from "echarts"` for a single
`registerTheme()` call — while also exporting the number and currency formatters that
ordinary panels use. Formatting a rupee value pulled in the whole charting library.
Found only because the build *verifies* the exclusion instead of assuming it.

**3. Modals portal to `document.body`.** `ModalPortal` (`PanelRenderer.tsx:2314`)
portals out of the app shell so a fixed overlay clears the sticky header. In an embed
that lands in the host's light DOM, outside the shadow root, with none of our CSS —
and the thing it portals is `RunResultModal`: the recommendation, the planned writes
and the approve/reject. The entire decision card would have rendered unstyled in the
bank's page. The `react-dom` alias redirects it.

### Also confirmed

- **Fonts are already safe.** The runtime uses bundle-safe font *stacks* with no
  external fetch (`page.tsx:81`), so the CSP concern raised earlier does not apply.
  One repair was needed: `--font-inter` comes from `next/font` and is absent here,
  which invalidated the `--font-sans` chain.
- **Isolation works both ways.** The harness sets Georgia serif, crimson headings and
  yellow buttons on the host page; the card is unaffected, and no `--citra-*` rule
  reaches the host document.

### Cost check

`npm run typecheck` and `next build` both pass unchanged. `git diff` over
`citra-app-runtime/src/` is **empty**. The only edits to existing files are
`package.json` (two scripts + two devDependencies), `tsconfig.json` (`embed` added to
`exclude`, mirroring `e2e`), and `.gitignore` (build output).

---

## 12. Phase 3 results (built 2026-07-30)

Built as one piece, because a skill describing fields that do not exist would be
live guidance for specs that fail validation — skills are wildcard-copied into the
builder pod.

### The blocker the skill could not have worked around

`resolve_detail_data` resolved the record from the **linked queue's** data source,
and `DetailPanel.linked_to` was **required**. An embed has no queue to click — the
host passes the record id — so a detail panel would have returned
*"linked_to … which is not a queue with a data_source"* and rendered an empty card.

Fixed additively: `linked_to` is now optional and `DetailPanel.data_source` reads
the record directly by `id_field`. A model validator requires **exactly one** —
neither was the original silent-empty-card failure; both would let the resolver
prefer one without the author ever learning which.

### Changes

| File | Change |
|---|---|
| `models.py` | `PageKind` + `"embed"`; `DetailPanel.linked_to` → optional; `+data_source`; `_one_record_binding` validator; embed pages reject `chart`/`map` |
| `panel_data.py` | `resolve_detail_data` prefers `panel.data_source`, else the linked queue |
| `main.py` | `_smoke_assess_detail` docstring — detail can now carry its own source |
| `schemas/*.json` | regenerated via `gen_schemas.py` (never hand-edited) |
| `citra-app-runtime/src/types/spec.ts` | mirrored `DetailPanel` + `DetailData` |
| `skills/citra-embed-spec/SKILL.md` | new — how to author an embed page |
| `builder-workspace/AGENTS.md` | embed as the third surface answer + hard-rules block |
| `tests/test_embed_page.py` | 9 publish tests |

### Where enforcement sits, and why

**The skill decides the composition; the model blocks only the impossible.** Which
panels serve a decision card is guidance (`citra-embed-spec` + the AGENTS.md hard
rules) — a skill can say *what good looks like*, a validator can only say no. The
two exceptions are `chart` and `map`: they genuinely cannot render because the
bundle aliases echarts and leaflet away, so publish rejects them where the **builder**
sees it rather than at render time in front of a customer's officer. Same
both-sides pattern the dashboard page already uses.

A `queue` on an embed page is discouraged in the skill but **not** rejected — some
hosts may want a short worklist, and that is a preference, not a constraint.

### Verified

`9/9` new publish tests · `54` passing across the spec/validation/panel suites ·
`gen_schemas.py --check` reports schemas in sync · runtime `typecheck` clean ·
embed bundle rebuilds at 108.3 KB with exclusions intact · `9/9` browser checks.

Four unrelated failures (`test_runtime.py` ×3, one chart-type case) and two in
`test_schema_model_drift.py` were confirmed **pre-existing** by re-running with these
changes stashed — they fail identically without them.

---

## 13. Phase 2 results (built 2026-07-30)

The §1 snippet now works verbatim: `Citra.init({ getToken })` → `mount('#el',
{ embed, recordId, theme, onDecision })`, plus `update()`, `refresh()`,
`destroy()`, and a `<citra-decision embed=… record-id=…>` custom element for
hosts whose templates are easier to extend than their scripts.

### The design decision that shaped it

**The host's callbacks hang off the API layer, not the renderer.** `onDecision`
has to fire when an officer approves or rejects — but that button lives inside
`PanelRenderer`, which this build deliberately does not modify. Observing API
traffic through the `runtimeFetch` shim gives the identical signal:

| Host callback | Observed |
|---|---|
| `onRecommendation` | `POST /api/run/{slug}` → `correlation_id` |
| `onItemDecision` | `POST …/items/{id}/feedback` |
| `onDecision` | `POST …/approve/{cid}` |

Each mount remembers the correlation ids **it** produced, so two cards on one
page never report each other's decisions — otherwise a host would refresh the
wrong screen.

### Two multi-mount bugs found and fixed

Both were invisible with a single card, which is exactly why the suite mounts two.

- **Shared filter state.** The param store was a module global, so a filter
  changed on one card silently re-filtered the other. Now delivered by React
  context, scoped to the tree that owns it.
- **Portal theft on teardown.** The modal portal target was a single slot: a
  second mount stole it, and destroying that second mount reset it to `null` —
  sending the *first* card's modals back to `document.body`, outside the shadow
  root, unstyled. Now a stack that restores the previous target on pop.

### Verified

**13/13** browser checks, covering mount, live data, the chart-exclusion notice,
style isolation in both directions, token resolution, theme overrides,
`update()` re-targeting, `destroy()` cleanup, two-card independence, a bad embed
key producing a usable error, and `mount()` without `recordId` failing loud.

Bundle **110.3 KB gzip** (from 108.3), exclusions intact. App `typecheck` and
`build:embed` clean. The only `src/` change in the whole project remains
`types/spec.ts` from Phase 3.

The suite drives the **real** public API and fetches the spec over the network
from `/api/embed/{key}/spec` — the Phase-4 endpoint, stubbed in the test. So
Phase 4 only has to make the server agree with a contract the client already
exercises.

---

## 14. Phases 4a / 4 / 5 results (built 2026-07-30)

### Embed keys (4a)

`embed_keys.py` mints an environment-tagged key for **externally consumed** apps
only — an embed page or a headless app; an ordinary app or dashboard is opened
from Citra and gets none, so there is no credential to revoke later.

- `emb_test_…` on publish-to-test, `emb_live_…` on first promote.
- **Preserved thereafter.** The test guarding this is the important one: a key
  that changed per release would force every customer to re-paste their snippet.
- **Promote must not inherit the test key.** Promote copies the whole test
  document, so `ensure_embed_key` checks the prefix against the target
  environment and mints a fresh live key rather than carrying `emb_test_` into
  production, where it would silently address the wrong environment.
- `PUT /apps/{slug}/spec` now returns **409** for a prod-resolved external
  surface. Hand-editing a live spec is a fine escape hatch when the blast radius
  is inside Citra; it is not when the change lands on a customer's production
  screen with no test cycle.

### Serving + export (4)

| Piece | Where |
|---|---|
| `npm run build:embed` → `public/v1/` | one line in the Dockerfile builder stage; the existing `COPY --from=builder /app/public` ships it |
| Cache headers | `next.config.js` — 5 min on `/v1/citra.js`, immutable on `/v1/citra-<content hash>.js`, no-cache on `/v1/manifest.json`. A version-named immutable URL was unsafe: the version never changed between builds, so deploys rewrote bytes browsers cache for a year. NB a CDN can override these — Cloudflare Browser Cache TTL did. |
| CORS + preflight | **`src/middleware.ts`** — `headers()` cannot answer `OPTIONS`, and a POST with `Authorization` + JSON always triggers one |
| `GET /embed/{key}/spec` | smart-app-service, proxied by `/api/embed/[key]/spec` |
| `GET /apps/{slug}/embed/snippet` | smart-app-service, proxied by `/api/apps/[slug]/embed-snippet` |

Two decisions worth recording:

- **`apps_base_url` already is the runtime's public origin**, so the plan's
  proposed `EMBED_PUBLIC_BASE_URL` was unnecessary — one less setting to get
  wrong in a deployment.
- **The runtime fills `version`, not smart-app-service.** Only the service that
  serves the bundle knows which one is live; a version reported from the other
  side goes stale silently the first time the two deploy out of step.

### Hardening (5)

**A load failure is now visible in the card**, not just on `onError`. A host is
free to ignore the callback, and then the officer sees an empty rectangle where
a decision should be — the precise failure this surface exists to avoid. Rendered
with the renderer's own `.panel-error` styling and `role="alert"`.

`prefers-reduced-motion` and focus styling were already in `globals.css` and are
inherited by the shadow root, so no work was needed there.

[embed-integration-guide.md](embed-integration-guide.md) is the developer-facing
handoff — snippet, token exchange, full API, theming, test→prod, and a symptom →
cause → fix table.

### ⚠ Learning isolation was NOT working — found by verifying it (2026-07-30)

§7a asserted that test corrections could not reach prod clause memory because
the collections are env-routed. The collections *are* routed. **The environment
binding was wrong**, so the routing sent them to the wrong place.

**The chain.** An embedded card resolves its environment from the key prefix on
its FIRST call (`/embed/{key}/spec`). Every call after it — run, panel data,
detail, approve — is addressed by SLUG. `resolve_app_environment` is prod-first
by store, and promote COPIES test→prod leaving the test row in place, so a
promoted app resolves to prod. Demonstrated directly:

```
promoted app, resolved by slug   -> prod
unpromoted app, resolved by slug -> test
```

So from the moment a BA promoted, a bank's UAT page holding `emb_test_` would
read PRODUCTION records, run against PRODUCTION sources, and file its officers'
corrections into PRODUCTION clause memory. Not merely a learning-isolation
failure — **the UAT card operated on live customer data.**

Worth noting `env_context.py`'s own safety argument ("a handler that forgets to
set test simply fails to load the test app — fail-closed, never corruption")
does not cover this: the handler *does* resolve, just to the wrong environment.

**The fix.** The card sends `X-Citra-Embed-Key` on every request; a middleware
captures it and `_bind_app_env` prefers that environment. It is a HINT, not a
caller-chosen environment — `_embed_key_environment` verifies the key exists in
that environment's store bound to THAT slug, so a page can only reach an
environment it genuinely holds a key for. Captured by middleware rather than
threaded through ~25 handler signatures because missing one endpoint is exactly
how the gap arose.

Guarded by `tests/test_embed_env_isolation.py` (7) and a browser check that the
header rides on every call, not just the first.

*Process note:* the three tests that passed before the fix all asserted `"prod"`
— which is the default, so they would have passed against a no-op. Only the two
asserting `"test"` had any power.

### Verified

**97** passing across the smart-app-service spec/validation/endpoint suites
(37 of them new: keys, page kind, both endpoints, environment isolation) ·
`gen_schemas.py --check` in sync · runtime `typecheck` and `next build` clean,
with `/api/embed/[key]/spec`, `/api/apps/[slug]/embed-snippet` and Middleware
registered · **15/15** browser checks · bundle **110.5 KB gzip**, exclusions
intact.

**Still unverified: nothing has run against a real stack.** Every test here
stubs Mongo or the API. The environment-binding bug above is precisely the class
of defect that survives stubbed tests — it was found by reasoning about the call
sequence, not by a test failing.

---

## 16. LIVE dev E2E (2026-07-30)

Run against a real stack: scratch Mongo, real smart-app-service, real
citra-app-runtime, host page on a **different origin** so real CORS applies.

```bash
docker run -d --name citra-e2e-mongo -p 27077:27017 mongo:7
cd smart-app-service
MONGO_URI=mongodb://localhost:27077 MONGO_DB=citra_e2e python scripts/seed_embed_e2e.py
MONGO_URI=mongodb://localhost:27077 MONGO_DB=citra_e2e PORT=9100 \
  ./venv/Scripts/python.exe -m uvicorn main:app --port 9100
cd ../citra-app-runtime && npm run build:embed && npm run dev
./venv/Scripts/python.exe scripts/probe_embed_e2e.py     # 17 HTTP checks
cd e2e && npx playwright test -g "@embedlive"            # 6 browser checks
```

The seed writes to a **scratch** Mongo deliberately — a throwaway E2E has no
business leaving fixture rows in the shared dev Atlas.

### ⚠ It immediately caught a bug that would have broken every real integration

**`X-Citra-Embed-Key` was missing from the CORS preflight allow-list.** The
runtime middleware allowed `Authorization, Content-Type, Accept`. A browser
sending a header the preflight does not allow **rejects the request outright**,
so every API call from an embedded card would have failed — cross-origin only,
which is to say in every real deployment and in none of the tests.

The 16 stubbed browser checks could not see it: `page.route` intercepts before
CORS is evaluated. Only a genuinely foreign origin exercises it. Fixed, and the
live suite now asserts the preflight explicitly.

### Proven

| | |
|---|---|
| `/v1/citra.js` served, `max-age=300` | ✅ 407 KB from the runtime |
| CORS preflight incl. the embed key | ✅ 204 + correct allow-headers |
| Key → spec, both environments | ✅ `emb_live_`→prod, `emb_test_`→test |
| A slug is NOT an embed key | ✅ 404 |
| Unknown key | ✅ 404 (no existence probing) |
| Unauthenticated | ✅ refused |
| **Env binding through the middleware** | ✅ **prod without the header, test with it** |
| A key naming no app | ✅ ignored, falls back to store |
| Card renders cross-origin | ✅ title, record id, sections |
| Embed key on every real API call | ✅ zero unkeyed |
| Style isolation both ways | ✅ host Georgia excluded, `--citra-fg` resolved, no leak out |
| Theme applied | ✅ `--citra-primary: #0b5fff` |
| Bad key | ✅ visible failure, not a blank box |

### NOT proven — the data plane

The record does not load: `discovery could not resolve 'loan_origination'`. The
local discovery service's boot crawl fails because it is configured to find the
dept-MCP on its own port. That is **local demo-stack wiring, not an embed
defect** — it is the same source-resolution path every panel uses in the full
app, and it would fail identically there.

So the embed's *transport, auth, environment binding, rendering and isolation*
are proven end to end; **panel data, a real `/run`, and a real approve are
not.** Those need the discovery↔MCP link fixed locally first.

### Also spotted

The detail panel renders a **"← Back" button** inside the card. In an embed
there is nowhere to go back to — `router.back()` is a no-op against an empty
history — so it is a dead control on a customer's screen. Not fixed; it needs a
renderer-side notion of "no navigation chrome", which is the same gap that made
`readOnly` unshippable (§15).

---

## 17. The corrected composition, proven live (2026-07-30)

The queue-as-trigger shape works. Clicking **Review** inside the card, on a
`file://` origin, against the real stack:

```
[data req] /data/embed-e2e-loan/trigger?id=LAN-2026-000003
[data req] /run/embed-e2e-loan
portal children : 1
modal text      : Agent recommendation … DECLINE — FOIR 89.37% exceeds 50% cap
                  … PROPOSED CHANGES (1) Record Credit Decision
                  … Apply 1 change / Reject recommendation
leaked to host  : false
```

So: **a queue action inside an embed fires the agent, and RunResultModal — the
real decision surface — renders inside the shadow root.** That is what the
Phase-2 portal redirect was protecting; without it this modal lands in the
bank's light DOM with none of our CSS.

`citra-embed-spec` now describes this shape and has been verified against it.

### New defect found here

**The modal backdrop is see-through.** The host page's content reads through the
overlay (see `embed/dev/live-modal.png`), which is unacceptable on a customer's
screen — the officer is reading a decision through their own UI. The full app
gets an opaque backdrop from a rule the shadow root is not picking up; likely
another `:root`/`body`-anchored style, in the same family as §11's finding.
NOT fixed.

### Test-harness lesson

Three false negatives before the real result, all mine: a row selector that
assumed `<tr>` in a Cards-view queue; a wait on the record id, which the DETAIL
panel satisfies long before the queue renders; and a 30s default timeout against
a 33s agent turn. Each looked like a product failure. Wait on the exact thing
being tested — the action button, then the portal's child count.

---

## 15. What is still pending

### Done since (Citra-UI, 2026-07-30)

**Export button — built.** `PowerAppsScreen` gains an Export action that calls
`SmartAppService.getEmbedSnippet(slug)` and copies the snippet, naming the key,
the script URL, and — when the key is `emb_test_` — that it reads test data until
promoted. Shown only when `has_embed_page && has_embed_key`, so it can never lead
to a 409. Both flags are new on `AppSummary`.

**"Embedded card" is now a build option.** `BuildKindPickerModal` gains a tile
between App and API (`primaryPageKind: 'embed'`), with a matching welcome in
`AgentChatPane` and a teal badge + "Embedded" filter tab in the list.
`primary_page_kind` already flowed through `startBuild` generically, so no
transport change was needed.

Placement is deliberate: embedded sits *between* App and API because it is the
middle answer to "integrate with our system" — with the API the customer's
developers rebuild the reason-capture UI themselves, and that is the first thing
cut under deadline.

### Blocking a pilot

**1. Nothing has run against a real stack.** Every test stubs Mongo or the API.
The environment-binding defect in §14 is exactly the class that survives stubbed
tests — it was found by reasoning about a call sequence, not by a failing test.
A real pass needs Mongo + smart-app-service + runtime + a dept-MCP together, with
a genuinely promoted app.

**2. The builder has never produced an embed page.** `citra-embed-spec` and the
AGENTS.md routing are guidance for an LLM; whether the builder actually follows
them is unproven. The publish-side contract is tested, the authoring side is not.

**3. Nothing is deployed.** `citra.js` is not in any built image; the Dockerfile
line exists but has not been exercised in a real build.

### Known limitations, deliberately shipped

- **No `readOnly` option.** The renderer has no page-level read-only mode
  (`readonly` in PanelRenderer is a form-FIELD format). A flag would have
  rendered live Approve/Reject anyway — the opposite of what a host asking for
  read-only wants — so it was removed rather than shipped broken.
- **No `locale` option.** The renderer takes locale from the AppSpec, so a
  host-supplied one would be accepted and ignored.
- **Two live cards share a modal portal target.** The most recent mount wins;
  both are fixed-position overlays so it is invisible in practice. See §13.
- **One `Citra.init()` per page.** A second instance pointing at a different
  origin is refused, because `runtimeFetch`'s config is process-wide.

---

## 10. Out of scope

- Public/anonymous embedding. This is an authenticated internal surface only.
- Charts and maps in embeds. Excluded by decision; they stay in the full app surface.
- Anything that lets the embed write outside plan-then-apply. Every write still goes
  through the officer's Approve; the embed adds no new commit path.
- Self-service builder access for the customer's business users in the first sale.
  Deliver the working workflow first; the construction kit is what they grow into once
  they trust the output. Leading with "your BAs can build apps by chatting" is a
  thrilling demo and an alarming sentence to a bank's change-control function.
