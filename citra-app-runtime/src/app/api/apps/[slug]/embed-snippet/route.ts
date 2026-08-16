/**
 * Browser → server proxy for GET /apps/{slug}/embed/snippet — the Export
 * action behind the My Apps card.
 *
 * The runtime fills in `version`: smart-app-service knows the embed KEY and the
 * public origin, but only this service actually serves the bundle, so only it
 * knows which version is live. Reporting a guess from the other side would go
 * stale silently the first time the two deploy out of step.
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";
import { smartAppServiceUrl } from "@/lib/env";
import pkg from "../../../../../../package.json";

export async function GET(req: NextRequest, props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const slug = encodeURIComponent(params.slug);

  const upstream = await fetch(
    `${smartAppServiceUrl()}/apps/${slug}/embed/snippet`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
        ...runtimeAuthHeader(
          req.headers.get("authorization") ?? req.headers.get("Authorization"),
        ),
      ...embedKeyHeader(req),
      },
      cache: "no-store",
    },
  );

  const text = await upstream.text();
  if (!upstream.ok) {
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  }

  let body: Record<string, unknown>;
  try {
    body = JSON.parse(text);
  } catch {
    // Upstream returned 200 with something that isn't JSON — pass it through
    // rather than inventing a shape.
    return new NextResponse(text, { status: 200 });
  }
  body.version = (pkg as { version?: string }).version ?? null;
  return NextResponse.json(body);
}
