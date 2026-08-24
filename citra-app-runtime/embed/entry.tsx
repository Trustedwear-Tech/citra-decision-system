// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Embed bundle entry — the `Citra` global a bank's page talks to.
 *
 *   const citra = Citra.init({ getToken: () => myApp.citraToken() });
 *   citra.mount('#citra-decision', {
 *     embed: 'emb_live_9c21b4…',
 *     recordId: currentApplication.id,
 *     theme: { primary: '#0b5fff', radius: 6, density: 'compact' },
 *     onDecision: (d) => refreshCaseHeader(d),
 *   });
 *
 * The host writes plain JavaScript. React is an implementation detail of this
 * bundle — no bundler, no npm install, no awareness that React exists.
 *
 * Everything the renderer needs from Next.js arrives by build-time alias (see
 * scripts/build-embed.mjs), so no file the app build compiles is modified.
 */
import { createRoot, type Root } from "react-dom/client";
import PageBody from "@/components/PageBody";
import { listPages } from "@/lib/pages";
import type { AgentSpec, AppSpec, Page } from "@/types/spec";
import {
  configureRuntimeFetch,
  runtimeFetch,
  setEmbedKey,
  subscribe,
  type ApiEvent,
} from "./shims/runtimeFetch";
import { createParamStore, ParamStoreProvider } from "./shims/navigation";
import { setEmbedPortalRoot } from "./shims/react-dom";
// Bundled as raw text by esbuild (loader: { ".css": "text" }) and injected
// into the shadow root, so the card is styled without leaking a single rule
// into the host page.
import cssText from "@/app/globals.css";
// Re-applies the base that globals.css puts on <body> (which does not exist in
// a shadow root) and repairs the --font-inter chain. Order matters: after.
import resetCssText from "./embed-reset.css";

// ── Public types ────────────────────────────────────────────────────────────

export interface EmbedTheme {
  primary?: string;
  accent?: string;
  font?: string;
  radius?: number;
  density?: "comfortable" | "compact";
}

export interface DecisionEvent {
  caseId: string | null;
  recordId: string | null;
  action: string;
  reason?: string | null;
  reasonText?: string | null;
  correlationId: string | null;
  appliedWrites?: unknown[];
  raw: unknown;
}

export interface RecommendationEvent {
  recordId: string | null;
  correlationId: string | null;
  status: string | null;
  raw: unknown;
}

export interface ItemDecisionEvent {
  recordId: string | null;
  itemId: string | null;
  action: string | null;
  reason?: string | null;
  raw: unknown;
}

export interface InitOptions {
  /** Preferred. Called before every request, so the host can refresh a token
   *  without remounting. */
  getToken?: () => string | null | Promise<string | null>;
  /** Simpler alternative — static, so it dies with the token. */
  clientToken?: string;
  /** Defaults to the origin citra.js was served from. */
  baseUrl?: string;
  onError?: (err: Error) => void;
  // NOTE: no `locale` option. The renderer takes its locale from the AppSpec
  // (setAppLocale in lib/executiveTheme), so a host-supplied one would be
  // accepted and ignored — an option that silently does nothing is worse than
  // no option. Add it here only alongside the plumbing that honours it.
}

export interface MountOptions {
  /** Builder-published embed key (emb_test_… / emb_live_…). */
  embed: string;
  /** The record this decision is about. */
  recordId: string;
  theme?: EmbedTheme;
  // NOTE: no `readOnly` option. The renderer has no page-level read-only mode
  // — `readonly` in PanelRenderer is a form-FIELD format, nothing more — so a
  // flag here would render the full action controls anyway. In a decision card
  // that is not a cosmetic bug: a host asking for read-only and getting live
  // Approve/Reject is the opposite of what they requested. Ship it when the
  // renderer supports it.
  onDecision?: (e: DecisionEvent) => void;
  onItemDecision?: (e: ItemDecisionEvent) => void;
  onRecommendation?: (e: RecommendationEvent) => void;
  onError?: (err: Error) => void;
}

export interface CitraMount {
  update(next: { recordId: string }): void;
  refresh(): void;
  destroy(): void;
}

export interface CitraInstance {
  mount(target: string | Element, options: MountOptions): CitraMount;
  destroy(): void;
}

// ── Theme ───────────────────────────────────────────────────────────────────

/** Font STACKS only — never a webfont URL. A bank's CSP will block a font CDN,
 *  and a blocked font fails silently. Mirrors FONT_STACKS in the app's
 *  src/app/[slug]/[[...pagePath]]/page.tsx. */
