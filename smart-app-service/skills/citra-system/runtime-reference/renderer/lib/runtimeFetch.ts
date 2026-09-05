// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * runtimeFetch — same-origin `/api/...` fetch that always carries the
 * captured end-user JWT.
 *
 * Every browser → runtime API call MUST forward the user's token as
 * `Authorization: Bearer <jwt>` so the API proxy ([slug] route handlers) sends
 * a real user identity to smart-app-service, which forwards it on as
 * `X-User-JWT` to the dept-MCP. Without it the proxy falls back to the
 * `scope=citra-app-runtime` service token and dept-MCPs reject every read /
 * write with 403 "org/dept denied: user_org=None".
 *
 * Use this instead of bare `fetch` for ALL client-side `/api/...` calls. Doing
 * it per-call-site (capture token → build headers → set Authorization) kept
 * drifting and leaving auth gaps on newer panels (detail, tool, dashboard);
 * centralising it here makes the token structural, not optional.
 *
 * The token is captured from the `?_t=` URL param on first load — see
 * lib/userToken.ts. SSR-safe: getUserToken() returns null server-side, so this
 * simply sends no Authorization header there (server routes mint their own).
 *
 * Caller headers are preserved; an explicit Authorization passed by the caller
 * is never overwritten.
 */
import { getUserToken } from "./userToken";

export function runtimeFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = getUserToken();
  const headers = new Headers(init.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(input, { ...init, headers });
}
