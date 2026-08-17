// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * `echarts` (the core library) stub — aliased in for the EMBED build only.
 *
 * There is a THIRD static echarts import beyond the two obvious ones in
 * PanelRenderer and KpiSparkline: `src/lib/executiveTheme.ts:15` does
 * `import * as echarts from "echarts"` — and that module also exports the
 * locale/currency/number formatters (`fmtNum`, `getAppLocale`, `fmtCurrency`)
 * that non-chart panels use everywhere. So importing a formatter drags in the
 * entire charting library.
 *
 * It uses echarts for exactly one thing, at line 283:
 *
 *     echarts.registerTheme("citra-exec", CITRA_EXEC_THEME);
 *
 * Registering a chart theme when no chart can render is a no-op by definition,
 * so this stub satisfies the import and lets executiveTheme's real formatters
 * come through untouched. Aliasing the core (rather than reimplementing the
 * formatters in the embed) keeps ONE implementation of money and number
 * formatting — a second copy would drift, and drifting currency formatting in
 * a bank's UI is not a cosmetic bug.
 */

/** No-op: themes only matter to a renderer that is not in this bundle. */
export function registerTheme(_name: string, _theme: unknown): void {}

/** Present so a namespace import that touches them fails loud rather than
 *  silently rendering an empty box, if an allowlist gap ever lets a chart
 *  through. */
export function init(): never {
  throw new Error(
    "[citra-embed] echarts.init() called — charts are not supported in " +
      "embedded cards and the library is excluded from this bundle.",
  );
}

export function registerMap(_name: string, _geo: unknown): void {}
export function connect(_group: string): void {}
export function use(_ext: unknown): void {}