const FONT_STACKS: Record<string, string> = {
  inter: '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
  "source-sans":
    '"Source Sans 3", "Source Sans Pro", "Segoe UI", system-ui, sans-serif',
  "ibm-plex": '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
  system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
};

function applyTheme(host: HTMLElement, appTheme: unknown, override?: EmbedTheme) {
  const spec = (appTheme ?? {}) as Record<string, string>;
  const primary = override?.primary ?? spec.primary;
  const accent = override?.accent ?? spec.accent;
  const font = override?.font ?? spec.font;

  if (primary) host.style.setProperty("--citra-primary", primary);
  if (accent) host.style.setProperty("--citra-accent", accent);
  if (font) {
    // A known token maps to its stack; anything else is treated as a literal
    // family the customer has installed, ahead of the platform stack.
    const stack =
      FONT_STACKS[font.toLowerCase()] ?? `"${font}", ${FONT_STACKS.system}`;
    host.style.setProperty("--citra-font", stack);
    host.style.setProperty("--font-sans", stack);
  }
  if (override?.radius !== undefined) {
    const r = Math.max(0, override.radius);
    host.style.setProperty("--r-sm", `${r}px`);
    host.style.setProperty("--r-md", `${Math.round(r * 1.5)}px`);
    host.style.setProperty("--r-lg", `${r * 2}px`);
    host.style.setProperty("--r-xl", `${Math.round(r * 2.5)}px`);
  }
  if (override?.density) host.dataset.density = override.density;
}

/**
 * Adapt the app stylesheet for a shadow root.
 *
 * `:root` matches the DOCUMENT root element. Inside a shadow root it matches
 * nothing at all — so globals.css's opening `:root { … }` block, which declares
 * EVERY design token (`--citra-primary`, `--citra-fg`, `--citra-surface`, the
 * elevation and radius scales, `--font-sans`), silently declares them into the
 * void. Every rule that then reads `var(--citra-fg)` becomes invalid at
 * computed-value time and falls back to inheriting from the host page.
 *
 * The visible symptom is subtle enough to be misdiagnosed: the card renders,
 * the layout is right, and only the colours and typography are wrong — they
 * come from whatever the bank's page happens to set. That is exactly the
 * "looks foreign, gets blocked by their UX team" failure this bundle exists to
 * avoid.
 *
 * `:host` is the shadow-root equivalent, and custom properties declared there
 * inherit down the whole shadow tree. globals.css contains exactly one `:root`
 * selector, so this is a targeted rewrite rather than a blanket substitution.
 */
export function adaptStylesheet(css: string): string {
  const adapted = css.replace(/(^|[\s,}])(:root)\b/g, "$1:host");
  if (adapted === css && css.includes(":root")) {
    // Fail loud rather than ship an unstyled card: if the selector shape ever
    // changes and this stops matching, the tokens go dead again.
    throw new Error(
      "[citra-embed] globals.css contains :root but the :host rewrite matched " +
        "nothing — the design tokens would be undefined inside the shadow root.",
    );
  }
  return adapted;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Does this API url address `slug` as a whole path segment? */
export function isForSlug(url: string, slug: string): boolean {
  let path: string;
  try {
    path = new URL(url, "http://x.invalid").pathname;
  } catch {
    path = url;
  }
  const segments = path.split("/").filter(Boolean).map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  return segments.includes(slug);
}

function resolveTarget(target: string | Element): Element {
  const el = typeof target === "string" ? document.querySelector(target) : target;
  if (!el) {
    // Fail loud: a typo'd selector otherwise looks identical to "the card
    // failed to load", and the developer debugs the wrong layer.
    throw new Error(
      `[citra-embed] mount target not found: ${String(target)}. Check the ` +
        `element exists in the DOM before calling mount().`,
    );
  }
  return el;
}

/** The origin this script was served from — the Citra runtime, by definition. */
function scriptOrigin(): string {
  const cur = document.currentScript as HTMLScriptElement | null;
  const src =
    cur?.src ||
    Array.from(document.getElementsByTagName("script"))
      .map((s) => s.src)
      .filter((s) => /\/v\d+\/citra(\.[\w.]+)?\.js(\?|$)/.test(s))
      .pop();
  if (!src) return "";
  try {
    return new URL(src, window.location.href).origin;
  } catch {
    return "";
  }
}
// Captured at PARSE time: document.currentScript is only meaningful while the
// script is executing, and is null by the time init() runs from a host callback.
const SCRIPT_ORIGIN = scriptOrigin();

