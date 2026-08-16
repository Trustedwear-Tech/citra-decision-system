/**
 * Browser → server proxy for the record comments/notes thread.
 *
 *   GET  /api/apps/{slug}/records/{recordId}/comments  → list the human notes
 *   POST /api/apps/{slug}/records/{recordId}/comments  → append a note {text}
 *
 * Forwards to smart-app-service `/apps/{slug}/records/{recordId}/comments`,
 * which reads/writes the app-local overlay (kind="comment") — never the SoR.
 * Used by PanelRenderer's CommentsSection (the `comments` detail section).
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

function upstreamUrl(slug: string, recordId: string): string {
  return `${smartAppServiceUrl()}/apps/${encodeURIComponent(
    slug
  )}/records/${encodeURIComponent(recordId)}/comments`;
}

function authHeaders(req: NextRequest): Record<string, string> {
  return {
    Accept: "application/json",
    ...runtimeAuthHeader(
      req.headers.get("authorization") ?? req.headers.get("Authorization")
    ),
      ...embedKeyHeader(req),
  };
}

function relay(text: string, upstream: Response): NextResponse {
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json"
    }
  });
}

export async function GET(
  req: NextRequest,
  props: { params: Promise<{ slug: string; recordId: string }> }
) {
  const params = await props.params;
  const upstream = await fetch(upstreamUrl(params.slug, params.recordId), {
    method: "GET",
    headers: authHeaders(req),
    cache: "no-store"
  });
  return relay(await upstream.text(), upstream);
}

export async function POST(
  req: NextRequest,
  props: { params: Promise<{ slug: string; recordId: string }> }
) {
  const params = await props.params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const upstream = await fetch(upstreamUrl(params.slug, params.recordId), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(req) },
    body: JSON.stringify(body),
    cache: "no-store"
  });
  return relay(await upstream.text(), upstream);
}
