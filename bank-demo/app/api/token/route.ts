// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Hands the CURRENT officer's Citra token to the page, for `getToken()`.
 *
 * The card needs a bearer token on every call, but the token itself lives in an
 * httpOnly cookie the browser cannot read. So the page asks its OWN server for
 * it, over same-origin — no CORS, no token in localStorage, and the bank keeps
 * a single place where session policy is enforced.
 *
 * A real integrator might instead mint a short-lived, embed-scoped token here.
 * That is strictly better and this endpoint is where it would go.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function GET() {
  const token = cookies().get("acme_citra_token")?.value;
  if (!token) {
    // 401, not an empty 200: the card's onError should say "signed out", and a
    // blank token would surface as an unexplained empty card instead.
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }
  return NextResponse.json({ token });
}
