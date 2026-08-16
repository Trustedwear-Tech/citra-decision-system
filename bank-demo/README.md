<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: BUSL-1.1

  Licensed under the Business Source License 1.1. Non-production use is granted;
  production use requires a commercial licence until the Change Date, after
  which this file converts to Apache-2.0. See LICENSE at the repository root.
-->

# Acme Bank — demo customer application

A pretend bank, so the Citra decision card can be shown the way a customer
actually meets it: inside someone else's application, added with one script tag.

Three business lines, and only one of them has a card — a decision app goes
where a decision is made, not everywhere:

| screen            | Citra |
|-------------------|-------|
| Loan Origination  | the credit decision card |
| Collections       | none — deliberately |
| Motor Claims      | slot ready; set `NEXT_PUBLIC_CITRA_CLAIMS_EMBED_KEY` |

## Why Next.js

`app/api/login/route.ts` runs on the SERVER, and that is the whole point:

* Citra's user service does not allow-list customer domains for CORS, and
  should not have to — a browser calling it from a bank's domain is blocked,
  correctly. The exchange belongs on the bank's own server.
* The officer's password never leaves that server.
* The Citra token is stored in an **httpOnly** cookie, so no page script — including
  any third-party script — can read it. `/api/token` hands it to `getToken()`
  same-origin.

Integrators copy demos, so this one models the shape a bank should ship.

## The integration, in full

`components/CitraCard.tsx` — load the script, `init({ getToken })`, `mount({ embed, recordId })`.
No Citra npm package. No build step. No shared React.

## Running it

Bring up the main quickstart stack first (`make install` from the repo root —
see the top-level README), which starts `citra-user-service` on `:7004` and
`citra-app-runtime` on `:3100` and seeds the `acme-bank` demo tenant. Then:

    cp .env.local.example .env.local     # fill in the embed key
    npm install
    npm run dev                          # http://localhost:4300

Points at your **local** stack by default (`localhost:3100` / `localhost:7004`)
— everything here talks to the same containers the rest of the quickstart
brings up, nothing reaches outside your machine.

## Notes

* Pinned to **Next 14.2.5** — 14.2.35 fails to boot on Node 24 — and
  **TypeScript 5.4.5**, because Next 14 cannot load TypeScript 7.
* The LENDING record ids are real rows in `loan_origination.loan_applications`
  — the same table `scripts/quickstart/seed-demo.sh` seeds into Postgres. The
  card reads that source live, so an invented id renders an empty card.
* `embed-test/` is the sibling zero-framework version (raw Node, one HTML file),
  kept because plenty of banks are on stacks that look like that.

## Demo accounts

`scripts/quickstart/seed-demo.sh acme-bank` (part of `make install`) seeds one
officer per department into the local `citra-user-service` Mongo. Sign in as a
lending officer to see the card:

| email | dept |
|---|---|
| `credit-manager@acme-bank-demo.citra.ai` | lending — **has the decision card** |
| `collections-manager@acme-bank-demo.citra.ai` | collections |
| `claims-manager@acme-bank-demo.citra.ai` | claims |
| `sales-manager@acme-bank-demo.citra.ai` | sales_distribution |
| `coo@acme-bank-demo.citra.ai` | central_ops (also org_admin) |

The seed script creates these as org/department records only — it does not set
a local-auth password on them, so `app/api/login/route.ts`'s email+password
exchange won't yet succeed for a freshly seeded persona. Give one a password
first (e.g. the super-admin's **Manage Users** panel in the main app, or
`Citra-User-Service/scripts/backfill_work_sa.js`'s sibling admin scripts), or
sign in as the platform super-admin (`ADMIN_EMAIL`/`ADMIN_PASSWORD` from the
root `.env`) and use its own dept assignment instead.
