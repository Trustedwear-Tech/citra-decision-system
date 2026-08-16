/**
 * Browser → server proxy for GET /embed/{key}/spec.
 *
 * This is the FIRST call citra.js makes, from a page on the CUSTOMER's origin,
 * so it is cross-origin by definition (see src/middleware.ts for the CORS
 * headers and the OPTIONS preflight).
 *
 * The embed key names which app and which environment; authorisation is still
 * the officer's own JWT, checked upstream against the app's audience exactly as
 * opening the app in Citra would. The key is an identifier, not a credential.
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";
import { smartAppServiceUrl } from "@/lib/env";

export async function GET(req: NextRequest, props: { params: Promise<{ key: string }> }) {
  const params = await props.params;
  const key = encodeURIComponent(params.key);

  const upstream = await fetch(`${smartAppServiceUrl()}/embed/${key}/spec`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      ...runtimeAuthHeader(
        req.headers.get("authorization") ?? req.headers.get("Authorization"),
      ),
      ...embedKeyHeader(req),
    },
    cache: "no-store",
  });

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
