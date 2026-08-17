// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Embed bundle checks — @embed
 *
 * Unlike the rest of this suite these need NO backend: the bundle is loaded
 * from a file:// page and driven through its REAL public API (`Citra.init()` +
 * `mount()`), with the spec and panel fetches intercepted. That is the point —
 * proving the runtime's own PanelRenderer works outside Next.js, inside a
 * shadow root, on a page we do not control.
 *
 *   cd e2e && npx playwright test -g "@embed"
 *
 * Build the bundle first: `npm run build:embed` in the runtime root.
 */
import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const RUNTIME_ROOT = path.resolve(__dirname, "..");
const BUNDLE = path.join(RUNTIME_ROOT, "public", "v1", "citra.js");
const HARNESS =
  "file:///" +
  path.join(RUNTIME_ROOT, "embed", "dev", "test.html").replace(/\\/g, "/");

/** A REAL published app — chart + queue + detail — not a hand-written fixture
 *  that could quietly drift from what the builder actually emits. */
const FIXTURE = path.resolve(
  RUNTIME_ROOT,
  "..",
  "demo-data/tenants/acme-bank/apps/01_loan_triage.json",
);

function loadFixture() {
  const f = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
  return {
    slug: f.app_spec.slug,
    app_spec: f.app_spec,
    agent_spec: f.agent_spec,
  };
}

/**
 * Stand in for the Citra runtime.
 *
 * `/api/embed/{key}/spec` is the Phase-4 endpoint the bundle already calls;
 * serving it here means Phase 2 is exercised through the real code path rather
 * than a test-only seam, and Phase 4 only has to make the server agree.
 *
 * Panel fetches are served too: without data the panels render their
 * "Failed to fetch" state, which proves they MOUNTED but not that they RENDER —
 * and the chart panel would bail before reaching the aliased echarts component,
 * leaving the exclusion path untested.
 */
async function stubApi(page: Page, opts: { specStatus?: number } = {}) {
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();

    if (/\/api\/embed\/[^/]+\/spec/.test(url)) {
      if (opts.specStatus && opts.specStatus !== 200) {
        return route.fulfill({
          status: opts.specStatus,
          contentType: "application/json",
          body: JSON.stringify({ detail: "nope" }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(loadFixture()),
      });
    }
    if (url.includes("/api/data/")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          rows: [
            { application_id: "LN-4471", applicant_name: "R. Iyer",
              product: "Home Loan", amount: 4500000, status: "under_review" },
            { application_id: "LN-4472", applicant_name: "S. Nair",
              product: "Auto Loan", amount: 850000, status: "under_review" },
          ],
          total: 2,
        }),
      });
    }
    if (url.includes("/api/detail/")) {
      const id = new URL(url).searchParams.get("id") ?? "LN-4471";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          panel_id: "det",
          record_id: id,
          record: { application_id: id, applicant_name: "R. Iyer",
                    product: "Home Loan", amount: 4500000 },
          record_columns: ["application_id", "applicant_name", "product", "amount"],
          sections: [],
        }),
      });
    }
    return route.fulfill({
      status: 200, contentType: "application/json", body: "{}",
    });
  });
}

/**
 * The card's own root — `shadowRoot.textContent` also returns the injected
 * stylesheet, so assertions must read the shell, not the whole root.
 *
 * Passed to `page.evaluate` as the function itself (with the selector as its
 * single argument), never referenced from inside another arrow — a closure over
 * this name does not exist in the browser context.
 */
const SHELL_TEXT = (sel: string) => {
  const host = document.querySelector(sel) as HTMLElement;
  return host?.shadowRoot?.querySelector(".app-shell")?.textContent ?? "";
};
const shellText = (page: Page, sel = "#citra-decision") =>
  page.evaluate(SHELL_TEXT, sel);

async function mountCard(page: Page, opts: Record<string, unknown> = {}) {
  await page.evaluate((o) => (window as any).mountCard(o), opts);
}

test.beforeAll(() => {
  if (!fs.existsSync(BUNDLE)) {
    throw new Error(
      `embed bundle missing at ${BUNDLE} — run \`npm run build:embed\` first`,
    );
  }
});

// ── Mounting ────────────────────────────────────────────────────────────────

test("@embed init + mount renders a published AppSpec outside Next.js", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);

  await expect
    .poll(() => shellText(page), { timeout: 15_000 })
    .toContain("Loan Triage");
  expect(errors, `page errors: ${errors.join("\n")}`).toHaveLength(0);
});

test("@embed renders every panel the page composes", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);

  // Panels nest inside .panel-host, not directly under .app-page.
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            (document.querySelector("#citra-decision") as HTMLElement).shadowRoot!
              .querySelectorAll(".panel-host > *").length,
        ),
      { timeout: 15_000 },
    )
    .toBeGreaterThanOrEqual(3);
});

