<!-- Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
     SPDX-License-Identifier: Apache-2.0 -->

## Building on it

One published spec, three ways to consume it -- the surfaces are described
under *Three surfaces, one intelligence* above; this is the mechanics.

### 1. Build a Decision App

**In the UI.** Sign in at http://localhost:8081. Under **🚀 Run your
high-stakes, complex operations**, open **Self-Improving Decision Apps & APIs**
— that is the builder — and describe the app in plain English. A builder pod
drafts the spec against the catalogue, asks what it cannot infer, and publishes
when you accept.

**Not "My Decision Apps".** That card next to it is the *consumer* list: it
shows apps someone else built and published **to you**, and it is what a user
without build rights sees. Building is gated on `canBuildApps`, so an operator
who only consumes apps will not see the builder card at all. Same distinction
for **My Dashboards**.

One question it asks matters more than the others: the **surface** -- a full
app, an embedded card, or headless. Pick the embedded surface if you intend to
embed it. It cannot be bolted on afterwards; an app built without an embed page
returns a 409 at step 3 below and has to be rebuilt.

**Headless.** The same engine, no UI:

```bash
# 1. Open a build session  ->  session_id
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:9100/build -d '{...}'

# 2. Drive the build conversationally
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/build/$SESSION_ID/chat/stream -d '{...}'

# 3. Validate the whole spec and go live
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:9100/publish -d '{...}'
```

`POST /apps/{slug}/edit` revises a published app, `GET /apps/{slug}/spec/lint`
checks a spec without publishing, and `POST /apps/{slug}/promote-to-prod`
copies test to prod.

### 2. Call it from your own system

Start from `GET /apps/{slug}/decision-contract` -- the app describes its own
request shape, endpoints and governance rules, so you are not guessing. The
loop itself, and its two governance guards, are under *Driving it headlessly*
above.

Rather than writing that HTTP by hand:

| | Package | Source |
|---|---|---|
| TypeScript | `@citra/decision-api` | `decision-api-sdk/typescript/` |
| Python | `citra-decision-api` | `decision-api-sdk/python/` |

`decision-api-sdk/INTEGRATION.md` is the one to read: auth, the governed loop,
rendering lists / details / media, item findings and feedback, plus raw-HTTP
recipes for Kotlin, Swift and curl where there is no SDK.
`decision-api-sdk/API-REFERENCE.md` is the endpoint list.

### 3. Embed the card in a screen you already have

Ask the app for its snippet -- nothing to look up or assemble:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:9100/apps/$SLUG/embed/snippet
```

It returns the embed key, the script URL, the record contract, and this, filled
in:

```html
<div id="citra-decision"></div>
<script src="https://apps.example.com/v1/citra.js"></script>
<script>
  const citra = Citra.init({ getToken: () => yourApp.citraToken() });
  citra.mount('#citra-decision', {
    embed:    'emb_live_...',
    recordId: yourApp.currentRecordId(),
    onDecision: (d) => yourApp.onCitraDecision(d),
  });
</script>
```

The card renders in a shadow root, so the host page's CSS and the card's cannot
reach each other. The key's prefix carries the environment (`emb_test_` /
`emb_live_`), so a UAT screen and a production screen can point at different
environments at the same time.

Three things that bite in this order:

- **409, "no embed page"** -- built without the embed surface. Rebuild it (§1).
- **409, "no embed key"** -- published before keys existed; republish to mint one.
- **A script URL you do not serve.** `APPS_BASE_URL` defaults to
  `https://apps.citra-ai.com`, so a self-hosted install hands out a snippet
  pointing at an origin that is not yours. Set it to your own before you copy
  the snippet anywhere.

`bank-demo/` is a complete worked integration to read against.

## Three surfaces, one intelligence

- **Decision App** -- a ready-made workspace: case-working pages, live
  dashboards, a plain-English copilot. Live on day one, nothing to build.
- **Decision API** -- every recommendation, score, reason, and the learning
  loop itself, served over REST. Call it from any system you already run.
- **Embeddable recommendation UI** -- drop the recommendation and its
  reasoning straight into your existing LOS, CRM, or core screens. Your
  team never changes tools. See `bank-demo/` for a worked integration.
