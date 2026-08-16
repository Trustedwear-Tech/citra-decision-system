/**
 * Browser → server proxy for POST /apps/{slug}/tool/{tool_name}.
 *
 * Used by PanelRenderer's PanelToolButtons to invoke a tools_v2 entry
 * directly (no LLM). The smart-app-service route gates access on:
 *   1. user JWT + tenant
 *   2. panel exists
 *   3. tool_name is in the panel's tool_buttons allowlist
 *   4. tool exists in agent_spec.tools_v2 and the kind is enabled
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function POST(
  req: NextRequest,
  props: { params: Promise<{ slug: string; tool: string }> }
) {
  const params = await props.params;
  const slug = encodeURIComponent(params.slug);
  const tool = encodeURIComponent(params.tool);
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/tool/${tool}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...runtimeAuthHeader(
        req.headers.get("authorization") ??
          req.headers.get("Authorization")
      ),
      ...embedKeyHeader(req),
    },
    body: JSON.stringify(body),
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
