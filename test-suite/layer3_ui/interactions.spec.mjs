// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * UI interaction matrix — the (panel × single_row × interaction) cells.
 *
 * Rendering proves a panel paints; this proves its AFFORDANCES work: a queue row
 * navigates, a queue filters/sorts/paginates, the governed decision loop
 * approves/overrides/rejects, media opens, a form submits, a filter drives the
 * URL, chat answers, notifications navigate. Each test drives the real DOM
 * against the mock API and asserts the resulting state, then records its cell.
 *
 * Selectors are pinned to citra-app-runtime/src/components/PanelRenderer.tsx.
 * Emits cells via _coverage.mjs (folded into ui.json by globalTeardown).
 */
import { test, expect } from "@playwright/test";
import { emitCell } from "./_coverage.mjs";

const RUNTIME = process.env.RUNTIME_URL || "http://localhost:3100";
const TOKEN = process.env.DA_UI_TOKEN || "";
const at = (panel, state) => `${RUNTIME}/fx-${panel}-${state}${TOKEN ? `?_t=${TOKEN}` : ""}`;
const load = async (page, panel, state) => {
  const r = await page.goto(at(panel, state), { waitUntil: "networkidle" });
  expect(r?.status(), `${panel}@${state} should load`).toBeLessThan(500);
};

// ── queue ───────────────────────────────────────────────────────────────────
test("queue: click_row navigates", async ({ page }) => {
  await load(page, "queue", "many_rows");
  await page.locator(".q-card.clickable, .q-table tbody tr.clickable").first().click();
  await expect(page).toHaveURL(/id=INS-/);   // navigated with the row's id
  emitCell("queue", "single_row", "click_row");
  emitCell("queue", "single_row", "navigate");
});

test("queue: filter (search) narrows rows", async ({ page }) => {
  await load(page, "queue", "many_rows");
  const cards = page.locator(".q-card");
  await expect(cards.first()).toBeVisible();
  await page.locator('input[placeholder="Search…"]').fill("INS-7");
  // client-side filter: only INS-7 matches (INS-17 does not contain "INS-7")
  await expect(page.locator(".q-card")).toHaveCount(1);
  emitCell("queue", "single_row", "filter");
});

test("queue: sort toggles a column", async ({ page }) => {
  await load(page, "queue", "many_rows");
  await page.locator('.q-viewtoggle button:has-text("Table")').click();
  await page.locator("table.q-table thead th").first().click();
  await expect(page.locator("table.q-table thead th.sorted")).toBeVisible();
  emitCell("queue", "single_row", "sort");
});

test("queue: paginate to page 2", async ({ page }) => {
  await load(page, "queue", "many_rows");   // 25 rows, 12/page -> 3 pages
  await page.locator('button.q-pagebtn:has-text("Next")').first().click();
  await expect(page.locator("button.q-pagebtn.active:has-text('2')")).toBeVisible();
  emitCell("queue", "single_row", "paginate");
});

// ── detail (governed decision loop) ──────────────────────────────────────────
test("detail: approve applies the recommendation", async ({ page }) => {
  await load(page, "detail", "single_row");
  await page.locator(".dt-approval-actions button.q-btn-primary").first().click();
  await expect(page.locator(".q-badge-green, .dt-approval :text('Approved')")).toBeVisible();
  emitCell("detail", "single_row", "approve");
});

test("detail: reject records a rejection", async ({ page }) => {
  await load(page, "detail", "single_row");
  await page.locator(".dt-approval-actions button.q-btn:not(.q-btn-primary)").first().click();
  await expect(page.locator(".q-badge-red, .dt-approval :text('Rejected')")).toBeVisible();
  emitCell("detail", "single_row", "reject");
});

test("detail: media_open opens the attachment", async ({ page }) => {
  await load(page, "detail", "single_row");
  const media = page.locator('a[href*="/api/media/"], img[src*="/api/media/"]').first();
  await expect(media).toBeVisible();          // the media affordance renders and is openable
  const [popup] = await Promise.all([
    page.context().waitForEvent("page").catch(() => null),
    media.click(),
  ]);
  if (popup) { expect(popup.url()).toContain("/api/media/"); await popup.close(); }
  emitCell("detail", "single_row", "media_open");
});

