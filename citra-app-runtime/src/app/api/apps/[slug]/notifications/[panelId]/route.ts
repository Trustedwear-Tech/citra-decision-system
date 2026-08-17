// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Browser → server proxy for GET /apps/{slug}/notifications/{panelId}.
 *
 * Resolves a `notifications` panel: pending approvals the caller's roles can act
 * on + SLA-breached (overdue) records. Forwards to smart-app-service, which
 * gates access identically to the detail endpoint and returns
 * `{ notifications: [...], count, error }`. Used by NotificationsPanelView.
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
  const panelId = encodeURIComponent(params.panelId);

  const upstream = await fetch(
    `${smartAppServiceUrl()}/apps/${slug}/notifications/${panelId}`,
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
