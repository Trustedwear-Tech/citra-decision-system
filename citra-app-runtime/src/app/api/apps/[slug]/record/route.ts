// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Browser → server proxy for GET /apps/{slug}/record.
 *
 * Reads ONE record's current field values (by ?source=&id=&key=) for an
 * edit-mode form to prefill. Forwards to smart-app-service `/apps/{slug}/record`,
 * which gates access identically to the detail endpoint and returns
 * `{ record: {...} | null }`. Used by PanelRenderer's FormPanelView (mode="edit").
 */

import { NextRequest, NextResponse } from "next/server";
import { runtimeAuthHeader, embedKeyHeader } from "@/lib/specClient";

import { smartAppServiceUrl } from "@/lib/env";

export async function GET(req: NextRequest, props: { params: Promise<{ slug: string }> }) {
  const params = await props.params;
  const slug = encodeURIComponent(params.slug);
  const sp = req.nextUrl.searchParams;
  const qs = new URLSearchParams();
  const source = sp.get("source");
  const id = sp.get("id");
  const key = sp.get("key");
  if (source) qs.set("source", source);
  if (id) qs.set("id", id);
  if (key) qs.set("key", key);

  const upstream = await fetch(
    `${smartAppServiceUrl()}/apps/${slug}/record?${qs.toString()}`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
        ...runtimeAuthHeader(
          req.headers.get("authorization") ?? req.headers.get("Authorization")
        ),
      ...embedKeyHeader(req),
      },
      cache: "no-store"
    }
  );

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json"
    }
  });
}