test("@embed renders live data from the API", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);

  await expect
    .poll(() => shellText(page), { timeout: 15_000 })
    .toContain("LN-4471");
});

test("@embed excluded chart panel fails loud, not blank", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });

  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);

  // The fixture contains a chart panel, which the embed build aliases away.
  // A silent empty box in a bank's production screen would read as "the
  // integration is broken" — it has to say what happened, on screen AND in
  // the console.
  await expect
    .poll(() => shellText(page), { timeout: 15_000 })
    .toContain("can't be shown here");
  expect(consoleErrors.join("\n")).toContain("not supported in embedded cards");
});

// ── Isolation ───────────────────────────────────────────────────────────────

test("@embed styles stay inside the shadow root", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);
  await expect.poll(() => shellText(page), { timeout: 15_000 }).not.toBe("");

  const leak = await page.evaluate(() => {
    const hostStyleText = Array.from(document.querySelectorAll("style"))
      .map((s) => s.textContent ?? "")
      .join("\n");
    const shadowStyles = (
      document.querySelector("#citra-decision") as HTMLElement
    ).shadowRoot!.querySelectorAll("style").length;
    return { hostHasCitraVars: hostStyleText.includes("--citra-"), shadowStyles };
  });

  expect(leak.shadowStyles).toBeGreaterThan(0);
  expect(leak.hostHasCitraVars).toBe(false);
});

test("@embed host page styles do not bleed into the card", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);
  await expect.poll(() => shellText(page), { timeout: 15_000 }).not.toBe("");

  const fontFamily = await page.evaluate(() => {
    const shell = (
      document.querySelector("#citra-decision") as HTMLElement
    ).shadowRoot!.querySelector(".app-shell") as HTMLElement;
    return getComputedStyle(shell).fontFamily;
  });

  // The harness sets Georgia serif on body. Inherited properties DO cross a
  // shadow boundary, so the card must set its own family rather than rely on
  // isolation alone.
  expect(fontFamily.toLowerCase()).not.toContain("georgia");
});

test("@embed design tokens resolve inside the shadow root", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);
  await expect.poll(() => shellText(page), { timeout: 15_000 }).not.toBe("");

  // globals.css declares every token under `:root`, which matches NOTHING in a
  // shadow root. Unrewritten, all of these come back empty and the card
  // silently inherits the host page's colours — it still renders, so nothing
  // looks broken until a customer says it looks foreign.
  const tokens = await page.evaluate(() => {
    const shell = (
      document.querySelector("#citra-decision") as HTMLElement
    ).shadowRoot!.querySelector(".app-shell") as HTMLElement;
    const cs = getComputedStyle(shell);
    return {
      fg: cs.getPropertyValue("--citra-fg").trim(),
      surface: cs.getPropertyValue("--citra-surface").trim(),
      color: cs.color,
    };
  });

  expect(tokens.fg).toBe("#0f172a");
  expect(tokens.surface).toBe("#ffffff");
  expect(tokens.color).toBe("rgb(15, 23, 42)");
});

test("@embed theme overrides reach the CSS variables", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page, {
    theme: { primary: "#0b5fff", radius: 6, density: "compact" },
  });
  await expect.poll(() => shellText(page), { timeout: 15_000 }).not.toBe("");

  const applied = await page.evaluate(() => {
    const shell = (
      document.querySelector("#citra-decision") as HTMLElement
    ).shadowRoot!.querySelector(".app-shell") as HTMLElement;
    return {
      primary: shell.style.getPropertyValue("--citra-primary").trim(),
      radius: shell.style.getPropertyValue("--r-sm").trim(),
      density: shell.dataset.density,
    };
  });

  expect(applied).toEqual({ primary: "#0b5fff", radius: "6px", density: "compact" });
});

// ── Lifecycle ───────────────────────────────────────────────────────────────

test("@embed update() re-targets the card at another record", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);
  await expect
    .poll(() => shellText(page), { timeout: 15_000 })
    .toContain("LN-4471");

  await page.evaluate(() => (window as any).__mount.update({ recordId: "LN-4472" }));

  // The detail request must carry the NEW id — a stale ?id= is how a queue→
  // detail navigation silently shows the wrong customer's record.
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            (document.querySelector("#citra-decision") as HTMLElement)
              .shadowRoot!.querySelector(".app-shell")?.textContent ?? "",
        ),
      { timeout: 15_000 },
    )
    .toContain("LN-4472");
});

test("@embed destroy() removes everything it added", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page);
  await expect.poll(() => shellText(page), { timeout: 15_000 }).not.toBe("");

  const after = await page.evaluate(() => {
    (window as any).__mount.destroy();
    return (document.querySelector("#citra-decision") as HTMLElement)
      .shadowRoot!.childNodes.length;
  });
  expect(after).toBe(0);
});

