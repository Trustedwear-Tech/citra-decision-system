// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Browser → server proxy for POST /apps/{slug}/queue-state.
 *
 * A queue asks, on load, for the latest staged card of every row it shows
 * (`{ key_column, keys }`), and smart-app-service answers from the staging
 * collection: pending reviews, applied decisions, sent-back recommendations.
 * The browser tab is then a cache of what the database says, not the only
 * place a review ever existed.
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

  const upstream = await fetch(`${smartAppServiceUrl()}/apps/${slug}/queue-state`, {
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
