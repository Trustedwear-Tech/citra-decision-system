// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Decision API client.
 *
 * A thin, dependency-free wrapper over the smart-app-service Decision API — the
 * headless "brain" of a Citra Decision App (the same API the web runtime uses).
 * Works anywhere `fetch` exists: browsers, React Native (iOS/Android), Node 18+,
 * Electron/desktop. No UI assumptions — you render your own screens and drive
 * them off these calls.
 *
 * The governance boundary: NEVER write the system of record directly. Commit
 * only through `recommend()` → `approve()` (AI-recommended) or `decideDirect()`
 * (a no-AI human decision). Both run the governed path: policy gate → schema-
 * validated idempotent SoR write → audit → DecisionRecord → self-learning loop.
 */
import {
  AppDetailResponse,
  AppListResponse,
  ApproveRequest,
  AuditRunListResponse,
  ChatResponse,
  DecisionApiError,
  DecisionContract,
  DetailDataResponse,
  FeedbackRequest,
  FraudCalibrationReport,
  PanelDataResponse,
  RunRequest,
  RunResponse,
  RuntimeTokenResponse,
  SelfLearningResponse,
  ToolInvokeResponse,
} from "./types";

export type TokenProvider = string | (() => string | Promise<string>);

export interface DecisionAppClientOptions {
  /** Base URL of smart-app-service, e.g. "https://apps.citra-ai.com/api" or your gateway. */
  baseUrl: string;
  /**
   * The end-user's JWT (or a function returning it — use a function to refresh
   * on expiry). Sent as `Authorization: Bearer <jwt>` so per-user authz + audit
   * apply to every decision.
   */
  token: TokenProvider;
  /** Inject a fetch impl for environments without a global (old Node, tests). */
  fetch?: typeof fetch;
  /** Per-request timeout (ms). Default 60000. */
  timeoutMs?: number;
  /** Extra headers to merge into every request. */
  headers?: Record<string, string>;
}

export class DecisionAppClient {
  private readonly baseUrl: string;
  private readonly token: TokenProvider;
  private readonly _fetch: typeof fetch;
  private readonly timeoutMs: number;
  private readonly extraHeaders: Record<string, string>;

  constructor(opts: DecisionAppClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.token = opts.token;
    const f = opts.fetch ?? (typeof fetch !== "undefined" ? fetch : undefined);
    if (!f) throw new Error("No fetch available — pass opts.fetch");
    this._fetch = f.bind(globalThis);
    this.timeoutMs = opts.timeoutMs ?? 60_000;
    this.extraHeaders = opts.headers ?? {};
  }

  private async authHeader(): Promise<string> {
    const t = typeof this.token === "function" ? await this.token() : this.token;
    return `Bearer ${t}`;
  }