export interface EmbedSpecResponse {
  slug: string;
  app_spec: AppSpec;
  agent_spec: AgentSpec;
  page_id?: string;
}

// ── init ────────────────────────────────────────────────────────────────────

let activeBaseUrl: string | null = null;

function init(options: InitOptions = {}): CitraInstance {
  const baseUrl = (options.baseUrl || SCRIPT_ORIGIN || "").replace(/\/+$/, "");
  if (!baseUrl) {
    throw new Error(
      "[citra-embed] could not determine the Citra base URL. Pass " +
        "Citra.init({ baseUrl: 'https://citra.yourbank.internal' }) — it is " +
        "normally inferred from the <script src> that loaded this bundle.",
    );
  }
  if (activeBaseUrl && activeBaseUrl !== baseUrl) {
    // runtimeFetch's config is process-wide (it is called as a plain function
    // from deep inside the renderer, where no React context is reachable), so
    // a second deployment on the same page would silently retarget the first
    // instance's requests. Refuse rather than corrupt.
    throw new Error(
      `[citra-embed] Citra.init() was already called with baseUrl ` +
        `"${activeBaseUrl}"; a second instance pointing at "${baseUrl}" on the ` +
        `same page is not supported. Use one instance and mount() more than once.`,
    );
  }
  activeBaseUrl = baseUrl;

  if (!options.getToken && !options.clientToken) {
    // Not fatal — an unauthenticated call fails at the API with a clear 401 —
    // but saying so here saves a debugging cycle.
    console.warn(
      "[citra-embed] neither getToken nor clientToken was supplied; API calls " +
        "will be unauthenticated. Pass getToken for a refreshable session.",
    );
  }

  configureRuntimeFetch({
    baseUrl,
    getToken: options.getToken ?? (() => options.clientToken ?? null),
  });

  const mounts = new Set<CitraMount>();

  const instance: CitraInstance = {
    mount(target, mountOptions) {
      const m = createMount(target, mountOptions, options, () => {
        mounts.delete(m);
      });
      mounts.add(m);
      return m;
    },
    destroy() {
      Array.from(mounts).forEach((m) => m.destroy());
      mounts.clear();
      // Release the single-instance latch so a page CAN re-init (e.g. an SPA
      // tearing down and rebuilding a route). Only the "two live instances at
      // once" case is refused.
      if (activeBaseUrl === baseUrl) activeBaseUrl = null;
      // <citra-decision> mounts through whichever instance was created first.
      // Leaving a destroyed one wired up means a later element silently mounts
      // against a torn-down instance and never renders.
      if (sharedInstance === instance) sharedInstance = null;
    },
  };
  return instance;
}

// ── mount ───────────────────────────────────────────────────────────────────

