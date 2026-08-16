/**
 * Browser → server proxy for GET /apps/{slug}/media/{ds_id}.
 *
 * Streams a SoR-record media column (photo / PDF) THROUGH the dept-MCP. The
 * browser hits this same-origin route with an OPAQUE reference — the record
 * key + column — never a storage URL. We forward to smart-app-service (which
 * forwards to the dept-MCP `/media`), and pipe the byte stream straight back.
 * The MCP owns source-storage creds + per-source visibility and fetches the
 * bytes itself; the browser never touches S3 / the source system.
 *
 * Auth: an <img src>/<a href> is a plain GET that can't set an Authorization
 * header, so we read the `citra_user_token` cookie (set from the ?_t= launch
 * handshake — see lib/userToken.ts) and forward it as the bearer. FAIL-CLOSED:
 * no token → no Authorization → smart-app 401s (the intended outcome).
 */

import { NextRequest } from "next/server";
import { cookies } from "next/headers";

import { smartAppServiceUrl } from "@/lib/env";

export async function GET(
  req: NextRequest,
  { params }: { params: { slug: string; dsId: string } },
) {
  const slug = encodeURIComponent(params.slug);
  const dsId = encodeURIComponent(params.dsId);
  const sp = req.nextUrl.searchParams;
  const key = sp.get("key");
  const col = sp.get("col");
  const keyField = sp.get("key_field") || "id";

  if (!key || !col) {
    return new Response(JSON.stringify({ detail: "key + col query params required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Plain image/anchor GET → no Authorization header; read the launch cookie.
  const cookieTok = cookies().get("citra_user_token")?.value;
  const hdrAuth = req.headers.get("authorization") ?? req.headers.get("Authorization");
  const bearer = hdrAuth
    ? hdrAuth
    : cookieTok
      ? `Bearer ${cookieTok}`
      : undefined;

  const upstream = new URL(`${smartAppServiceUrl()}/apps/${slug}/media/${dsId}`);
  upstream.searchParams.set("key", key);
  upstream.searchParams.set("col", col);
  upstream.searchParams.set("key_field", keyField);

  const headers: Record<string, string> = {};
  if (bearer) headers["Authorization"] = bearer;

  let res: Response;
  try {
    res = await fetch(upstream.toString(), { headers, cache: "no-store" });
  } catch (err) {
    return new Response(
      JSON.stringify({ detail: `media upstream unreachable: ${String(err)}` }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }

  if (!res.ok) {
    // Forward the real status + message (401/403/404/502) so the UI can react.
    const text = await res.text();
    return new Response(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
    });
  }

  // Stream the bytes straight through (Next.js pipes the fetch ReadableStream).
  const out = new Headers();
  for (const h of ["content-type", "content-disposition", "cache-control", "content-length"]) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(res.body, { status: 200, headers: out });
}
