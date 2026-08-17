// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * LIVE dev E2E for the embeddable card — @embedlive
 *
 * Nothing is stubbed. The bundle, the spec and every API call go to a running
 * smart-app-service, and the host page is served from a DIFFERENT origin
 * (file://) so real CORS applies. That last part matters: the stubbed suite
 * intercepts requests before CORS is evaluated, which is exactly why a missing
 * `X-Citra-Embed-Key` in the preflight allow-list was invisible there and fatal
 * here.
 *
 * Prereqs (see docs/embeddable-decision-ui-plan.md §16):
 *   docker run -d --name citra-e2e-mongo -p 27077:27017 mongo:7
 *   MONGO_URI=mongodb://localhost:27077 MONGO_DB=citra_e2e \
 *     python scripts/seed_embed_e2e.py                 # in smart-app-service
 *   MONGO_URI=... MONGO_DB=citra_e2e PORT=9100 uvicorn main:app --port 9100
 *   npm run build:embed && npm run dev                 # in citra-app-runtime
 *
 *   cd e2e && npx playwright test -g "@embedlive"
 */
import { test, expect, type Page } from "@playwright/test";
import path from "node:path";
import jwt from "jsonwebtoken";
import fs from "node:fs";

const RUNTIME_ROOT = path.resolve(__dirname, "..");
const HARNESS =
  "file:///" +
  path.join(RUNTIME_ROOT, "embed", "dev", "live.html").replace(/\\/g, "/");

const LIVE_KEY = "emb_live_e2e0000000000002";
const TEST_KEY = "emb_test_e2e0000000000001";

/** Same secret the running service verifies with. */
function officerToken(): string {
  const env = fs.readFileSync(
    path.resolve(RUNTIME_ROOT, "..", "smart-app-service", ".env"),
    "utf8",
  );
  const secret = /^JWT_SECRET=(.*)$/m.exec(env)?.[1]?.trim();
  if (!secret) throw new Error("JWT_SECRET not found in smart-app-service/.env");
  return jwt.sign(
    {
      sub: "officer@acme-bank.com", user_id: "officer@acme-bank.com",
      email: "officer@acme-bank.com", tenant_id: "acme-bank",
      org_id: "acme-bank", roles: ["user"], dept_ids: ["lending"],
    },
    secret,
    { expiresIn: "1h", issuer: "Citra-AI" },
  );
}

const shellText = (page: Page) =>
  page.evaluate(() => {
    const host = document.querySelector("#citra-decision") as HTMLElement;
    return host?.shadowRoot?.querySelector(".app-shell")?.textContent ?? "";
  });

async function mount(page: Page, key = LIVE_KEY) {
  await page.evaluate(
    ([k, t]) => (window as any).mountLive(k, t),
    [key, officerToken()] as const,
  );
}

test.beforeAll(async ({ request }) => {
  const r = await request.get("http://localhost:3100/v1/citra.js");
  if (!r.ok()) {
    throw new Error(
      "citra.js is not being served — start the runtime and run `npm run build:embed`",
    );
  }
});

test("@embedlive the bundle is served with a cache header", async ({ request }) => {
  const r = await request.get("http://localhost:3100/v1/citra.js");
  expect(r.status()).toBe(200);
  expect(r.headers()["content-type"]).toContain("javascript");
  expect(r.headers()["cache-control"]).toContain("max-age=300");
});

test("@embedlive the preflight allows the embed key header", async ({ request }) => {
  // The bug this caught: without X-Citra-Embed-Key in the allow-list the
  // browser rejects EVERY embed API call cross-origin, so the card never loads.
  const r = await request.fetch(
    "http://localhost:3100/api/embed/" + LIVE_KEY + "/spec",
    {
      method: "OPTIONS",
      headers: {
        Origin: "https://bank.example",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-citra-embed-key",
      },
    },
  );
  expect(r.status()).toBe(204);
  expect(r.headers()["access-control-allow-headers"].toLowerCase())
    .toContain("x-citra-embed-key");
});

test("@embedlive card renders cross-origin against the real service", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(HARNESS);
  await mount(page);

  await expect
    .poll(() => shellText(page), { timeout: 30_000 })
    .toContain("Loan decision");
  expect(errors, errors.join("\n")).toHaveLength(0);

  // No host CORS failures — the whole point of running from file://.
  const reported = await page.evaluate(() => (window as any).citraEvents.error);
  expect(reported).toEqual([]);
});

test("@embedlive the embed key rides on the real API calls", async ({ page }) => {
  const keyed: string[] = [];
  const unkeyed: string[] = [];
  page.on("request", (r) => {
    if (!r.url().includes("/api/")) return;
    const k = r.headers()["x-citra-embed-key"];
    (k ? keyed : unkeyed).push(r.url());
  });

  await page.goto(HARNESS);
  await mount(page);
  await expect.poll(() => shellText(page), { timeout: 30_000 }).not.toBe("");

  expect(keyed.length).toBeGreaterThan(0);
  expect(unkeyed, `these API calls lost the key: ${unkeyed.join(", ")}`)
    .toHaveLength(0);
});

test("@embedlive a bad key surfaces a visible failure, not a blank box", async ({
  page,
}) => {
  await page.goto(HARNESS);
  await mount(page, "emb_live_doesnotexist00");

  await expect
    .poll(() => shellText(page), { timeout: 30_000 })
    .toContain("could not be loaded");
});

test("@embedlive the record loads from the real data plane", async ({ page }) => {
  await page.goto(HARNESS);
  await mount(page);

  // Straight through: citra.js → runtime proxy → smart-app-service → discovery
  // → dept-MCP → Postgres. Nothing stubbed anywhere in that chain.
  await expect
    .poll(() => shellText(page), { timeout: 60_000 })
    .toContain("LAN-2026-000001");
});

test("@embedlive no dead 'Back to the list' control", async ({ page }) => {
  await page.goto(HARNESS);
  await mount(page);
  await expect.poll(() => shellText(page), { timeout: 60_000 }).not.toBe("");

  // router.back() is a no-op in an embed — there is no queue to return to. A
  // visible button that does nothing reads as a broken integration.
  const visible = await page.evaluate(() => {
    const sr = (document.querySelector("#citra-decision") as HTMLElement).shadowRoot!;
    const btn = sr.querySelector(".dt-head > button.q-btn:first-child");
    return btn ? getComputedStyle(btn as Element).display : "absent";
  });
  expect(visible === "none" || visible === "absent").toBe(true);
});

test("@embedlive styles survive a real cross-origin load", async ({ page }) => {
  await page.goto(HARNESS);
  await mount(page);
  await expect.poll(() => shellText(page), { timeout: 30_000 }).not.toBe("");

  const seen = await page.evaluate(() => {
    const shell = (
      document.querySelector("#citra-decision") as HTMLElement
    ).shadowRoot!.querySelector(".app-shell") as HTMLElement;
    const cs = getComputedStyle(shell);
    return {
      font: cs.fontFamily.toLowerCase(),
      fg: cs.getPropertyValue("--citra-fg").trim(),
      primary: shell.style.getPropertyValue("--citra-primary").trim(),
      hostLeak: Array.from(document.querySelectorAll("style"))
        .some((s) => (s.textContent ?? "").includes("--citra-")),
    };
  });

  expect(seen.font).not.toContain("georgia");   // host body font must not win
  expect(seen.fg).toBe("#0f172a");              // :root → :host rewrite worked
  expect(seen.primary).toBe("#0b5fff");         // host theme applied
  expect(seen.hostLeak).toBe(false);            // nothing leaked outward
});
