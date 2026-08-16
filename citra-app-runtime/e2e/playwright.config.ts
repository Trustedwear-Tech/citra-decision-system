import { defineConfig, devices } from "@playwright/test";

// The runtime is started by the dev stack (Next.js on :3100), NOT by Playwright
// — these tests assume the full stack is up (runtime + smart-app-service + the
// dept MCP + Mongo). Override the base URL with RUNTIME_BASE_URL if needed.
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false, // one app, shared backend state — keep it sequential
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 60_000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: process.env.RUNTIME_BASE_URL || "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
