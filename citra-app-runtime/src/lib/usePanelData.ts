// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

// usePanelData — shared hook for panels backed by a server data source.
// Performs a single GET /api/data/{slug}/{panel_id} on mount (and again when
// the page params change). Extracted from PanelRenderer so the designed
// panels (hero / stat_strip / timeline) live in their own modules without a
// circular import into the renderer monolith.

import { useEffect, useState } from "react";
import { runtimeFetch } from "@/lib/runtimeFetch";
import type { PanelData } from "@/types/spec";

export interface DataState {
  loading: boolean;
  error: string | null;
  data: PanelData | null;
}

export function usePanelData(
  slug: string,
  panelId: string,
  enabled: boolean,
  reloadKey: number = 0,
  pageParams?: Record<string, string>,
): DataState {
  const [state, setState] = useState<DataState>({
    loading: enabled,
    error: null,
    data: null,
  });

  // Stable key so a re-render with an equal params object doesn't refetch, but
  // a filter_bar change (new ?district=…) does.
  const paramsKey = JSON.stringify(pageParams ?? {});

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    // Forward page params (filter_bar / navigate) so the source predicate
    // filters on the current selection.
    const parsed = JSON.parse(paramsKey) as Record<string, string>;
    const qs = Object.keys(parsed).length
      ? "?" + new URLSearchParams(parsed).toString()
      : "";
    // runtimeFetch carries the end-user JWT → X-User-JWT at the dept-MCP.
    runtimeFetch(
      `/api/data/${encodeURIComponent(slug)}/${encodeURIComponent(panelId)}${qs}`,
      { cache: "no-store" }
    )
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setState({
            loading: false,
            error: body.detail ?? body.error ?? `HTTP ${res.status}`,
            data: null,
          });
        } else {
          setState({ loading: false, error: null, data: body as PanelData });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          loading: false,
          error: err instanceof Error ? err.message : String(err),
          data: null,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [slug, panelId, enabled, reloadKey, paramsKey]);

  return state;
}
