/**
 * Browser → server proxy for POST /apps/{slug}/chat.
 *
 * The agent_chat panel cannot reach smart-app-service directly because
 * SMART_APP_SERVICE_URL is a server-only env var. This route runs
 * server-side, forwards the chat transcript, and returns the JSON reply.
 *
 * The Authorization header is forwarded so smart-app-service can scope the
 * agent run by tenant / owner and apply source visibility.
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function POST(req: NextRequest, props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const slug = encodeURIComponent(params.slug);
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Ask smart-app-service to keepalive-stream the turn (SSE). A chat turn
        // can outlast the gateway idle timeout (ALB ~60s / CF ~100s); buffering
        // the response (await .text()) would reintroduce the 504 this proxy
        // exists to avoid, so we pipe the stream straight through.
        Accept: "text/event-stream",
        ...runtimeAuthHeader(
          req.headers.get("authorization") ?? req.headers.get("Authorization")
        ),
      ...embedKeyHeader(req),
      },
      body: JSON.stringify(body),
      cache: "no-store"
    });
    const ct = upstream.headers.get("content-type") ?? "application/json";
    const isStream = ct.includes("text/event-stream");
    // Pass the body through WITHOUT buffering. Pre-flight errors (status >= 400)
    // arrive as a normal JSON body and pass through with their own content-type.
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: isStream
        ? { "Content-Type": ct, "Cache-Control": "no-cache", "X-Accel-Buffering": "no" }
        : { "Content-Type": ct }
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "chat proxy failed" },
      { status: 502 }
    );
  }
}
