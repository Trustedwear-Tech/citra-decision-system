// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * CORS for the embeddable decision UI.
 *
 * `citra.js` runs on the CUSTOMER's origin and calls this runtime's `/api/*`
 * routes, so every one of those calls is cross-origin. Two things are needed
 * and only one of them can live in `next.config.js`:
 *
 *   1. Response headers on `/api/*`            — headers() could do this.
 *   2. An answer to the OPTIONS preflight      — headers() CANNOT do this.
 *
 * A POST carrying `Authorization` and `Content-Type: application/json` always
 * triggers a preflight, and Next route handlers do not answer OPTIONS on their
 * own. Without this middleware the browser rejects the request before the
 * handler is ever reached, and the panel reports a generic network failure that
 * looks nothing like "CORS". Doing it here covers every API route at once;
 * per-route OPTIONS exports would drift the moment someone adds a route.
 *
 * WHY ALLOW-ALL IS SAFE HERE
 * Auth travels as a bearer token in a header, never a cookie — `runtimeFetch`
 * in the embed bundle sends `credentials: "omit"` explicitly. The `*` wildcard
 * is only illegal on credentialed requests, and it grants nothing on its own:
 * every call still needs a valid officer JWT, and a foreign origin cannot read
 * one out of the host page it does not control. What allow-all removes is the
 * single most likely cause of a silently failed first integration — a customer
 * origin nobody remembered to add to a list.
 */
import { NextResponse, type NextRequest } from "next/server";

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
  // X-Citra-Embed-Key MUST be listed. The embed sends it on every request to
  // keep a customer's UAT card bound to test after the app is promoted, and a
  // header the preflight does not allow makes the browser reject the request
  // outright — the card would fail entirely, cross-origin only. Stubbed tests
  // never see this: they intercept before CORS applies.
  "Access-Control-Allow-Headers":
    "Authorization, Content-Type, Accept, X-Citra-Embed-Key",
  // Lets the host page's devtools surface our correlation id on a failure.
  "Access-Control-Expose-Headers": "Content-Type, X-Trace-Id",
  "Access-Control-Max-Age": "86400",
};

export function middleware(req: NextRequest) {
  if (req.method === "OPTIONS") {
    // 204 + headers, no body — the preflight never reaches a route handler.
    return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
  }
  const res = NextResponse.next();
  for (const [k, v] of Object.entries(CORS_HEADERS)) res.headers.set(k, v);
  return res;
}

export const config = {
  // API routes only. The app's own pages are same-origin and adding CORS
  // headers to HTML responses would be noise at best.
  matcher: "/api/:path*",
};
