/**
 * Browser → server proxy for GET /apps/{slug}/detail/{panel_id}.
 *
 * Backs the detail panel: one round trip returns the clicked record plus
 * per-section data (documents, agent run timeline, pending approvals).
 * Forwards the `?id=` record selector and supplies an Authorization
 * header (caller token when present, else the minted runtime token).
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function GET(
  req: NextRequest,
  { params }: { params: { slug: string; panelId: string } }
) {
  const slug = encodeURIComponent(params.slug);
  const pid = encodeURIComponent(params.panelId);
  const id = req.nextUrl.searchParams.get("id");
  const qs = id ? `?id=${encodeURIComponent(id)}` : "";

  const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/detail/${pid}${qs}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      ...runtimeAuthHeader(
        req.headers.get("authorization") ?? req.headers.get("Authorization")
      ),
      ...embedKeyHeader(req),
    },
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
