/**
 * Browser → server proxy for GET /apps/{slug}/data/{panel_id}.
 *
 * Auth is FAIL-CLOSED: ``runtimeAuthHeader`` forwards the caller's bearer
 * token when present and sends NO Authorization otherwise (it does NOT mint a
 * service token). An unauthenticated request therefore gets a 401 from
 * smart-app-service — which is the intended outcome, not a bug.
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function GET(
  req: NextRequest,
  props: { params: Promise<{ slug: string; panelId: string }> }
) {
  const params = await props.params;
  const slug = encodeURIComponent(params.slug);
  const pid = encodeURIComponent(params.panelId);
  // Forward page params (e.g. a filter_bar's ?district=Patna) so the panel's
  // data_source filter predicate resolves on the current selection.
  const qs = req.nextUrl.search;

  const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/data/${pid}${qs}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      ...runtimeAuthHeader(
        req.headers.get("authorization") ??
          req.headers.get("Authorization")
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
