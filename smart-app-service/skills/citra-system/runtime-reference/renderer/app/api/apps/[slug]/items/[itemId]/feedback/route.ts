/**
 * Browser → server proxy for POST /apps/{slug}/items/{itemId}/feedback.
 *
 * Per-item human feedback on an ItemFinding (image / document analysis).
 * `accept` records the call; `reject` (with a reason) appends the reason to the
 * (app, modality, task_type) rubric so the next similar item is judged with it
 * — prompt-criteria learning (see docs/multimodal-decision-apps-plan.md).
 *
 * Auth is FAIL-CLOSED, same as the document route: forward the caller's bearer
 * when present, send NO Authorization otherwise. The officer's identity is the
 * `actor` recorded on the correction.
 */
import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";
import { smartAppServiceUrl } from "@/lib/env";

export async function POST(
  req: NextRequest,
  { params }: { params: { slug: string; itemId: string } }
) {
  const slug = encodeURIComponent(params.slug);
  const itemId = encodeURIComponent(params.itemId);

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return new NextResponse(JSON.stringify({ detail: "invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const upstream = await fetch(
    `${smartAppServiceUrl()}/apps/${slug}/items/${itemId}/feedback`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...runtimeAuthHeader(
          req.headers.get("authorization") ?? req.headers.get("Authorization")
        ),
      ...embedKeyHeader(req),
      },
      body: JSON.stringify(body ?? {}),
      cache: "no-store",
    }
  );

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
