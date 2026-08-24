// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

import { setAppLocale, setChartPalette } from "@/lib/executiveTheme";

/**
 * Sets the active locale/currency for all chart + KPI formatting from the
 * app's theme, BEFORE any panel renders. Mounted once at the top of the app
 * page (above the panels in JSX), so its render-time call to setAppLocale runs
 * first — formatters are localised before charts paint. Renders nothing.
 */
export default function LocaleSetter({
  locale,
  currency,
  title,
  chartPalette,
  primary,
}: {
  locale?: string | null;
  currency?: string | null;
  /** Browser-tab title incl. the company name (Theme v2 identity) — the
   *  server's generateMetadata is zero-fetch slug-derived, so the branded
   *  title is applied client-side once the spec is in hand. */
  title?: string | null;
  /** theme.chart_palette (+ theme.primary for the "brand" ramp). */
  chartPalette?: string | null;
  primary?: string | null;
}) {
  // Idempotent module-level set — safe to call on every render.
  setAppLocale(locale, currency);
  setChartPalette(chartPalette, primary);
  if (typeof document !== "undefined" && title && document.title !== title) {
    document.title = title;
  }
  return null;
}
