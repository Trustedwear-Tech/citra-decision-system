<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Embedding a Citra decision card

**Audience:** the developer who owns the screen the card goes on.
**Time to integrate:** an afternoon. No build step, no npm install, no framework.

---

## 1. What you are adding

A card that shows a recommendation for one record, the evidence behind it, and
Approve / Reject with a reason. Your officers make the decision without leaving
your application.

```html
<div id="citra-decision"></div>

<script src="https://citra.yourbank.internal/v1/citra.js"></script>
<script>
  const citra = Citra.init({ getToken: () => yourApp.citraToken() });

  citra.mount('#citra-decision', {
    embed:    'emb_live_9c21b4…',
    recordId: yourApp.currentApplicationId(),
    onDecision: (d) => yourApp.refreshCaseHeader(d),
  });
</script>
```

Your colleague who published the app gets you the exact snippet — with the
`embed` key filled in — from **My Apps → Copy embed script**. Copy it; don't retype the key.

**It works in any stack.** Angular, Vue, React, JSP, Razor, plain HTML. The
bundle carries its own renderer; your page needs no bundler and no awareness of
what's inside.

---

## 2. The two things you provide

### The record id

The card decides about **one record** — the one already on screen. You pass its
id; Citra reads that record itself.

Use whatever key the dataset uses (your application number, claim reference,
account id). If you are unsure which column that is, the person who built the
app knows.

### The officer's token

Your users sign into **your** identity provider as they always do. Exchange
their ID token for a Citra token once, then hand it to the card.

**Do the exchange on your server, not in the browser.** Two reasons, and the
first one will stop you outright:

1. **CORS.** Citra-User-Service accepts a fixed list of origins. Your domain is
   not on it, and getting every customer's domain added to a shared allow-list
   is not a sane integration step. Server-to-server has no preflight at all.
2. Your users' credentials and your session never leave your own origin.

```js
// YOUR BACKEND — e.g. POST /api/citra-token on your own domain.
// Called after your normal OIDC/PKCE sign-in has produced an ID token.
app.post('/api/citra-token', async (req, res) => {
  const upstream = await fetch(
    'https://citra.yourbank.internal/user-service/api/auth/oidc',
    { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idToken: req.session.idpIdToken }) },
  );
  const body = await upstream.json();
  // Return it to YOUR page. It is the officer's own bearer token — the same
  // one your front-end would hold after any sign-in — and the card needs it
  // in the browser to call the embed API.
  res.json({ token: body.token ?? body.data?.token });
});
```

```js
// YOUR BROWSER CODE — same-origin, so no CORS anywhere.
const { token } = await (await fetch('/api/citra-token', { method: 'POST' })).json();
sessionStore.citraToken = token;          // whatever your app already uses
```

If you would rather the token never reached the browser at all, keep it in the
server session and proxy the embed's API calls through your own backend
instead — `getToken` accepts a promise, so it can fetch a short-lived one on
demand. That is more moving parts for the same result; most integrations do
not need it.

There is **no second login**. Citra verifies the ID token against your IdP's
JWKS. If you use email/password against Citra rather than your own IdP, the
same applies with `/user-service/api/auth/local/login` — still server-side.

*(A working example of exactly this is in `embed-test/` in the Citra repo: a
throwaway host application that signs an officer in and mounts the card.)*

Prefer `getToken` over `clientToken` — it is called before every request, so a
refresh needs no remount:

```js
Citra.init({ getToken: () => sessionStore.citraToken });   // preferred
Citra.init({ clientToken: 'eyJ…' });                       // static; dies with the token
```

**What the officer can see is decided by their departments**, mapped from your
directory groups at sign-in. A record outside their scope returns nothing — you
do not need to pre-filter.

---

## 3. API reference

### `Citra.init(options)` → instance

| Option | Type | Notes |
|---|---|---|
| `getToken` | `() => string \| Promise<string>` | **Preferred.** Called per request. |
| `clientToken` | `string` | Static alternative. |
| `baseUrl` | `string` | Defaults to the origin `citra.js` was served from. |
| `onError` | `(err) => void` | Instance-wide error sink. |

Call it **once per page**. Mount as many cards as you like from that instance.

### `citra.mount(selectorOrElement, options)` → mount

