/**
 * Browser → server proxy for POST /apps/{slug}/document/{ds_id}.
 *
 * Returns a short-lived signed URL pointing at the original artifact
 * behind a chunk. The browser-side click handler in PanelRenderer
 * obtains `metadata.doc_path` from a prior /api/data result and POSTs it
 * here; we forward to smart-app-service with the user's auth header
 * (captured by the same `?_t=` handshake the panel-data route uses) so
 * the dept-MCP can re-check per-source visibility before signing.
 *
 * Auth is FAIL-CLOSED (same as panel-data): forward the caller's bearer when
 * present, send NO Authorization otherwise (no minted service-token fallback).
 * An unauthenticated "Open" click 401s — the intended outcome.
 *
 * The browser fetches the signed URL directly from S3 — the bucket
 * has a permissive GET/HEAD CORS policy (AllowedOrigins=*), so the
 * runtime → S3 hop is cross-origin but allowed. No server-side
 * artifact fetch is needed here.
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function POST(
  req: NextRequest,
  { params }: { params: { slug: string; dsId: string } }
) {
  const slug = encodeURIComponent(params.slug);
  const dsId = encodeURIComponent(params.dsId);

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return new NextResponse(
      JSON.stringify({ detail: "invalid JSON body" }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );
  }

  const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/document/${dsId}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...runtimeAuthHeader(
        req.headers.get("authorization") ??
          req.headers.get("Authorization")
      ),
      ...embedKeyHeader(req),
    },
    body: JSON.stringify(body ?? {}),
    cache: "no-store"
  });

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json"
    }
  });
}
