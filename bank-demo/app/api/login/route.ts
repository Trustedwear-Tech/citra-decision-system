// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * The bank's own sign-in endpoint — and the reason this demo is Next.js.
 *
 * A Next.js route handler runs on the SERVER, which is exactly where the token
 * exchange belongs:
 *
 *   1. Citra's user service does not allow-list customer domains for CORS, and
 *      should not have to. A browser calling it from acmebank.example is
 *      blocked, correctly.
 *   2. The officer's password never leaves the bank's own server.
 *   3. The Citra token is set as an httpOnly cookie, so page scripts — and any
 *      third-party script on the page — cannot read it.
 *
 * Integrators copy demos, so this one models the shape a real bank should ship
 * rather than the shortest thing that works.
 */
import { NextResponse } from "next/server";

const USER_SERVICE = process.env.USER_SERVICE_URL;

export async function POST(req: Request) {
  if (!USER_SERVICE) {
    // Fail loud: a missing config here would otherwise look like bad credentials.
    return NextResponse.json(
      { error: "USER_SERVICE_URL is not configured on the server." },
      { status: 500 },
    );
  }

  let email = "";
  let password = "";
  try {
    const body = await req.json();
    email = String(body.email ?? "");
    password = String(body.password ?? "");
  } catch {
    return NextResponse.json({ error: "Malformed request." }, { status: 400 });
  }
  if (!email || !password) {
    return NextResponse.json(
      { error: "Email and password are required." },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${USER_SERVICE.replace(/\/+$/, "")}/api/auth/local/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch (e) {
    return NextResponse.json(
      { error: `Could not reach the identity service: ${(e as Error).message}` },
      { status: 502 },
    );
  }

  const text = await upstream.text();
  let data: Record<string, unknown> = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    /* fall through to the status check with an empty body */
  }

  if (!upstream.ok) {
    const msg =
      (data.error as string) ||
      (data.message as string) ||
      `Sign-in failed (HTTP ${upstream.status})`;
    return NextResponse.json({ error: msg }, { status: upstream.status });
  }

  // The user service has returned the token under a couple of shapes over time;
  // accept either rather than break on the one we did not expect.
  const nested = (data.data ?? {}) as Record<string, unknown>;
  const token = (data.token ?? nested.token) as string | undefined;
  const user = (data.user ?? nested.user ?? {}) as Record<string, unknown>;
  if (!token) {
    return NextResponse.json(
      { error: "Sign-in succeeded but no token was returned." },
      { status: 502 },
    );
  }

  // The browser gets the officer's NAME for the UI; the token stays httpOnly.
  const res = NextResponse.json({ email: user.email ?? email });
  res.cookies.set("acme_citra_token", token, {
    httpOnly: true,
    sameSite: "lax",
    // The demo runs on http://localhost, where a Secure cookie would be dropped.
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  return res;
}