| Option | Type | Notes |
|---|---|---|
| `embed` | `string` | **Required.** The key from Export. |
| `recordId` | `string` | **Required.** The record on screen. |
| `theme` | `{ primary, accent, font, radius, density }` | Match your application. |
| `onDecision` | `(e) => void` | Fires after a committed decision. |
| `onItemDecision` | `(e) => void` | Fires on a per-document accept/reject. |
| `onRecommendation` | `(e) => void` | Fires when the recommendation renders. |
| `onError` | `(err) => void` | Per-card. |

```js
mount.update({ recordId: 'LN-4472' });  // re-target without remounting
mount.refresh();                        // reload
mount.destroy();                        // remove; call this on route change
citra.destroy();                        // tear down every card
```

`onDecision` payload:

```js
{ caseId, recordId, action: 'approve' | 'reject' | 'cancel',
  reason: 'revenue_vs_tax_mismatch',          // the reason CODE
  reasonText: 'Revenue vs tax filing mismatch',
  correlationId, appliedWrites, raw }
```

### Declarative alternative

For server-rendered templates and CMS blocks, where extending markup is easier
than extending scripts:

```html
<citra-decision embed="emb_live_9c21b4…" record-id="LN-4471"></citra-decision>
```

`Citra.init()` must still run once on the page. Changing `record-id` re-targets
the card.

---

## 4. Making it look like your application

Pass a theme at mount:

```js
theme: { primary: '#0b5fff', accent: '#f59e0b', font: 'Inter',
         radius: 6, density: 'compact' }
```

`font` accepts `inter`, `source-sans`, `ibm-plex`, `system`, or any family your
users already have installed. **No webfont is ever fetched** — a font CDN would
be blocked by your CSP and would fail silently, so the card uses font *stacks*.

The card renders in a shadow root, so your CSS cannot break it and its CSS
cannot leak into your page. `radius` and `density` adjust corner rounding and
spacing to sit naturally in your layout.

---

## 5. Test, then production

Your app has two keys:

| Key | Reads |
|---|---|
| `emb_test_…` | test data, test systems |
| `emb_live_…` | production data, production systems |

Use the test key on your UAT page and the live key on production. **Both work at
the same time**, which is what lets you validate a change before officers see
it.

The live key is stable — it does not change when the app is updated, so you
never re-paste. Only the environment prefix distinguishes them; an `emb_test_`
key on a production page is a bug you can catch in code review.

---

## 6. When something goes wrong

The card shows failures **on screen**, and reports them to `onError`. Common ones:

| Symptom | Cause | Fix |
|---|---|---|
| "This decision card could not be loaded" + `Check the embed key` | Wrong or retired key | Re-copy it from My Apps → Copy embed script |
| The same message after a 401/403 | Token missing, expired, or rejected | Check `getToken` returns a current token |
| A CORS error on an **embed** call | The runtime is not reachable from your origin | Confirm the `baseUrl`; the runtime allows all origins |
| A CORS error on **sign-in** | You are calling Citra-User-Service from the browser | Do the token exchange server-side — see §2. Its allow-list does not include your domain, by design |
| "mount target not found" | The element does not exist yet | Mount after your DOM is ready |
| Card renders but no records | The officer's departments do not cover this record | Check their directory groups |
| "This panel can't be shown here" | A chart or map on the embed page | Ask the app owner to remove it — charts belong on a full app page |

Every card logs with a `[citra-embed]` prefix, so filtering the console isolates
it from your own output.

---

## 7. Notes worth knowing

- **Nothing commits without the officer's click.** Every write is proposed,
  shown, and applied only on Approve. The card adds no new write path.
- **Capture the reason.** The reason code recorded on a reject is what the
  system learns from. If you build your own UI later, keep that field.
- **Pin a version** if your change control requires it:
  `https://citra.yourbank.internal/v1/citra-1.4.2.js`. The unversioned
  `/v1/citra.js` is the stable pointer and picks up fixes automatically.
- **Call `mount.destroy()`** when your view unmounts, or you leak a card per
  navigation in a single-page app.
- **The `embed` key is not a secret.** It identifies which app and environment,
  nothing more — like a publishable key. Access is controlled by the officer's
  token.
