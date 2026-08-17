// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Thin client for smart-app-service. Used server-side from Next.js route handlers.
 *
 * Auth model: there is NO service/god token. Every call carries the END USER's
 * JWT — launched from Citra-UI as `?_t=` and validated by smart-app-service
 * with the shared `JWT_SECRET` (the same key user-service signs with, the same
 * way Citra-Service validates). SSR reads the token from the request (the
 * `?_t=` query param or the `citra_user_token` cookie — see page.tsx);
 * browser → `/api` proxy calls forward it via `runtimeAuthHeader(incoming)`.
 * An unauthenticated caller sends no token and smart-app-service returns 401/404.
 */

import type { AppDetail, AppListResponse } from "@/types/spec";
import { smartAppServiceUrl } from "./env";

/**
 * Authorization header for a browser→server proxy route. Forwards the user's
 * token when present; otherwise sends NOTHING. There is deliberately no
 * service-token fallback — an anonymous caller must not be able to read an app
 * (the upstream 401 is the correct, diagnosable outcome).
 */
export function runtimeAuthHeader(
  incoming?: string | null,
): Record<string, string> {
  return incoming ? { Authorization: incoming } : {};
}

/**
 * Forward the embedded card's key to smart-app-service.
 *
 * Only the card's FIRST call names the key in its path; every one after it —
 * run, panel data, detail, approve — is addressed by SLUG, and slug resolution
 * upstream is prod-first. A PROMOTED app exists in both the test and prod
 * stores, so without this header a bank's UAT card would silently read and
 * write PRODUCTION records from the moment the app was promoted.
 *
 * Upstream treats it as a hint and verifies the key exists in that environment
 * bound to that slug, so forwarding it cannot grant access to an environment
 * the caller has no key for. Safe to forward unconditionally.
 */
export function embedKeyHeader(req: {
  headers: { get(name: string): string | null };
}): Record<string, string> {
  const key = req.headers.get("x-citra-embed-key");
  return key ? { "X-Citra-Embed-Key": key } : {};
}

function bearer(token?: string | null): Record<string, string> {
  if (!token) return {};
  return { Authorization: token.startsWith("Bearer ") ? token : `Bearer ${token}` };
}

async function getJson<T>(
  path: string,
  init?: RequestInit,
  userToken?: string | null,
): Promise<T> {
  const res = await fetch(`${smartAppServiceUrl()}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...bearer(userToken),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(
      `smart-app-service ${path} -> ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as T;
}

/** Sentinel: the caller had no/expired user token (upstream 401). Distinct from
 *  null (= 404 app not found) so the page can render a "session missing" state
 *  instead of crashing to the generic error boundary. */
export const UNAUTHORIZED = Symbol("unauthorized");

export async function fetchAppDetail(
  slug: string,
  userToken?: string | null,
): Promise<AppDetail | null | typeof UNAUTHORIZED> {
  try {
    return await getJson<AppDetail>(
      `/apps/${encodeURIComponent(slug)}`,
      undefined,
      userToken,
    );
  } catch (err) {
    if (err instanceof Error && err.message.includes("404")) return null;
    // A 401 is an AUTH problem (missing/expired handoff token), not an app
    // problem — surface it as a distinct, human-readable state (fail loud,
    // but diagnosably: "reopen from Citra", not "Something went wrong").
    if (err instanceof Error && err.message.includes("401")) return UNAUTHORIZED;
    throw err;
  }
}

export async function fetchApps(
  params: { scope?: "all" | "mine" | "shared" | "admin"; kind?: "app" | "dashboard" } = {},
  userToken?: string | null,
): Promise<AppListResponse> {
  const qs = new URLSearchParams();
  qs.set("scope", params.scope || "all");
  if (params.kind) qs.set("kind", params.kind);
  return getJson<AppListResponse>(`/apps?${qs.toString()}`, undefined, userToken);
}

export async function runAction(
  slug: string,
  body: { action: string; inputs: Record<string, unknown>; user_id?: string },
  userToken?: string | null,
): Promise<unknown> {
  return getJson(
    `/apps/${encodeURIComponent(slug)}/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    userToken,
  );
}
