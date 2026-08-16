/**
 * Browser → server proxy for POST /apps/{slug}/run/{correlation_id}/approve.
 *
 * Backs the Approve / Reject buttons in a detail panel's `approval`
 * section. Forwards the caller's token when present so smart-app-service
 * can attribute the approver; falls back to the runtime service token.
 *
 * Note: smart-app-service rejects self-approval — when the runtime runs
 * without an end-user identity, approving a run the runtime itself
 * requested returns 403 by design.
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function POST(
  req: NextRequest,
  props: { params: Promise<{ slug: string; cid: string }> }
) {
  const params = await props.params;
  const slug = encodeURIComponent(params.slug);
  const cid = encodeURIComponent(params.cid);
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    body = {};
  }

  const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/run/${cid}/approve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // Ask smart-app-service to keepalive-stream the apply (SSE). The
      // plan-then-apply replay can outlast the gateway idle timeout (ALB ~60s /
      // CF ~100s); buffering (await .text()) would reintroduce the 504, so we
      // pipe the stream straight through.
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
}
