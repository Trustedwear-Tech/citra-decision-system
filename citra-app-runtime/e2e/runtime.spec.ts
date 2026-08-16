import { test, expect } from "@playwright/test";
import { appUrl } from "./auth";

/**
 * Runtime click-through suite — the INTERACTION layer the in-builder vision
 * gate (`citra_visual_review`) cannot cover. The vision gate renders each page
 * and critiques the screenshot ("does it look right"); it never clicks a row,
 * drills into a detail page, or submits a form. Every prod-breaking bug we hit
 * (detail-panel 404, 500-row freeze, runtimeFetch 403, form-submit 422) lived
 * in exactly that gap. These tests exercise it against a PINNED published app.
 *
 * Assertions are on DOM + behaviour (not pixels): vision sees "rendered", these
 * see "works". Selectors are the runtime's stable classes (PanelRenderer.tsx):
 *   .q-card / .q-card.clickable  → queue rows
 *   .cf-submit, input[type=file] → form submit + upload
 *   "Submitted" / "Run failed"   → submit result toast
 *
 * App under test (override with E2E_SLUG): bsphcl-meter-inspection
 *   pages: inspections (queue) · new-inspection (form) · detail (row-click)
 *
 * Tags: @read = no LLM credits needed (read-only). The form-submit test drives
 * an agent_action on_submit (→ /run → model) and needs OpenRouter credits.
 */
const SLUG = process.env.E2E_SLUG || "bsphcl-meter-inspection";
const QUEUE_PAGE = process.env.E2E_QUEUE_PAGE || "inspections";
const FORM_PAGE = process.env.E2E_FORM_PAGE || "new-inspection";

// A real 1x1 PNG — the bytes the file picker would produce.
const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

const ERROR_TEXT = /not found|app not found|HTTP 404|HTTP 5\d\d|Run failed|Failed \(|is not of type|user_org=None/i;

test.describe("Smart App runtime — interaction layer", () => {
  test("@read queue renders live rows (data-binding + auth handoff)", async ({ page }) => {
    // A panel-data request must succeed — catches the runtimeFetch/JWT (403) class.
    const dataResp = page
      .waitForResponse(
        (r) => /\/api\/apps\/.+\/(data|panel|document)/.test(r.url()) && r.request().method() !== "OPTIONS",
        { timeout: 30_000 }
      )
      .catch(() => null);

    await page.goto(appUrl(SLUG, QUEUE_PAGE));
    await page.waitForSelector(".q-card", { timeout: 30_000 });

    const rows = await page.locator(".q-card").count();
    expect(rows, "queue should render at least one live row").toBeGreaterThan(0);

    const resp = await dataResp;
    if (resp) expect(resp.status(), `panel-data ${resp.url()}`).toBeLessThan(400);

    await expect(page.locator("body")).not.toContainText(ERROR_TEXT);
  });

  test("@read clicking a queue row opens its detail page (drill-down + _bind_app_env)", async ({ page }) => {
    await page.goto(appUrl(SLUG, QUEUE_PAGE));
    await page.waitForSelector(".q-card.clickable", { timeout: 30_000 });

    const before = page.url();
    await page.locator(".q-card.clickable").first().click();

    // Client navigation to the (hidden) detail page with an ?id= param.
    await page.waitForFunction((u) => location.href !== u, before, { timeout: 15_000 });

    // The detail must actually resolve — this is the exact path that 404'd
    // ("app not found") before the get_detail_data _bind_app_env fix.
    await expect(page.locator("body")).not.toContainText(ERROR_TEXT);
    // Some record content rendered (field rows or an attachment section).
    await expect(page.locator(".q-field, .q-detail, [class*='detail'], [class*='attachment']").first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("new-inspection form submits with a file upload (no 422) [needs credits]", async ({ page }) => {
    await page.goto(appUrl(SLUG, FORM_PAGE));
    await page.waitForSelector(".cf-submit, form", { timeout: 30_000 });

    // Best-effort fill of text/number inputs so required fields are satisfied.
    for (const inp of await page.locator("form input:not([type=file]):not([type=checkbox]), form textarea").all()) {
      try {
        const type = await inp.getAttribute("type");
        await inp.fill(type === "number" ? "1" : "PROBE-2026");
      } catch {
        /* select/date controls handled by their own UI — skip */
      }
    }

    // The file upload — the exact path that emitted {filename,content_type,data}
    // and 422'd against a string column before the #3b blob fallback.
    const fileInput = page.locator("input[type=file]").first();
    if ((await fileInput.count()) > 0) {
      await fileInput.setInputFiles({
        name: "evidence.png",
        mimeType: "image/png",
        buffer: Buffer.from(PNG_B64, "base64"),
      });
    }

    await page.locator(".cf-submit, button[type=submit]").first().click();

    // Either a success toast or a failure toast will appear — assert success,
    // and explicitly that NO submit/upload validation error surfaced.
    const success = page.locator("text=/Submitted/i");
    const failure = page.locator("text=/Run failed|Failed \\(|is not of type|input validation failed/i");
    await expect(success.or(failure).first()).toBeVisible({ timeout: 45_000 });
    await expect(failure, "submit must not 422 (file upload fallback should store the blob)").toHaveCount(0);
  });
});
