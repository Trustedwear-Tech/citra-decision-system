// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

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
  // `cookies()` became async in Next 15 -- without the await this reads a
  // Promise, `.get` is undefined, and every officer looks signed out.
  const token = (await cookies()).get("acme_citra_token")?.value;
  if (!token) {
    // 401, not an empty 200: the card's onError should say "signed out", and a
    // blank token would surface as an unexplained empty card instead.
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }
  return NextResponse.json({ token });
}