function createMount(
  target: string | Element,
  opts: MountOptions,
  initOpts: InitOptions,
  onDestroyed: () => void,
): CitraMount {
  if (!opts?.embed) throw new Error("[citra-embed] mount() requires `embed`.");
  if (!opts?.recordId) {
    throw new Error(
      "[citra-embed] mount() requires `recordId` — the record the decision is " +
        "about. The host application knows it; the card cannot guess it.",
    );
  }

  const el = resolveTarget(target);
  const reportError = (err: Error) => {
    (opts.onError ?? initOpts.onError ?? ((e: Error) => {
      console.error("[citra-embed]", e);
    }))(err);
  };

  const shadow = el.shadowRoot ?? (el.attachShadow
    ? el.attachShadow({ mode: "open" })
    : null);
  if (!shadow) {
    throw new Error(
      "[citra-embed] this browser does not support shadow DOM, which the " +
        "embedded card requires.",
    );
  }
  shadow.replaceChildren();

  const style = document.createElement("style");
  style.textContent = `${adaptStylesheet(cssText)}\n${resetCssText}`;
  shadow.appendChild(style);

  const host = document.createElement("div");
  host.className = "app-shell";
  shadow.appendChild(host);

  // Modals portal to document.body in the renderer; the react-dom shim
  // redirects them here so they stay inside the shadow root and keep their
  // styles. Appended AFTER the content host so overlays stack above it.
  const portalRoot = document.createElement("div");
  portalRoot.className = "citra-embed-portal";
  shadow.appendChild(portalRoot);
  setEmbedPortalRoot(portalRoot);

  let recordId = opts.recordId;
  let root: Root | null = null;
  let destroyed = false;
  let spec: EmbedSpecResponse | null = null;

  /**
   * Show the failure IN the card, not only on the console.
   *
   * `onError` tells the HOST something went wrong, but a host is free to
   * ignore it — and then the officer sees an empty rectangle where a decision
   * should be. That is the exact failure this whole surface is meant to avoid:
   * a blank card reads as "the integration is broken" and costs a support
   * cycle to trace. Uses the renderer's own `.panel-error` styling so it looks
   * like part of the card rather than a foreign alert.
   */
  function renderFailure(message: string) {
    if (destroyed) return;
    root?.unmount();
    root = null;
    host.replaceChildren();
    const box = document.createElement("div");
    box.className = "panel-error";
    box.setAttribute("role", "alert");
    const title = document.createElement("strong");
    title.textContent = "This decision card could not be loaded.";
    const detail = document.createElement("span");
    detail.textContent = message;
    box.append(title, detail);
    host.appendChild(box);
  }

  // ── Host callbacks, wired to API traffic ──────────────────────────────────
  //
  // The approve button lives inside PanelRenderer, which this build does not
  // modify. Observing the API gives the same signal: a run produces a
  // correlation id, and an approve on that id is the officer's decision. We
  // remember the ids THIS mount produced so two cards on one page never report
  // each other's decisions.
  const ownCorrelationIds = new Set<string>();

  const unsubscribe = subscribe((e: ApiEvent) => {
    if (destroyed || !spec) return;
    const body = (e.body ?? {}) as Record<string, unknown>;
    const slug = spec.slug;
    // Match the slug as a whole PATH SEGMENT, not a substring. Every embed API
    // route carries it as one (/api/run/{slug}, /api/data/{slug}/{panel},
    // /api/apps/{slug}/approve/{cid}), and a substring test would let an app
    // called "loan-triage" fire its host's callbacks for "loan-triage-v2" —
    // the host would then refresh the wrong screen.
    if (!isForSlug(e.url, slug)) return;

    // A run: the recommendation the officer is being shown.
    if (e.method === "POST" && /\/api\/run\//.test(e.url) && e.ok) {
      const cid = (body.correlation_id as string) || null;
      if (cid) ownCorrelationIds.add(cid);
      opts.onRecommendation?.({
        recordId,
        correlationId: cid,
        status: (body.status as string) ?? null,
        raw: body,
      });
      return;
    }

    // Per-item accept/reject on a document or image.
    if (e.method === "POST" && /\/items\/[^/]+\/feedback/.test(e.url) && e.ok) {
      const req = (e.requestBody ?? {}) as Record<string, unknown>;
      const m = e.url.match(/\/items\/([^/?]+)\/feedback/);
      opts.onItemDecision?.({
        recordId,
        itemId: m ? decodeURIComponent(m[1]) : null,
        action: (req.decision as string) ?? (req.action as string) ?? null,
        reason: (req.reason_code as string) ?? (req.reason as string) ?? null,
        raw: { request: req, response: body },
      });
      return;
    }

    // The decision itself.
    if (e.method === "POST" && /\/approve\//.test(e.url) && e.ok) {
      const req = (e.requestBody ?? {}) as Record<string, unknown>;
      const m = e.url.match(/\/approve\/([^/?]+)/);
      const cid = m ? decodeURIComponent(m[1]) : null;
      // Only report decisions on runs THIS mount started. Without this, a
      // second card on the page fires its host's callback for someone else's
      // approval — and the host would refresh the wrong screen.
      if (cid && ownCorrelationIds.size && !ownCorrelationIds.has(cid)) return;
      opts.onDecision?.({
        caseId: (body.case_key as string) ?? cid,
        recordId,
        action: (req.decision as string) ?? "approve",
        reason: (req.reason_code as string) ?? (req.decision_reason as string) ?? null,
        reasonText: (req.note as string) ?? null,
        correlationId: cid,
        appliedWrites: (body.applied_writes as unknown[]) ?? undefined,
        raw: { request: req, response: body },
      });
    }
  });

  function renderSpec() {
    if (destroyed || !spec) return;
    const pages = listPages(spec.app_spec);
    const page: Page | undefined = spec.page_id
      ? pages.find((p) => p.id === spec!.page_id)
      : pages.find((p) => (p as { kind?: string }).kind === "embed") ?? pages[0];
    if (!page) {
      renderFailure("This app has no page to display.");
      reportError(
        new Error(
          `[citra-embed] embed "${opts.embed}" resolved an app with no page to render.`,
        ),
      );
      return;
    }

    applyTheme(host, (spec.app_spec as { theme?: unknown }).theme, opts.theme);

    // `id` is the param the detail panel reads
    // (PanelRenderer.tsx:3276 — pageParams?.id ?? pageParams?.record_id).
    // Seeded into BOTH the page params and the live store so a filter push
    // elsewhere on the card cannot drop it.
    const params: Record<string, string> = { id: recordId, record_id: recordId };
    const store = createParamStore(params);

    root?.unmount();
    root = createRoot(host);
    root.render(
      <ParamStoreProvider store={store}>
        <PageBody
          appSpec={spec.app_spec}
          agentSpec={spec.agent_spec}
          page={page}
          pageParams={params}
          slug={spec.slug}
        />
      </ParamStoreProvider>,
    );
  }

  async function loadAndRender() {
    try {
      // Pin the environment for the WHOLE session, not just this call. Only
      // this request names the key in its path; every panel fetch afterwards is
      // slug-addressed, and slug resolution upstream is prod-first — so a
      // promoted app would drag a UAT card onto production data without it.
      setEmbedKey(opts.embed);
      const res = await runtimeFetch(
        `/api/embed/${encodeURIComponent(opts.embed)}/spec`,
      );
      if (!res.ok) {
        throw new Error(
          `[citra-embed] could not load embed "${opts.embed}" (HTTP ${res.status}). ` +
            (res.status === 404
              ? "Check the embed key — it comes from Export on the app card."
              : res.status === 401 || res.status === 403
                ? "The officer's token was rejected; check getToken()."
                : "See the Citra runtime logs."),
        );
      }
      spec = (await res.json()) as EmbedSpecResponse;
      renderSpec();
    } catch (err) {
      const e = err instanceof Error ? err : new Error(String(err));
      // Both: the host gets the callback, and the officer gets something
      // legible instead of an empty box.
      renderFailure(
        e.message.replace(/^\[citra-embed\]\s*/, "") ||
          "Please try again, or contact support if it persists.",
      );
      reportError(e);
    }
  }

  void loadAndRender();

  const mount: CitraMount = {
    update(next) {
      if (destroyed || !next?.recordId || next.recordId === recordId) return;
      recordId = next.recordId;
      ownCorrelationIds.clear();
      renderSpec();
    },
    refresh() {
      if (destroyed) return;
      ownCorrelationIds.clear();
      void loadAndRender();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      unsubscribe();
      root?.unmount();
      root = null;
      setEmbedPortalRoot(null);
      shadow.replaceChildren();
      onDestroyed();
    },
  };
  return mount;
}

