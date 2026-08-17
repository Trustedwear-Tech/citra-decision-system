// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Page-resolution helpers — abstract over single-page vs. multi-page AppSpec.
 *
 * The runtime accepts both shapes:
 *  - Legacy single-page: `AppSpec.panels[]` non-empty; `pages[]` absent/empty.
 *  - Multi-page:        `AppSpec.pages[]` non-empty; `panels[]` empty.
 *
 * Every consumer that needs to render a page should go through `resolvePage`
 * so neither route handlers nor components need to know which mode they're in.
 */

import type { AppSpec, Page } from "@/types/spec";

const SYNTHETIC_PAGE_ID = "main";

/** Returns true when the spec uses the multi-page model. */
export function isMultiPage(spec: AppSpec): boolean {
  return Array.isArray(spec.pages) && spec.pages.length > 0;
}

/** Flattens the spec into a list of pages, synthesising one for the legacy single-page case. */
export function listPages(spec: AppSpec): Page[] {
  if (isMultiPage(spec)) return spec.pages!;
  return [
    {
      id: SYNTHETIC_PAGE_ID,
      path: "/",
      title: spec.title,
      layout: "grid",
      panels: spec.panels ?? [],
    },
  ];
}

/** Returns the id of the page that '/' should resolve to. */
export function defaultPageId(spec: AppSpec): string {
  if (!isMultiPage(spec)) return SYNTHETIC_PAGE_ID;
  if (spec.navigation?.default_page) return spec.navigation.default_page;
  const slashPage = spec.pages!.find((p) => p.path === "/");
  if (slashPage) return slashPage.id;
  return spec.pages![0].id;
}

/**
 * Resolves a URL path segment to a Page. Accepts:
 *  - undefined / empty / "main"  → default page
 *  - a page id matching a page in pages[]
 *
 * Returns `null` when nothing matches (caller should 404).
 */
export function resolvePage(
  spec: AppSpec,
  pagePathSegments: string[] | undefined,
): Page | null {
  const pages = listPages(spec);
  if (!pagePathSegments || pagePathSegments.length === 0) {
    const id = defaultPageId(spec);
    return pages.find((p) => p.id === id) ?? pages[0] ?? null;
  }
  const first = pagePathSegments[0];
  // Try direct id match.
  const byId = pages.find((p) => p.id === first);
  if (byId) return byId;
  // Try explicit `path` match (allows "/" or "/inbox" overrides).
  const joined = "/" + pagePathSegments.join("/");
  const byPath = pages.find((p) => p.path === joined);
  if (byPath) return byPath;
  return null;
}

/** Builds the in-app URL for a page (relative to /{slug}). */
export function pageHref(slug: string, page: Page): string {
  const id = page.id === SYNTHETIC_PAGE_ID ? "" : `/${page.id}`;
  return `/${slug}${id}`;
}

/**
 * Substitutes navigate-target param templates against a context.
 *
 *  - `{row.<field>}`    — from `ctx.row`
 *  - `{result.<field>}` — from `ctx.result`
 *  - `{form.<field>}`   — from `ctx.form`
 *  - `{param.<field>}`  — from `ctx.params` (page params already in URL)
 *
 * Unknown templates are left literal (the caller can decide what to do).
 */
export function substituteParams(
  params: Record<string, string> | undefined,
  ctx: {
    row?: Record<string, unknown>;
    result?: Record<string, unknown>;
    form?: Record<string, unknown>;
    params?: Record<string, unknown>;
  },
): Record<string, string> {
  if (!params) return {};
  // EMBEDDED, global substitution — consistent with tool-button arg templating
  // (substituteToolArgs). The old anchored ^{…}$ regex only matched a value
  // that was ENTIRELY one template, so "ORD-{row.id}" or "{row.id}/edit" leaked
  // the literal template into the URL. An unresolved ref → "" (and is warned),
  // so a single all-unresolved template still drops the param.
  const TEMPLATE = /\{(row|result|form|param)\.([a-zA-Z0-9_.]+)\}/g;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(params)) {
    out[key] = value.replace(TEMPLATE, (_full: string, scope: string, field: string) => {
      // The `param` scope maps to ctx.params (plural) — without this remap
      // {param.X} never resolved (ctx["param"] is undefined).
      const ctxKey = scope === "param" ? "params" : scope;
      const src = (ctx as Record<string, Record<string, unknown> | undefined>)[ctxKey];
      if (src && field in src) {
        const v = src[field];
        return v === null || v === undefined ? "" : String(v);
      }
      if (typeof console !== "undefined") {
        // eslint-disable-next-line no-console
        console.warn(
          `[citra-app] navigate param "${key}" references unresolved "{${scope}.${field}}" ` +
            `(field not in ${scope} scope) — substituting ""`,
        );
      }
      return "";
    });
  }
  return out;
}

/** Build a fully-qualified navigate URL with substituted params. */
export function buildNavigateHref(
  slug: string,
  page: Page,
  params: Record<string, string>,
): string {
  const base = pageHref(slug, page);
  const entries = Object.entries(params).filter(([, v]) => v !== "");
  if (entries.length === 0) return base;
  const qs = new URLSearchParams(entries).toString();
  return `${base}?${qs}`;
}