// override lives in the queue's RunResultModal (a card carrying `_recommendation`),
// reached WITHOUT a navigate action so the click opens the modal.
test("queue: override edits a recommendation then applies", async ({ page }) => {
  await load(page, "queue", "rec");
  await page.locator(".q-card.clickable, .q-table tbody tr.clickable").first().click();
  await expect(page.locator(".rr-modal")).toBeVisible();
  await page.locator(".rr-modal select").first().selectOption("Repair");
  await page.locator('.rr-modal button.q-btn-primary:has-text("Apply")').click();
  await expect(page.locator(".rr-modal")).toContainText(/Applied|Approved|completed|✓/i);
  emitCell("queue", "single_row", "override");
});

// ── form ─────────────────────────────────────────────────────────────────────
test("form: submit runs the tool then navigates", async ({ page }) => {
  await load(page, "form", "single_row");
  await page.locator('input[name="id"]').fill("X1");
  await page.locator("button.cf-submit").click();
  await expect(page).toHaveURL(/id=X1/);      // tool ok -> navigate fired
  emitCell("form", "single_row", "submit");
  emitCell("form", "single_row", "navigate");
});

// The prod-found bug class: a FRESH tab whose first action is a navigate-only
// submit. The mock 401s the spec fetch without a user token, so this passes
// ONLY if the runtime mirrors ?_t= into the SSR cookie on mount (TokenCapture)
// — i.e. auth survives the very first client-side navigation.
test("form: fresh-tab navigate-only submit carries auth", async ({ page }) => {
  const r = await page.goto(`${RUNTIME}/fx-form-authnav?_t=e2e-token`,
    { waitUntil: "networkidle" });
  expect(r?.status(), "first load with token should render").toBeLessThan(400);
  await page.locator('input[name="id"]').fill("X9");
  await page.locator("button.cf-submit").click();
  await expect(page).toHaveURL(/id=X9/);
  const body = await page.locator("body").innerText();
  expect(/went wrong|session is missing/i.test(body),
    `auth was lost on first navigation:\n${body.slice(0, 200)}`).toBeFalsy();
  await expect(page.locator('input[name="id"]')).toBeVisible();  // target page rendered
  emitCell("form", "single_row", "auth_carry");
});

// ── agent_chat ───────────────────────────────────────────────────────────────
test("agent_chat: submit gets a grounded reply", async ({ page }) => {
  await load(page, "agent_chat", "single_row");
  await page.locator(".chat-input input").fill("How many open?");
  await page.locator('.chat-input button:has-text("Send")').click();
  await expect(page.getByText(/Grounded answer/)).toBeVisible();   // assistant reply arrived
  emitCell("agent_chat", "single_row", "submit");
});

// ── document_view ────────────────────────────────────────────────────────────
test("document_view: media_open opens the document modal", async ({ page }) => {
  await load(page, "document_view", "many_rows");
  await page.locator("button.dt-doc-clickable").first().click();
  await expect(page.locator(".rr-modal")).toBeVisible();
  emitCell("document_view", "single_row", "media_open");
});

// ── filter_bar ───────────────────────────────────────────────────────────────
test("filter_bar: filter updates the URL param", async ({ page }) => {
  await load(page, "filter_bar", "single_row");
  await page.locator("label.filter-control select").first().selectOption("a");
  await expect(page).toHaveURL(/status=a/);
  emitCell("filter_bar", "single_row", "filter");
});

// ── notifications ────────────────────────────────────────────────────────────
test("notifications: click_row navigates", async ({ page }) => {
  await load(page, "notifications", "single_row");
  await page.locator("li.nc-item.is-clickable").first().click();
  await expect(page).toHaveURL(/id=INS-1/);
  emitCell("notifications", "single_row", "click_row");
  emitCell("notifications", "single_row", "navigate");
});