// ── <citra-decision> custom element ─────────────────────────────────────────

/**
 * Declarative alternative for hosts whose templates are easier to extend than
 * their scripts (server-rendered pages, CMS blocks). It needs an instance to
 * exist, so `Citra.init()` is still called once by the page.
 *
 *   <citra-decision embed="emb_live_…" record-id="LN-4471"></citra-decision>
 */
let sharedInstance: CitraInstance | null = null;

function defineElement() {
  if (typeof window === "undefined" || !("customElements" in window)) return;
  if (customElements.get("citra-decision")) return;

  class CitraDecisionElement extends HTMLElement {
    private _mount: CitraMount | null = null;

    static get observedAttributes() {
      return ["embed", "record-id"];
    }

    connectedCallback() {
      if (!sharedInstance) {
        console.error(
          "[citra-embed] <citra-decision> used before Citra.init(). Call " +
            "Citra.init({ getToken }) once on the page first.",
        );
        return;
      }
      const embed = this.getAttribute("embed") ?? "";
      const recordId = this.getAttribute("record-id") ?? "";
      try {
        this._mount = sharedInstance.mount(this, { embed, recordId });
      } catch (err) {
        console.error("[citra-embed]", err);
      }
    }

    attributeChangedCallback(name: string, _old: string | null, val: string | null) {
      if (name === "record-id" && this._mount && val) {
        this._mount.update({ recordId: val });
      }
    }

    disconnectedCallback() {
      this._mount?.destroy();
      this._mount = null;
    }
  }

  customElements.define("citra-decision", CitraDecisionElement);
}

// ── The global ──────────────────────────────────────────────────────────────

const Citra = {
  version: __EMBED_VERSION__,
  init(options: InitOptions = {}): CitraInstance {
    const instance = init(options);
    sharedInstance = sharedInstance ?? instance;
    defineElement();
    return instance;
  },
};

export default Citra;
export { init };
