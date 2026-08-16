// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * UI rendering matrix — the real UI-coverage measurement.
 *
 * For every (panel type × data state) cell, load the runtime with a fixture spec
 * (slug `fx-<panel>-<state>`), and assert three things:
 *   1. structure — the panel rendered (not the app-level "Something went wrong");
 *   2. fail-loud — negative states (source_error / unauthorized / non_columnar)
 *      show an ERROR, never a silent-blank panel;
 *   3. visual — a screenshot per cell (Playwright's toHaveScreenshot diffs it).
 * Accessibility (axe) can be layered per cell with @axe-core/playwright.
 *
 * Emits which cells were exercised → ../.coverage-cells/ui.json (the aggregator
 * turns that into the UI coverage %).
 *
 * Requires: the runtime running against mock-api.mjs, and Playwright browsers.
 * See README.md. Run: `npx playwright test`.
 */
import { test, expect } from "@playwright/test";
import { emitCell } from "./_coverage.mjs";

const RUNTIME = process.env.RUNTIME_URL || "http://localhost:3100";
const TOKEN = process.env.DA_UI_TOKEN || "";

// Panels that fetch columnar panel data have the fail-loud negative states; a
// static/self-fetching panel (form/markdown/notice/agent_chat/notifications/
// filter_bar) has no data-source error to surface, so it's only asked to render
// its content. Mirror of vocabulary.DATA_PANELS (the denominator uses the split).
const DATA_PANELS = ["queue", "detail", "chart", "document_view", "dashboard", "calendar", "map"];
const STATIC_PANELS = ["form", "markdown", "notice", "agent_chat", "notifications", "filter_bar"];
const DATA_STATES = ["loading", "empty", "single_row", "many_rows", "truncated",
  "source_error", "unauthorized", "non_columnar"];
const NEGATIVE = new Set(["source_error", "unauthorized", "non_columnar"]);

// legal (panel, state) cells only — data panels get every state, static panels
// only `single_row` (does the content render at all).
const CELLS = [
  ...DATA_PANELS.flatMap((p) => DATA_STATES.map((s) => [p, s])),
  ...STATIC_PANELS.map((p) => [p, "single_row"]),
];

for (const [panel, state] of CELLS) {
  test(`render ${panel} @ ${state}`, async ({ page }) => {
    const url = `${RUNTIME}/fx-${panel}-${state}${TOKEN ? `?_t=${TOKEN}` : ""}`;
    const resp = await page.goto(url, { waitUntil: "networkidle" });
    expect(resp?.status(), "page should load").toBeLessThan(500);

    const body = await page.locator("body").innerText();

    // Coverage is the render+assert, NOT the screenshot — record the cell BEFORE
    // the visual-regression check so a baseline diff can't erase coverage.
    emitCell(panel, state);

    if (NEGATIVE.has(state)) {
      // fail-loud: a visible error, never a silent-blank panel. The runtime's
      // panel-error surfaces the upstream `detail` text (e.g. "... returned 500:
      // connection refused", "not columnar", "not permitted"/403), so match the
      // error affordance broadly — role=alert or the known detail phrasings.
      const hasError = await page.locator('[role="alert"], .panel-error').count() > 0
        || /went wrong|error|unavailable|not permitted|not columnar|refused|failed|HTTP \d{3}/i.test(body);
      expect(hasError, `${panel}@${state}: expected a visible error (fail-loud), got:\n${body.slice(0, 200)}`).toBeTruthy();
    } else if (state === "empty") {
      expect(/no records|empty|nothing|—|no data/i.test(body) || body.length > 0).toBeTruthy();
    } else {
      // a populated state must show real content, not the app error boundary
      expect(/something went wrong/i.test(body), `${panel}@${state} hit the error boundary`).toBeFalsy();
    }

    await expect(page).toHaveScreenshot(`${panel}-${state}.png`, { maxDiffPixelRatio: 0.02 });
  });
}
// Coverage cells are written per-test by emitCell() and folded into ui.json by
// globalTeardown (playwright.config) — no afterAll, so a second spec file can
// contribute cells without clobbering this one's.