test("@embed two cards on one page keep separate state", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);
  await mountCard(page, { target: "#citra-decision", recordId: "LN-4471" });
  await page.evaluate(() =>
    (window as any).__citra.mount("#citra-decision-2", {
      embed: "emb_test_harness",
      recordId: "LN-4472",
    }),
  );

  // Each mount owns its own shadow root and its own param store; a shared
  // module-level store would show the same record in both.
  await expect
    .poll(() => shellText(page), { timeout: 15_000 })
    .toContain("LN-4471");
  await expect
    .poll(() => shellText(page, "#citra-decision-2"), { timeout: 15_000 })
    .toContain("LN-4472");
});

// ── Failure surfaces ────────────────────────────────────────────────────────

test("@embed a bad embed key reports a usable error", async ({ page }) => {
  await stubApi(page, { specStatus: 404 });
  await page.goto(HARNESS);
  await mountCard(page);

  await expect
    .poll(() => page.evaluate(() => (window as any).citraEvents.error), {
      timeout: 15_000,
    })
    .toEqual([expect.stringContaining("Check the embed key")]);
});

test("@embed a load failure is VISIBLE, not an empty box", async ({ page }) => {
  await stubApi(page, { specStatus: 500 });
  await page.goto(HARNESS);
  await mountCard(page);

  // onError tells the HOST, but a host is free to ignore it — and then the
  // officer stares at a blank rectangle where a decision should be. A blank
  // card reads as "the integration is broken" and costs a support cycle.
  await expect
    .poll(() => shellText(page), { timeout: 15_000 })
    .toContain("could not be loaded");

  const role = await page.evaluate(
    () =>
      (document.querySelector("#citra-decision") as HTMLElement).shadowRoot!
        .querySelector(".panel-error")
        ?.getAttribute("role") ?? null,
  );
  expect(role).toBe("alert");
});

test("@embed every request carries the embed key, not just the first", async ({
  page,
}) => {
  const seen: { url: string; key: string | null }[] = [];
  await stubApi(page);
  // Registered AFTER stubApi on purpose: Playwright runs the most recently
  // added matching handler first, so this observes and then falls through to
  // the stub. Registering it first would mean the stub fulfils and this never
  // runs.
  await page.route("**/api/**", async (route) => {
    seen.push({
      url: route.request().url(),
      key: route.request().headers()["x-citra-embed-key"] ?? null,
    });
    await route.fallback();
  });
  await page.goto(HARNESS);
  await mountCard(page);
  await expect.poll(() => shellText(page), { timeout: 15_000 }).not.toBe("");

  // Only the FIRST call names the key in its path; run/data/detail/approve are
  // slug-addressed, and slug resolution upstream is prod-first. A PROMOTED app
  // exists in both stores, so without this header on EVERY call a bank's UAT
  // card silently reads and writes PRODUCTION records.
  expect(seen.length).toBeGreaterThan(1);
  const slugAddressed = seen.filter((r) => !/\/api\/embed\//.test(r.url));
  expect(slugAddressed.length).toBeGreaterThan(0);
  for (const r of slugAddressed) {
    expect(r.key, `no embed key on ${r.url}`).toBe("emb_test_harness");
  }
});

test("@embed <citra-decision> element renders and re-targets", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);

  // The declarative path for hosts whose templates are easier to extend than
  // their scripts. It was shipped in Phase 2 with no coverage at all.
  await page.evaluate(async () => {
    await (window as any).citraReady;
    (window as any).Citra.init({
      baseUrl: "https://citra.example.test",
      getToken: () => "harness-token",
    });
    const el = document.createElement("citra-decision");
    el.id = "declarative";
    el.setAttribute("embed", "emb_test_harness");
    el.setAttribute("record-id", "LN-4471");
    document.querySelector(".host-chrome")!.appendChild(el);
  });

  await expect
    .poll(() => shellText(page, "#declarative"), { timeout: 15_000 })
    .toContain("LN-4471");

  // Changing the attribute must re-target, not silently keep the old record.
  await page.evaluate(() =>
    document.querySelector("#declarative")!.setAttribute("record-id", "LN-4472"),
  );
  await expect
    .poll(() => shellText(page, "#declarative"), { timeout: 15_000 })
    .toContain("LN-4472");
});

test("@embed mount() without recordId fails loud", async ({ page }) => {
  await stubApi(page);
  await page.goto(HARNESS);

  const err = await page.evaluate(async () => {
    await (window as any).citraReady;
    const c = (window as any).Citra.init({ baseUrl: "https://citra.example.test" });
    try {
      c.mount("#citra-decision", { embed: "emb_test_harness" });
      return "no error";
    } catch (e) {
      return String(e);
    }
  });
  expect(err).toContain("requires `recordId`");
});