  private async request<T>(
    method: string,
    path: string,
    opts: { query?: Record<string, unknown>; body?: unknown } = {},
  ): Promise<T> {
    let url = this.baseUrl + path;
    if (opts.query) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query)) {
        if (v !== undefined && v !== null) qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += (url.includes("?") ? "&" : "?") + s;
    }
    const headers: Record<string, string> = {
      Authorization: await this.authHeader(),
      ...this.extraHeaders,
    };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    let resp: Response;
    try {
      resp = await this._fetch(url, {
        method,
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    if (!resp.ok) {
      let detail: unknown;
      try {
        detail = (await resp.json())?.detail;
      } catch {
        detail = await resp.text().catch(() => resp.statusText);
      }
      throw new DecisionApiError(resp.status, detail ?? resp.statusText, path);
    }
    if (resp.status === 204) return undefined as T;
    return (await resp.json()) as T;
  }

  // ── Discovery ──────────────────────────────────────────────────────────────
  /** List apps the current user can access. */
  listApps(): Promise<AppListResponse> {
    return this.request("GET", "/apps");
  }
  /** Full app + agent spec. */
  getApp(slug: string): Promise<AppDetailResponse> {
    return this.request("GET", `/apps/${enc(slug)}`);
  }
  /**
   * The self-describing headless contract: actions, request schema, endpoints,
   * governance rules. Read this once and drive the whole integration from it.
   */
  getContract(slug: string): Promise<DecisionContract> {
    return this.request("GET", `/apps/${enc(slug)}/decision-contract`);
  }

  // ── The governed decision loop ──────────────────────────────────────────────
  /**
   * Ask the agent to REASON and RECOMMEND. Nothing is written yet — the result
   * is `status:"pending_approval"` with `planned_writes`/`decision`/`reasoning`
   * (or `"completed"` for a read-only app). Show it to the officer, then
   * `approve()`.
   */
  recommend(slug: string, req: RunRequest): Promise<RunResponse> {
    // NB: no `surface` field — the server's RunRequest never had one (the
    // real field is `mode`, default "queue_action"); sending it was dead weight.
    return this.request("POST", `/apps/${enc(slug)}/run`, {
      body: { inputs: {}, ...req },
    });
  }
  /**
   * Commit a recommendation: approve / reject / cancel. Pass `overrides` to
   * change editable fields before the write (governed override) — aligned to
   * planned_writes[i].
   */
  approve(
    slug: string,
    correlationId: string,
    req: ApproveRequest,
  ): Promise<RunResponse & Record<string, unknown>> {
    return this.request(
      "POST",
      `/apps/${enc(slug)}/run/${enc(correlationId)}/approve`,
      { body: req },
    );
  }
  /**
   * Make a decision WITHOUT the AI (a human_direct write). Body is
   * `{ arguments: {...} }`; omit `panelId` for headless apps. Runs the same
   * governed write path.
   */
  decideDirect(
    slug: string,
    toolName: string,
    args: Record<string, unknown>,
    panelId?: string,
  ): Promise<ToolInvokeResponse> {
    return this.request("POST", `/apps/${enc(slug)}/tool/${enc(toolName)}`, {
      body: { arguments: args, ...(panelId ? { panel_id: panelId } : {}) },
    });
  }
  /** Read-only conversational copilot over the app's data. */
  chat(slug: string, message: string, extra: Record<string, unknown> = {}): Promise<ChatResponse> {
    return this.request("POST", `/apps/${enc(slug)}/chat`, {
      body: { message, ...extra },
    });
  }

  // ── Data (render your own native/desktop UI) ────────────────────────────────
  /** Rows for a queue/table/chart panel. `params` become query predicates. */
  getPanelData(
    slug: string,
    panelId: string,
    params: Record<string, unknown> = {},
  ): Promise<PanelDataResponse> {
    return this.request("GET", `/apps/${enc(slug)}/data/${enc(panelId)}`, { query: params });
  }
  /** Everything a detail screen needs for one record (matched by `id`). */
  getDetail(slug: string, panelId: string, id: string): Promise<DetailDataResponse> {
    return this.request("GET", `/apps/${enc(slug)}/detail/${enc(panelId)}`, { query: { id } });
  }
  /** Resolve choices for a dropdown/typeahead field. */
  getFieldOptions(slug: string, body: Record<string, unknown>): Promise<unknown> {
    return this.request("POST", `/apps/${enc(slug)}/field-options`, { body });
  }
  /** Pending approvals / SLA notifications for a panel. */
  getNotifications(slug: string, panelId: string): Promise<unknown> {
    return this.request("GET", `/apps/${enc(slug)}/notifications/${enc(panelId)}`);
  }

  /**
   * Build the URL for a record's media (photo/PDF) — streamed through the MCP,
   * never a storage URL. Use it as an `<img src>` (attach the token via a header
   * or use `fetchMedia`), or fetch it yourself.
   */
  mediaUrl(
    slug: string,
    dataSourceId: string,
    args: { keyField: string; key: string; col: string },
  ): string {
    const qs = new URLSearchParams({ key_field: args.keyField, key: args.key, col: args.col });
    return `${this.baseUrl}/apps/${enc(slug)}/media/${enc(dataSourceId)}?${qs.toString()}`;
  }
  /** Fetch a record's media bytes (authenticated) as a Blob. */
  async fetchMedia(
    slug: string,
    dataSourceId: string,
    args: { keyField: string; key: string; col: string },
  ): Promise<Blob> {
    const resp = await this._fetch(this.mediaUrl(slug, dataSourceId, args), {
      headers: { Authorization: await this.authHeader() },
    });
    if (!resp.ok) throw new DecisionApiError(resp.status, await resp.text().catch(() => ""), "media");
    return await resp.blob();
  }

  // ── Governance / audit / learning ───────────────────────────────────────────
  listRuns(slug: string, params: { limit?: number; offset?: number } = {}): Promise<AuditRunListResponse> {
    return this.request("GET", `/apps/${enc(slug)}/runs`, { query: params });
  }
  getAudit(slug: string, correlationId: string): Promise<unknown> {
    return this.request("GET", `/apps/${enc(slug)}/runs/${enc(correlationId)}/audit`);
  }
  getLoopMetrics(slug: string): Promise<unknown> {
    return this.request("GET", `/apps/${enc(slug)}/loop-metrics`);
  }
  getSelfLearning(slug: string): Promise<SelfLearningResponse> {
    return this.request("GET", `/apps/${enc(slug)}/self-learning`);
  }
  setSelfLearning(slug: string, body: Record<string, unknown>): Promise<SelfLearningResponse> {
    return this.request("POST", `/apps/${enc(slug)}/self-learning`, { body });
  }
  /**
   * Disposition one item review card (an `ItemFinding` from `RunResponse.item_findings`)
   * — feeds the self-learning loop. `decision`: accept | reject | cancel.
   * For a fraud `case` item this records the officer's confirm/dismiss; it does
   * NOT reject the overall decision (do that via `approve(...,"reject")`).
   * Returns 409 `fraud_not_enabled` if you disposition a `case` item on an app
   * whose ontology has fraud detection turned off.
   */
  submitFeedback(slug: string, itemId: string, body: FeedbackRequest): Promise<unknown> {
    return this.request("POST", `/apps/${enc(slug)}/items/${enc(itemId)}/feedback`, { body });
  }
  /**
   * Fraud calibration report — read-only. Replays past decisions to show how
   * often each fraud signal coincided with an officer reject. Changes nothing
   * automatically. Returns 409 `fraud_not_enabled` if the app's ontology does
   * not enable fraud detection.
   */
  calibrateFraud(slug: string, body: Record<string, unknown> = {}): Promise<FraudCalibrationReport> {
    return this.request("POST", `/apps/${enc(slug)}/fraud-calibration`, { body });
  }

  // ── Auth ────────────────────────────────────────────────────────────────────
  /** Mint a short-lived scoped runtime token for launching the app. */
  mintRuntimeToken(slug: string, body: Record<string, unknown> = {}): Promise<RuntimeTokenResponse> {
    return this.request("POST", `/apps/${enc(slug)}/runtime/token`, { body });
  }
}

function enc(s: string): string {
  return encodeURIComponent(s);
}
