// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import { defineConfig } from "@playwright/test";

// The mock API is started for you; the RUNTIME must already be running and
// pointed at it (see README) — set RUNTIME_URL to reach it.
export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.mjs",
  globalSetup: "./_globalSetup.mjs",
  globalTeardown: "./_globalTeardown.mjs",
  // Coverage cells accumulate in an in-process array written once in afterAll.
  // Multiple workers = separate processes = each writes its own subset and the
  // last clobbers the rest (ui.json came out empty). Keep it single-process; the
  // matrix is I/O-light (page loads), so this costs little.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  webServer: {
    command: "node mock-api.mjs",
    port: Number(process.env.MOCK_API_PORT || 8899),
    reuseExistingServer: true,
    stdout: "pipe",
  },
  use: {
    baseURL: process.env.RUNTIME_URL || "http://localhost:3100",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.02 } },
});
