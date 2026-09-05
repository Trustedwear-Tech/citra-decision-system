// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import Markdown from "./Markdown";
import Icon from "./Icon";
import { HeroPanelView, StatStripPanelView, TimelinePanelView } from "./DesignPanels";
import { KpiSparkline, KpiProgress } from "./KpiSparkline";
import { usePanelData } from "@/lib/usePanelData";
import { kpiFromServer, computeMetric, autoMetricIcon, type KpiResult } from "@/lib/kpi";
import { badgeColorFor, formatCellText, progressFraction } from "@/lib/format";
import { ItemFindingReview, REASON_MAX, MIN_REASON_WORDS, SOFT_MAX_REASON_WORDS, reasonWordCount,
         type ItemFinding } from "./ItemFindingReview";
import ScorecardView from "./ScorecardView";
import type { MapPoint } from "./LeafletMap";
import { listPages, buildNavigateHref, substituteParams } from "@/lib/pages";
import { runtimeFetch } from "@/lib/runtimeFetch";
import { readAgentStream } from "@/lib/agentStream";
import type { NavigateTarget, QueueAction, FormOnSubmit, ToolButton,
              FactorScorecard } from "@/types/spec";
import ReactECharts from "echarts-for-react";
import { chartToEchartsOption } from "@/lib/chartToEcharts";
import {
  EXEC_PALETTE,
  EXEC_THEME_NAME,
  fmtNum,
  fmtINR,
  fmtMoney,
  fmtDelta,
  getAppLocale,
  type Delta
} from "@/lib/executiveTheme";
import type {
  AgentSpec,
  AppSpec,
  ChartPanel,
  Panel,
  PanelData,
  FormPanel,
  QueuePanel,
  DetailAction,
  DetailPanel,
  DetailData,
  DetailSectionData,
  DetailRun,
  DetailPendingRun,
  RecordComment,
  DashboardPanel,
  PanelMetricValue,
  AgentChatPanel,
  DocumentViewPanel,
  MarkdownPanel,
  NoticePanel,
  CalendarPanel,
  MapPanel,
  FilterBarPanel,
  FilterControl,
  NotificationsPanel,
  NotificationItem,
  PageKind,
  PlannedWrite,
  FieldSpec,
  OptionItem
} from "@/types/spec";

interface Props {
  panel: Panel;
  app: AppSpec;
  agent: AgentSpec;
  slug: string;
  /** URL query params for the current page. Available to panel data + navigate templates. */
  pageParams?: Record<string, string>;
  /** Purpose of the page this panel lives on. 'dashboard' suppresses the inline agent_chat (the hero brief covers it). */
  pageKind?: PageKind;
}

// usePanelData now lives in @/lib/usePanelData (shared with DesignPanels).

// ---------------------------------------------------------------------------
// doNavigate — shared client navigation helper. Used by form on_submit and
// queue action handlers. Substitutes navigate.params templates against the
// available scopes (row / result / form / params) and routes via Next.js.
// ---------------------------------------------------------------------------

type NavCtx = {
  row?: Record<string, unknown>;
  result?: Record<string, unknown>;
  form?: Record<string, unknown>;
  params?: Record<string, string>;
};

function doNavigate(
  router: ReturnType<typeof useRouter>,
  app: AppSpec,
  slug: string,
  target: NavigateTarget,
  ctx: NavCtx,
) {
  const pages = listPages(app);
  const page = pages.find((p) => p.id === target.page);
  if (!page) {
    console.warn("[citra-app] navigate to unknown page id", target.page);
    return;
  }
  const resolved = substituteParams(target.params, ctx);
  const href = buildNavigateHref(slug, page, resolved);
  router.push(href);
}

export default function PanelRenderer({ panel, app, agent, slug, pageParams, pageKind }: Props) {
  const body = renderPanelBody(panel, app, agent, slug, pageParams ?? {}, pageKind ?? "standard");
  const buttons = panel.tool_buttons ?? [];
  if (buttons.length === 0) return body;
  return (
    <div className="panel-with-buttons">
      {body}
      <PanelToolButtons panel={panel} slug={slug} pageParams={pageParams ?? {}} />
    </div>
  );
}

function renderPanelBody(
  panel: Panel,
  app: AppSpec,
  agent: AgentSpec,
  slug: string,
  pageParams: Record<string, string>,
  pageKind: PageKind,
) {
  switch (panel.type) {
    case "form":
      return <FormPanelView panel={panel} app={app} agent={agent} slug={slug} pageParams={pageParams} />;
    case "queue":
      return <QueuePanelView panel={panel} app={app} slug={slug} pageParams={pageParams} />;
    case "detail":
      return <DetailPanelView panel={panel} app={app} agent={agent} slug={slug} pageParams={pageParams} />;
    case "dashboard":
      return <DashboardPanelView panel={panel} slug={slug} pageParams={pageParams} />;
    case "hero":
      return <HeroPanelView panel={panel} app={app} slug={slug} pageParams={pageParams} />;
    case "stat_strip":
      return <StatStripPanelView panel={panel} slug={slug} pageParams={pageParams} />;
    case "timeline":
      return <TimelinePanelView panel={panel} slug={slug} pageParams={pageParams} />;
    case "chart":
      return <ChartPanelView panel={panel} slug={slug} pageParams={pageParams} />;
    case "agent_chat":
      // A DASHBOARD PAGE surfaces the copilot via the hero-brief band
      // (AppShell), never as an inline panel. On a standard page the inline
      // chat stays.
      if (pageKind === "dashboard") return null;
      return <AgentChatPanelView panel={panel} agent={agent} slug={slug} />;
    case "document_view":
      return <DocumentViewPanelView panel={panel} slug={slug} />;
    case "markdown":
      return <MarkdownPanelView panel={panel} />;
    case "notice":
      return <NoticePanelView panel={panel} />;
    case "calendar":
      return <CalendarPanelView panel={panel} slug={slug} pageParams={pageParams} />;
    case "map":
      return <MapPanelView panel={panel} slug={slug} pageParams={pageParams} />;
    case "filter_bar":
      return <FilterBarView panel={panel} slug={slug} pageParams={pageParams} />;
    case "notifications":
      return <NotificationsPanelView panel={panel} app={app} slug={slug} />;
    default: {
      // Fail loud (RULE #1): a spec carrying a panel type this runtime build
      // does not know about must SURFACE — never silently render nothing. This
      // only fires at runtime for a server spec newer than this bundle; the
      // `never` binding keeps the union exhaustive at compile time.
      const _exhaustive: never = panel;
      const unknownType = (panel as { type?: string })?.type ?? "(missing)";
      console.error(
        `[PanelRenderer] Unknown panel type "${unknownType}" — runtime cannot render it. ` +
          `Spec panel id="${(panel as { id?: string })?.id ?? "?"}". ` +
          `This runtime build predates the panel type; rebuild/redeploy citra-app-runtime.`,
      );
      void _exhaustive;
      return (
        <div className="panel-error" role="alert">
          <strong>Unsupported panel type “{unknownType}”.</strong>
          <span>
            This app uses a UI component newer than the current runtime. Ask
            your admin to update Citra; nothing was rendered for this panel.
          </span>
        </div>
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Tool buttons — direct invocation of tools_v2 entries (no LLM)
// ---------------------------------------------------------------------------

/** Substitute `{param.x}` references in a tool_button's args from the page
 *  params (e.g. a detail page's record id). Non-string values pass through.
 *  Unresolved refs become "" so a stray template never reaches the source. */
function substituteToolArgs(
  args: Record<string, unknown> | undefined,
  pageParams: Record<string, string>,
): Record<string, unknown> {
  if (!args) return {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(args)) {
    out[k] =
      typeof v === "string"
        ? v.replace(/\{param\.([a-zA-Z0-9_]+)\}/g, (_, f) => pageParams[f] ?? "")
        : v;
  }
  return out;
}

function PanelToolButtons({
  panel,
  slug,
  pageParams,
}: {
  panel: Panel;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const buttons = panel.tool_buttons ?? [];
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ name: string; ok: boolean; body: string } | null>(
    null
  );

  async function invoke(b: ToolButton) {
    if (b.confirm && !window.confirm(b.confirm)) return;
    setBusy(b.tool_name);
    setResult(null);
    try {
      const res = await runtimeFetch(`/api/apps/${slug}/tool/${b.tool_name}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          panel_id: panel.id,
          // Resolve {param.x} from the page params (e.g. the detail record
          // id) before POST; the server merges these over the button's
          // pre-bound args and fires the tool directly (no LLM).
          arguments: substituteToolArgs(b.args, pageParams),
        }),
      });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (!res.ok) {
        setResult({
          name: b.label,
          ok: false,
          body: String((body as Record<string, unknown>).detail ?? body.error ?? `HTTP ${res.status}`),
        });
      } else {
        setResult({ name: b.label, ok: true, body: "Done." });
      }
    } catch (e) {
      setResult({ name: b.label, ok: false, body: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="panel-tool-buttons">
      {buttons.map((b) => (
        <button
          key={b.tool_name}
          type="button"
          className="panel-tool-button"
          disabled={busy !== null}
          onClick={() => invoke(b)}
        >
          {busy === b.tool_name ? (
            <><span className="q-spin" /> {b.label}</>
          ) : (
            b.label
          )}
        </button>
      ))}
      {result && (
        <span className={`cf-msg cf-msg-${result.ok ? "ok" : "err"}`}>
          {result.name}: {result.body}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------

function resolveSchema(
  panel: FormPanel,
  agent: AgentSpec
): Record<string, unknown> | null {
  if (panel.schema_inline) return panel.schema_inline;
  if (panel.schema_ref === "agent.input_schema") {
    return agent.input_schema ?? null;
  }
  return null;
}

type FieldDef = {
  type?: string;
  format?: string;
  description?: string;
  title?: string;
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  items?: { enum?: unknown[] };
  /** File upload: render a file picker. The chosen file is base64-encoded
   *  into the submit payload ({filename, content_type, data}); the bound MCP
   *  write column stores the blob. Only emit when the dataset/tool contract
   *  advertises a file column. */
  accepted_types?: string[];
  multiple?: boolean;
  /** Dynamic dropdown: options resolved server-side from a data source
   *  (live DISTINCT values) or a static list, via /apps/{slug}/field-options. */
  options_source?: {
    kind?: string;
    data_source?: string;
    value_column?: string;
    label_column?: string;
    filter?: Record<string, unknown>;
    limit?: number;
    values?: { value: string; label?: string }[];
    /** Typeahead: render a search-as-you-type combobox (debounced ?q=). */
    search?: boolean;
  };
};

type FormMsg = { kind: "ok" | "err" | "info"; text: string } | null;
type FieldControl =
  | "dynamic_select" | "typeahead" | "select" | "radio" | "multiselect"
  | "checkbox" | "textarea" | "number" | "currency" | "date" | "datetime"
  | "time" | "file" | "readonly" | "text";

/** Pick the HTML control kind for a JSON-schema property. */
function fieldControl(def: FieldDef): FieldControl {
  if (def.format === "readonly") return "readonly";         // static display (still submits its default)
  if (def.format === "file") return "file";                 // upload (blob → MCP write column)
  if (def.options_source?.search) return "typeahead";       // search-as-you-type combobox
  if (def.options_source) return "dynamic_select";          // dropdown from a data source
  if (def.type === "array" && (def.items?.enum?.length ?? 0) > 0) return "multiselect"; // multi-select from items.enum
  if (Array.isArray(def.enum) && def.enum.length) {
    return def.format === "radio" ? "radio" : "select";     // static dropdown or radio group
  }
  if (def.type === "boolean") return "checkbox";            // (format:"toggle" also lands here)
  if (def.format === "textarea" || def.format === "multiline") return "textarea";
  if (def.format === "currency") return "currency";         // money — numeric input, locale-aware affordance
  if (def.type === "number" || def.type === "integer") return "number";
  if (def.format === "date") return "date";
  if (def.format === "date-time" || def.format === "datetime") return "datetime";
  if (def.format === "time") return "time";                 // wall-clock time-of-day picker
  return "text";
}

/** Server-enforced inline-upload ceiling (Citra-Service rejects > 15MB after
 *  decode). Guard CLIENT-side too so a too-large file fails fast with a clear
 *  message instead of being base64'd + shipped 3 hops only to be 400'd. */
const _MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

/** Read a File as a base64 blob descriptor. The MCP write column stores it;
 *  the platform only carries the bytes (proxy transport). Large files should
 *  use a pre-signed-URL MCP action instead of this inline path. */
async function fileToBlobDescriptor(
  file: File,
): Promise<{ filename: string; content_type: string; data: string }> {
  if (file.size > _MAX_UPLOAD_BYTES) {
    throw new Error(
      `"${file.name}" is ${(file.size / (1024 * 1024)).toFixed(1)} MB — the ` +
      `inline upload limit is 15 MB. Use a smaller file.`,
    );
  }
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return {
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    data: btoa(bin),
  };
}

/** A <select> whose options are fetched from /field-options (data-source or
 *  static options_source) — the dynamic combo box for FORM fields. */
function DynamicFormSelect({
  slug, panelId, fieldName, def, required,
}: {
  slug: string;
  panelId: string;
  fieldName: string;
  def: FieldDef;
  required: boolean;
}) {
  const [opts, setOpts] = useState<{ value: string; label?: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [optsError, setOptsError] = useState(false);
  // Controlled value so a preset default survives the async options load — an
  // uncontrolled defaultValue is consumed at mount (before options arrive) and
  // the field would otherwise silently drop its default.
  const [val, setVal] = useState<string>((def.default as string) ?? "");
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await runtimeFetch(`/api/apps/${encodeURIComponent(slug)}/field-options`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field: fieldName, panel_id: panelId }),
        });
        if (!res.ok) {
          // RULE #1: an HTTP failure is NOT "no options" — surface it.
          console.error("[field-options] http", res.status);
          if (!cancelled) setOptsError(true);
          return;
        }
        const b = (await res.json().catch(() => ({}))) as { options?: { value: string; label?: string }[] };
        if (!cancelled) { setOpts(b.options ?? []); setOptsError(false); }
      } catch (err) {
        // RULE #1: don't swallow — surface the failure so the empty dropdown
        // isn't mistaken for "no options exist".
        console.error("[field-options] load failed", err);
        if (!cancelled) setOptsError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [slug, panelId, fieldName]);
  return (
    <>
      <select name={fieldName} required={required} value={val} onChange={(e) => setVal(e.target.value)}>
        <option value="" disabled={required}>{loading ? "Loading…" : "Select…"}</option>
        {opts.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label ?? prettyKey(String(o.value))}
          </option>
        ))}
      </select>
      {optsError && (
        <div className="q-empty"><span className="q-empty-icon">⚠</span>couldn&apos;t load options</div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Filter bar — declarative page-param controls. A control sets a URL query
// param on change; Next.js re-renders the (server) page, so every panel whose
// query references {param.<param>} re-queries. View-only: a filter never
// writes — it only changes which slice of data the page shows.
// ---------------------------------------------------------------------------

/** One filter control. Its choices come from the SAME /field-options endpoint
 *  the form combos use (keyed by panel_id + the control's `param`). On change
 *  it merges the value into the current URL query and pushes — the page
 *  re-renders server-side and dependent panels re-query. */
function FilterControlSelect({
  slug,
  panelId,
  control,
  current,
}: {
  slug: string;
  panelId: string;
  control: FilterControl;
  current: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [opts, setOpts] = useState<{ value: string; label?: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [optsError, setOptsError] = useState(false);
  const isDate = control.control_type === "daterange";

  // Push the new value into the URL query, preserving every other param.
  function setParam(value: string) {
    const sp = new URLSearchParams(searchParams?.toString() ?? "");
    if (value) sp.set(control.param, value);
    else sp.delete(control.param);
    const qs = sp.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  // (Defaults are applied once, atomically, by FilterBarView — not per control,
  // so two defaulted controls can't clobber each other's URL param on mount.)

  // Load the option list for select-style controls (not date pickers).
  useEffect(() => {
    if (isDate) { setLoading(false); return; }
    let cancelled = false;
    (async () => {
      try {
        const res = await runtimeFetch(`/api/apps/${encodeURIComponent(slug)}/field-options`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field: control.param, panel_id: panelId }),
        });
        const b = (await res.json().catch(() => ({}))) as { options?: { value: string; label?: string }[] };
        if (!cancelled) { setOpts(b.options ?? []); setOptsError(false); }
      } catch (err) {
        // RULE #1: surface the failure — an empty filter isn't "no options".
        console.error("[filter_bar] options load failed", err);
        if (!cancelled) setOptsError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [slug, panelId, control.param, isDate]);

  return (
    <label className="filter-control" style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
      <span style={{ fontWeight: 600, color: "var(--citra-muted)" }}>{control.label}</span>
      {isDate ? (
        <input
          type="date"
          value={current}
          onChange={(e) => setParam(e.target.value)}
        />
      ) : (
        <select
          value={current}
          onChange={(e) => setParam(e.target.value)}
          disabled={loading && opts.length === 0}
        >
          <option value="">{loading ? "Loading…" : (control.all_label ?? "All")}</option>
          {opts.map((o) => (
            <option key={String(o.value)} value={String(o.value)}>
              {o.label ?? prettyKey(String(o.value))}
            </option>
          ))}
        </select>
      )}
      {optsError && (
        <span className="q-empty" style={{ fontSize: 11 }}>
          <span className="q-empty-icon">⚠</span>couldn&apos;t load options
        </span>
      )}
    </label>
  );
}

function FilterBarView({
  panel,
  slug,
  pageParams,
}: {
  panel: FilterBarPanel;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Apply every control's default ONCE, in a SINGLE push — so multiple
  // defaulted controls can't clobber each other (each per-control push would
  // read the same pre-default snapshot and drop the others). A param already
  // in the URL (deep-link / back-nav) is honoured, never overwritten.
  const didDefaults = useRef(false);
  useEffect(() => {
    if (didDefaults.current) return;
    didDefaults.current = true;
    const missing = (panel.controls ?? []).filter(
      (c) => c.default && !pageParams[c.param],
    );
    if (!missing.length) return;
    const sp = new URLSearchParams(searchParams?.toString() ?? "");
    for (const c of missing) sp.set(c.param, c.default as string);
    const qs = sp.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="filter-bar"
      style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-end" }}
    >
      {(panel.controls ?? []).map((c) => (
        <FilterControlSelect
          key={c.param}
          slug={slug}
          panelId={panel.id}
          control={c}
          current={pageParams[c.param] ?? ""}
        />
      ))}
    </div>
  );
}

/** Search-as-you-type combobox for a high-cardinality data_source dimension.
 *  Debounces the typed text to /field-options (?q=) and submits the chosen
 *  value via a hidden input so the form payload is unchanged vs a <select>. */
function TypeaheadFormSelect({
  slug, panelId, fieldName, def, required,
}: {
  slug: string;
  panelId: string;
  fieldName: string;
  def: FieldDef;
  required: boolean;
}) {
  const [query, setQuery] = useState("");
  const [opts, setOpts] = useState<{ value: string; label?: string }[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [optsError, setOptsError] = useState(false);
  const [selected, setSelected] = useState<{ value: string; label?: string } | null>(
    def.default != null ? { value: String(def.default) } : null,
  );
  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await runtimeFetch(`/api/apps/${encodeURIComponent(slug)}/field-options`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field: fieldName, panel_id: panelId, q: query }),
        });
        if (!res.ok) {
          // RULE #1: an HTTP failure is NOT "No matches" — surface it.
          console.error("[field-options] http", res.status);
          if (!cancelled) setOptsError(true);
          return;
        }
        const b = (await res.json().catch(() => ({}))) as { options?: { value: string; label?: string }[] };
        if (!cancelled) { setOpts(b.options ?? []); setOptsError(false); }
      } catch (err) {
        // RULE #1: don't swallow — surface the failure so "No matches" isn't
        // shown when the backend is actually down.
        console.error("[field-options] load failed", err);
        if (!cancelled) setOptsError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 220); // debounce
    return () => { cancelled = true; clearTimeout(t); };
  }, [slug, panelId, fieldName, query]);
  const display = selected ? (selected.label ?? prettyKey(selected.value)) : "";
  return (
    <div className="cf-typeahead">
      <input type="hidden" name={fieldName} value={selected?.value ?? ""} />
      <input
        type="text"
        className="cf-typeahead-input"
        role="combobox"
        aria-expanded={open}
        required={required && !selected}
        placeholder={def.title ? `Search ${def.title}…` : "Search…"}
        value={open ? query : display}
        onFocus={() => { setOpen(true); setQuery(""); }}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setSelected(null); }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && (
        <ul className="cf-typeahead-list">
          {loading && <li className="cf-typeahead-empty">Searching…</li>}
          {!loading && optsError && <li className="cf-typeahead-empty">⚠ couldn&apos;t load options</li>}
          {!loading && !optsError && opts.length === 0 && <li className="cf-typeahead-empty">No matches</li>}
          {opts.map((o) => (
            <li
              key={String(o.value)}
              className="cf-typeahead-opt"
              onMouseDown={() => { setSelected(o); setOpen(false); }}
            >
              {o.label ?? prettyKey(String(o.value))}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FormPanelView({
  panel,
  app,
  agent,
  slug,
  pageParams,
}: {
  panel: FormPanel;
  app: AppSpec;
  agent: AgentSpec;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const router = useRouter();
  const schema = resolveSchema(panel, agent);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<FormMsg>(null);
  const [result, setResult] = useState<string | null>(null);

  // Edit mode: prefill the form from an existing record (?id=) so the officer
  // changes current values rather than re-typing them. We override each field's
  // `default` with the record value (forms are uncontrolled / defaultValue), so
  // the record MUST be loaded before first render — gate below until it is.
  const editMode = panel.mode === "edit";
  const editId = editMode
    ? pageParams?.id ?? pageParams?.record_id ?? null
    : null;
  const needsPrefill = !!(editMode && editId && panel.prefill_source);
  const [prefill, setPrefill] = useState<Record<string, unknown> | null>(null);
  const [prefillState, setPrefillState] = useState<"loading" | "ready" | "error">(
    needsPrefill ? "loading" : "ready",
  );
  const [prefillErr, setPrefillErr] = useState<string | null>(null);

  useEffect(() => {
    if (!needsPrefill || !editId || !panel.prefill_source) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await runtimeFetch(
          `/api/apps/${encodeURIComponent(slug)}/record?source=${encodeURIComponent(
            panel.prefill_source!,
          )}&id=${encodeURIComponent(editId)}${
            panel.key_field ? `&key=${encodeURIComponent(panel.key_field)}` : ""
          }`,
        );
        const b = (await res.json().catch(() => ({}))) as {
          record?: Record<string, unknown> | null;
          detail?: string;
        };
        if (!res.ok) throw new Error(b.detail || `HTTP ${res.status}`);
        // A 200 with record:null means the record wasn't found — do NOT render a
        // blank edit form (RULE #1: editing a non-existent record would write an
        // empty/bogus update). Surface it as an error.
        if (b.record == null) throw new Error("record not found");
        if (!cancelled) {
          setPrefill(b.record);
          setPrefillState("ready");
        }
      } catch (e) {
        // RULE #1: never silently prefill from an empty/errored read.
        if (!cancelled) {
          setPrefillErr(e instanceof Error ? e.message : "could not load record");
          setPrefillState("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug, editId, needsPrefill, panel.prefill_source, panel.key_field]);

  // Multi-step (wizard) support. steps[] groups field names; fields not named
  // in any step fall to the last step (nothing is dropped). When stepping we
  // toggle field visibility and suppress native `required` (the submit handler
  // validates required fields and jumps to the first missing one's step).
  const steps = panel.steps ?? [];
  const usingSteps = steps.length > 0;
  const [stepIdx, setStepIdx] = useState(0);
  const stepOfField = useMemo(() => {
    const m = new Map<string, number>();
    steps.forEach((s, i) => s.fields.forEach((f) => { if (!m.has(f)) m.set(f, i); }));
    return m;
  }, [steps]);
  const lastStep = usingSteps ? steps.length - 1 : 0;
  const fieldStep = (name: string): number => stepOfField.get(name) ?? lastStep;

  if (!schema) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">∅</span>No form schema bound.
      </div>
    );
  }
  if (needsPrefill && prefillState === "loading") {
    return (
      <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}>
        <div className="q-skel-card" style={{ height: 120 }} />
      </div>
    );
  }
  if (needsPrefill && prefillState === "error") {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">⚠</span>Couldn&apos;t load the record to
        edit: {prefillErr}
      </div>
    );
  }
  const baseProps = (schema.properties ?? {}) as Record<string, FieldDef>;
  // Edit mode: seed each field's default from the loaded record's value.
  const props: Record<string, FieldDef> = prefill
    ? Object.fromEntries(
        Object.entries(baseProps).map(([k, def]) => {
          const v = prefill[k];
          return v === undefined || v === null
            ? [k, def]
            : [k, { ...def, default: v } as FieldDef];
        }),
      )
    : baseProps;
  const required = (schema.required ?? []) as string[];
  const entries = Object.entries(props);

  return (
    <form
      className="cf"
      onSubmit={async (e) => {
        e.preventDefault();
        setSubmitting(true);
        setMessage(null);
        setResult(null);

        const onSubmit: FormOnSubmit | undefined = panel.on_submit;
        const action = onSubmit?.agent_action;
        const directTool = onSubmit?.tool_name;
        const navigate = onSubmit?.navigate;
        if (!action && !directTool && !navigate) {
          setSubmitting(false);
          setMessage({
            kind: "err",
            text: "Form has no on_submit.agent_action, on_submit.tool_name, or on_submit.navigate configured.",
          });
          return;
        }

        // Collect inputs, coercing types as the schema requested.
        const form = e.currentTarget;
        const formData = new FormData(form);

        // Wizard mode suppresses native `required` (hidden required inputs are
        // not focusable), so validate required fields here and jump to the
        // first missing field's step rather than silently submitting blanks.
        if (usingSteps) {
          for (const name of required) {
            const def = props[name];
            const ctrl = def ? fieldControl(def) : "text";
            if (ctrl === "checkbox") continue; // booleans are never "empty"
            let missing: boolean;
            if (ctrl === "file") {
              // FormData.get() on an empty file input is an empty File, whose
              // String() is "[object File]" (non-empty) — check .files instead.
              const el = form.elements.namedItem(name) as HTMLInputElement | null;
              missing = !el?.files?.length;
            } else {
              const v = formData.get(name);
              missing = v == null || String(v).trim() === "";
            }
            if (missing) {
              setSubmitting(false);
              setStepIdx(fieldStep(name));
              setMessage({ kind: "err", text: `“${(def?.title ?? prettyKey(name))}” is required.` });
              return;
            }
          }
        }

        const inputs: Record<string, unknown> = {};
        for (const [k, def] of entries) {
          const control = fieldControl(def);
          if (control === "checkbox") {
            inputs[k] = (form.elements.namedItem(k) as HTMLInputElement | null)?.checked ?? false;
            continue;
          }
          if (control === "multiselect") {
            // A <select multiple> yields one entry per chosen option.
            const all = formData.getAll(k).map((x) => String(x)).filter((x) => x !== "");
            if (all.length) inputs[k] = all;
            continue;
          }
          if (control === "file") continue; // handled async below
          const v = formData.get(k);
          if (v == null || v === "") continue;
          if ((control === "number" || control === "currency") && typeof v === "string") {
            const n = Number(v);
            inputs[k] = Number.isNaN(n) ? v : n;
          } else {
            inputs[k] = v;
          }
        }

        // File fields → base64 blob descriptor(s). The MCP write column stores
        // the blob; the platform only carries the bytes. (Async: must await
        // the reads before the submit fetch below.)
        for (const [k, def] of entries) {
          if (fieldControl(def) !== "file") continue;
          const el = form.elements.namedItem(k) as HTMLInputElement | null;
          const files = el?.files ? Array.from(el.files) : [];
          if (!files.length) continue;
          const encoded = await Promise.all(files.map(fileToBlobDescriptor));
          inputs[k] = def.multiple ? encoded : encoded[0];
        }

        // edit mode: re-include the record key so on_submit writes an UPDATE to
        // the existing record (overlay merges by it; an MCP write keys by it),
        // not a brand-new row.
        if (editMode && panel.key_field && editId) {
          inputs[panel.key_field] = editId;
        }

        try {
          let outputs: Record<string, unknown> = {};
          let ok = true;
          if (directTool) {
            // Direct (no-LLM) write: POST the form fields straight to the
            // tool endpoint. The source system is updated immediately and the
            // action is audited (surface="smartapp_tool_direct").
            const res = await runtimeFetch(
              `/api/apps/${encodeURIComponent(slug)}/tool/${encodeURIComponent(directTool)}`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ panel_id: panel.id, arguments: inputs }),
              },
            );
            const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
            if (!res.ok) {
              ok = false;
              setMessage({
                kind: "err",
                text: `Failed (${res.status}): ${body.detail ?? body.error ?? "unknown"}`,
              });
            } else {
              outputs = (body.result ?? {}) as Record<string, unknown>;
              setMessage({ kind: "ok", text: "Saved." });
            }
          } else if (action) {
            const res = await runtimeFetch(`/api/run/${encodeURIComponent(slug)}`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ action, inputs }),
            });
            const body = await res.json().catch(() => ({} as Record<string, unknown>));
            outputs = (body.outputs ?? {}) as Record<string, unknown>;
            if (!res.ok) {
              ok = false;
              setMessage({
                kind: "err",
                text: `Run failed (${res.status}): ${body.detail ?? body.error ?? "unknown"}`,
              });
            } else if (body.status === "pending_approval") {
              setMessage({
                kind: "info",
                text: `Submitted — awaiting approval${
                  body.outputs?.approver_roles?.length
                    ? ` (${body.outputs.approver_roles.join(", ")})`
                    : ""
                }.`,
              });
            } else if (body.status === "failed") {
              ok = false;
              setMessage({ kind: "err", text: `Run failed: ${body.error ?? "unknown"}` });
            } else {
              setMessage({
                kind: "ok",
                text: `Submitted${body.correlation_id ? ` · ${body.correlation_id}` : ""}.`,
              });
              setResult(
                (body.outputs?.text as string) ??
                  (body.reasoning as string) ??
                  (body.decision as string) ??
                  null,
              );
            }
          }

          if (navigate && ok) {
            doNavigate(router, app, slug, navigate, {
              result: outputs,
              form: inputs,
              params: pageParams,
            });
          }
        } catch (err) {
          setMessage({
            kind: "err",
            text: `Network error: ${err instanceof Error ? err.message : String(err)}`,
          });
        } finally {
          setSubmitting(false);
        }
      }}
    >
      {entries.length === 0 && (
        <div className="cf-hint">This form has no fields.</div>
      )}
      {usingSteps && (
        <div className="cf-steps" role="list">
          {steps.map((s, i) => (
            <div
              key={i}
              role="listitem"
              className={`cf-step ${i === stepIdx ? "is-active" : i < stepIdx ? "is-done" : ""}`}
            >
              <span className="cf-step-num">{i < stepIdx ? "✓" : i + 1}</span>
              <span className="cf-step-title">{s.title}</span>
            </div>
          ))}
        </div>
      )}
      {usingSteps && steps[stepIdx]?.description && (
        <div className="cf-hint">{steps[stepIdx].description}</div>
      )}
      <div className="cf-grid">
        {entries.map(([name, def]) => {
          const control = fieldControl(def);
          const isRequired = required.includes(name);
          // In wizard mode, native `required` is suppressed (hidden required
          // inputs aren't focusable); the submit handler validates instead.
          const nativeReq = isRequired && !usingSteps;
          const hiddenStep = usingSteps && fieldStep(name) !== stepIdx;
          const label = def.title ?? prettyKey(name);
          if (control === "checkbox") {
            return (
              <label
                key={name}
                className="cf-field cf-field-check"
                style={hiddenStep ? { display: "none" } : undefined}
              >
                <input type="checkbox" name={name} defaultChecked={!!def.default} />
                <span>
                  <span className="cf-label">
                    {label}
                    {isRequired && <span className="cf-req">*</span>}
                  </span>
                  {def.description && <span className="cf-desc">{def.description}</span>}
                </span>
              </label>
            );
          }
          return (
            <label key={name} className="cf-field" style={hiddenStep ? { display: "none" } : undefined}>
              <span className="cf-label">
                {label}
                {isRequired && <span className="cf-req">*</span>}
              </span>
              {control === "readonly" ? (
                <>
                  <input type="hidden" name={name} value={(def.default as string) ?? ""} />
                  <span className="cf-readonly">{String(def.default ?? "—")}</span>
                </>
              ) : control === "typeahead" ? (
                <TypeaheadFormSelect
                  slug={slug}
                  panelId={panel.id}
                  fieldName={name}
                  def={def}
                  required={nativeReq}
                />
              ) : control === "dynamic_select" ? (
                <DynamicFormSelect
                  slug={slug}
                  panelId={panel.id}
                  fieldName={name}
                  def={def}
                  required={nativeReq}
                />
              ) : control === "select" ? (
                <select
                  name={name}
                  required={nativeReq}
                  defaultValue={(def.default as string) ?? ""}
                >
                  <option value="" disabled={isRequired}>
                    Select…
                  </option>
                  {(def.enum ?? []).map((opt) => (
                    <option key={String(opt)} value={String(opt)}>
                      {prettyKey(String(opt))}
                    </option>
                  ))}
                </select>
              ) : control === "multiselect" ? (
                <select name={name} required={nativeReq} multiple size={4}>
                  {((def.items?.enum ?? def.enum) ?? []).map((opt) => (
                    <option key={String(opt)} value={String(opt)}>
                      {prettyKey(String(opt))}
                    </option>
                  ))}
                </select>
              ) : control === "radio" ? (
                <span className="cf-radio-group">
                  {(def.enum ?? []).map((opt) => (
                    <label key={String(opt)} className="cf-radio">
                      <input
                        type="radio"
                        name={name}
                        value={String(opt)}
                        required={nativeReq}
                        defaultChecked={String(def.default ?? "") === String(opt)}
                      />
                      <span>{prettyKey(String(opt))}</span>
                    </label>
                  ))}
                </span>
              ) : control === "file" ? (
                <input
                  name={name}
                  type="file"
                  required={nativeReq}
                  multiple={!!def.multiple}
                  accept={(def.accepted_types ?? []).join(",") || undefined}
                />
              ) : control === "textarea" ? (
                <textarea
                  name={name}
                  required={nativeReq}
                  rows={4}
                  defaultValue={(def.default as string) ?? ""}
                />
              ) : (
                <input
                  name={name}
                  required={nativeReq}
                  defaultValue={(def.default as string) ?? ""}
                  inputMode={control === "currency" ? "decimal" : undefined}
                  type={
                    control === "number" || control === "currency"
                      ? "number"
                      : control === "time"
                      ? "time"
                      : control === "date"
                      ? "date"
                      : control === "datetime"
                      ? "datetime-local"
                      : "text"
                  }
                  min={control === "number" || control === "currency" ? def.minimum : undefined}
                  max={control === "number" || control === "currency" ? def.maximum : undefined}
                  step={
                    control === "currency"
                      ? "0.01"
                      : control === "number" && def.type === "integer"
                      ? 1
                      : "any"
                  }
                />
              )}
              {def.description && <span className="cf-desc">{def.description}</span>}
            </label>
          );
        })}
      </div>
      <div className="cf-actions">
        {usingSteps && stepIdx > 0 && (
          <button
            className="q-btn"
            type="button"
            onClick={() => { setMessage(null); setStepIdx((i) => Math.max(0, i - 1)); }}
          >
            ← Back
          </button>
        )}
        {usingSteps && stepIdx < lastStep ? (
          <button
            className="q-btn q-btn-primary"
            type="button"
            onClick={() => { setMessage(null); setStepIdx((i) => Math.min(lastStep, i + 1)); }}
          >
            Next →
          </button>
        ) : (
          <button className="q-btn q-btn-primary cf-submit" type="submit" disabled={submitting}>
            {submitting ? (
              <><span className="q-spin" /> Submitting…</>
            ) : (
              "Submit"
            )}
          </button>
        )}
        {message && <span className={`cf-msg cf-msg-${message.kind}`}>{message.text}</span>}
      </div>
      {result && (
        <div className="cf-result">
          <div className="cf-result-head">Result</div>
          <Markdown content={result} />
        </div>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

type ToastState = { kind: "ok" | "err" | "info"; msg: string } | null;

/** Outcome of one agent action fired from a queue row. */
type RunResult = {
  rowKey: string;
  rowTitle: string;
  label: string;
  status: string;
  decision?: string;
  reasoning?: string;
  outputs: Record<string, unknown>;
  correlationId?: string;
  error?: string;
  /** Slug — needed by the Apply button to call /api/apps/{slug}/approve/{cid}. */
  slug?: string;
  /** Captured but not-yet-applied writes when the run came back as
   *  status="pending_approval". Each entry is the LLM's proposed write
   *  validated by the MCP's dry-run preflight. The Apply button replays
   *  these against /execute_action with dry_run=false. */
  plannedWrites?: PlannedWrite[];
  /** Content hash of the proposed writes shown to the officer. Echoed back on
   *  Apply as expected_plan_hash so the server verifies display == commit. */
  planHash?: string;
  /** Per-write outcome after Apply — surfaced so the officer can see
   *  which of N proposed changes committed and which failed. Empty
   *  while status is still pending_approval. */
  writeEvents?: Array<{
    tool?: string;
    kind?: string;
    status?: string;
    dataset_id?: string;
    action_id?: string;
    source_id?: string;
    args?: Record<string, unknown>;
    result?: Record<string, unknown>;
    /** Officer override delta for this write: { field: { from, to } }. */
    override?: Record<string, { from?: unknown; to?: unknown }> | null;
  }>;
  /** Structured per-item findings (image_analyze / doc_extract) surfaced for
   *  per-item officer accept/reject. Reject-reasons train the rubric. */
  itemFindings?: ItemFinding[];
  /** Precedent receipts — the past cases the model says informed this
   *  recommendation. relation: "similar" (followed) | "differs" (deviated). */
  citedPrecedents?: Array<{ decision_id?: string; relation?: string; note?: string }>;
  /** The case signature the model actually saw ("loss_type:theft", …), frozen
   *  at decision time. Shown READ-ONLY: it is derived from the record's own
   *  columns, never picked by the officer — two officers tagging the same case
   *  differently is exactly what would stop a correction ever clustering. It is
   *  displayed so the officer can see the SCOPE a correction of theirs would
   *  teach, which is otherwise invisible at the moment they're asked for one. */
  caseFacets?: string[];
  /** Learned judgements that were in front of the model for this run.
   *  `relation`: "cited" (the model said it used it) | "injected" (it was
   *  offered and not cited) | "overrode_by_rule". Shown so the officer can see
   *  what their team already taught — and, when the agent went against it,
   *  that it did. */
  citedClauses?: Array<{ clause_id?: string; text?: string; relation?: string;
                         status?: string; support_count?: number }>;
  /** The computed scorecard, when the app declares a factor_set. Assembled
   *  server-side in code from the declared weights — the officer sees the same
   *  totals the ledger records, and the same ones the Decision API returns. */
  scorecard?: FactorScorecard;
};

/** Map a status / decision string to one of the badge tone names. */
/** Assemble a RunResult from a `/api/run` response body.
 *
 *  Shared by the queue's row actions and the detail panel's record actions.
 *  Both surfaces feed the SAME RunResultModal — the officer's decision surface,
 *  where the reject reason is captured and the correction that becomes a
 *  learned clause is written. If the two assembled their result separately they
 *  could drift in which fields they carry (planned writes, item findings, cited
 *  precedents), and a field missing here is silently missing from the decision.
 */
function runResultFromBody(
  b: Record<string, unknown>,
  meta: { rowKey: string; rowTitle: string; label: string; slug: string },
): RunResult {
  return {
    ...meta,
    status: String(b.status ?? "completed"),
    decision: b.decision as string | undefined,
    reasoning: b.reasoning as string | undefined,
    outputs: (b.outputs ?? {}) as Record<string, unknown>,
    correlationId: b.correlation_id as string | undefined,
    error: b.error as string | undefined,
    planHash: b.plan_hash as string | undefined,
    plannedWrites: Array.isArray(b.planned_writes)
      ? (b.planned_writes as RunResult["plannedWrites"])
      : undefined,
    writeEvents: Array.isArray(b.write_events)
      ? (b.write_events as RunResult["writeEvents"])
      : undefined,
    itemFindings: Array.isArray(b.item_findings)
      ? (b.item_findings as ItemFinding[])
      : undefined,
    citedPrecedents: Array.isArray(b.cited_precedents)
      ? (b.cited_precedents as RunResult["citedPrecedents"])
      : undefined,
    // The runtime carries the frozen signature in `references.case_facets`
    // (see runtime.run_references) — there is no top-level field on
    // RunResponse. Reading only the top level meant the facet strip rendered
    // on the QUEUE path (staged rows DO have case_facets top-level) and
    // silently vanished on the on-demand path, so an officer asked for a
    // correction could not see which scope their lesson would teach — the one
    // thing the strip exists to show.
    caseFacets: Array.isArray(b.case_facets)
      ? (b.case_facets as string[])
      : Array.isArray((b.references as Record<string, unknown> | undefined)?.case_facets)
        ? ((b.references as Record<string, string[]>).case_facets)
        : undefined,
    citedClauses: Array.isArray(b.cited_clauses)
      ? (b.cited_clauses as RunResult["citedClauses"])
      : undefined,
    scorecard: (b.scorecard ?? undefined) as FactorScorecard | undefined,
  };
}

function statusTone(value: unknown): string {
  const s = String(value ?? "").toLowerCase();
  if (/(resolved|approv|complete|compliant|released|paid|active|success|done|delivered|clean|routed)/.test(s))
    return "green";
  if (/(reject|fail|flag|disqualif|overdue|breach|error|non[_ -]?compliant|hold|cancel|leak)/.test(s))
    return "red";
  if (/(new|pending|submitted|queued|await|^open$|unassigned|draft|needs[_ -]?review)/.test(s))
    return "amber";
  if (/(progress|routed|review|evaluation|transit|loading|processing|assigned)/.test(s))
    return "blue";
  if (/(escalat|urgent|critical)/.test(s)) return "purple";
  return "gray";
}

function badgeClass(value: unknown): string {
  return `q-badge q-badge-${statusTone(value)}`;
}

function prettyKey(k: string): string {
  return k.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Normalise an agent decision for display: a short token / snake_case verb
 *  (REASSIGN, escalate, route_to_je) is humanised; a real sentence is left as
 *  written — so the verdict badge never shows a cryptic raw model token. */
function normalizeDecision(s: string): string {
  const t = String(s).trim();
  if (/^[a-z0-9_-]+$/i.test(t) && t.length <= 24) return prettyKey(t);
  return t;
}

// Obviously-internal / PII column names auto-hidden from a queue card when the
// BA hasn't explicitly pinned columns (they're never decision-relevant + risk
// disclosure). Decision-relevant names are floated to the front instead.
const _HIDDEN_COL_RE =
  /(^_|_id$|^id$|tenant_id|org_id|correlation_id|workflow_execution_id|_hash$|password|secret|token|aadhaar|^pan$|pan_no|ssn|salary)/i;
const _PRIORITY_COL_RE =
  /(amount|amt|cost|price|due|sla|deadline|priorit|severit|score|risk|status|state|assign|owner|name|district|region|zone|created|updated|date|time)/i;
const _DANGER_ACTION_RE = /(reject|cancel|hold|delete|deny|discard|decline|remove)/i;
const _AI_SOURCES = new Set(["trigger", "workflow", "staging", "agent", "recommendation"]);

/** An AI-recommended row comes from a recommender source — NOT merely any
 *  non-"queue_action" value (which falsely tagged manual/import rows and
 *  diluted the trust signal). */
function isAiRecommended(source: unknown): boolean {
  return _AI_SOURCES.has(String(source ?? "").toLowerCase());
}

/** Pick a card's at-a-glance fields. Pinned panel.columns are respected as-is;
 *  otherwise drop internal/PII columns and float decision-relevant ones first
 *  so the deciding field lands on the card, not in an unseen tail column. */
function pickCardFields(
  cols: string[], titleCol: string, badgeCol: string | null, pinned: boolean,
): string[] {
  let c = cols.filter((x) => x !== titleCol && x !== badgeCol && x !== "source");
  if (!pinned) {
    c = c.filter((x) => !_HIDDEN_COL_RE.test(x));
    c = [...c].sort(
      (a, b) => (_PRIORITY_COL_RE.test(a) ? 0 : 1) - (_PRIORITY_COL_RE.test(b) ? 0 : 1),
    );
  }
  return c.slice(0, 5);
}

function QueuePanelView({
  panel,
  app,
  slug,
  pageParams,
}: {
  panel: QueuePanel;
  app: AppSpec;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const router = useRouter();
  const ds = (app.data_sources ?? []).find((d) => d.id === panel.data_source);
  const [reload, setReload] = useState(0);
  const { loading, error, data } = usePanelData(slug, panel.id, true, reload, pageParams);

  const allRows = useMemo(() => data?.rows ?? [], [data]);
  const cols = useMemo(
    () => (panel.columns?.length ? panel.columns : data?.columns ?? []),
    [panel.columns, data],
  );

  const pageSize = panel.page_size ?? 12;
  const [view, setView] = useState<"cards" | "table" | "kanban" | "split">(
    panel.view ?? "cards",
  );
  const kanbanCol = panel.group_by;
  // Split (master-detail) view: the selected row key. Defaults to the first
  // row of the current page once data lands.
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sortCol, setSortCol] = useState<string | null>(panel.default_sort?.column ?? null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(panel.default_sort?.dir ?? "asc");
  const [page, setPage] = useState(1);
  const [toast, setToast] = useState<ToastState>(null);
  // F1: multiple rows can run IN PARALLEL — track the busy SET, not one slot,
  // so clicking a second Review/Triage never cancels the first's spinner.
  const [busyRows, setBusyRows] = useState<Set<string>>(new Set());
  const startBusy = (k: string) => setBusyRows((s) => new Set(s).add(k));
  const endBusy = (k: string) =>
    setBusyRows((s) => { const n = new Set(s); n.delete(k); return n; });
  // Per-row assessment outcome — drives the inline chip + the modal. F2:
  // persisted to sessionStorage so a refresh / navigation doesn't lose the last
  // AI assessment until the officer re-runs that row.
  const _resultsKey = `citra:results:${slug}:${panel.id}`;
  const [results, setResults] = useState<Record<string, RunResult>>({});
  const updateResults = (
    fn: (m: Record<string, RunResult>) => Record<string, RunResult>,
  ) =>
    setResults((m) => {
      const next = fn(m);
      if (typeof window !== "undefined") {
        try { sessionStorage.setItem(_resultsKey, JSON.stringify(next)); } catch { /* quota */ }
      }
      return next;
    });
  // Hydrate persisted results once on mount (client only → no SSR mismatch).
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = sessionStorage.getItem(_resultsKey);
      if (raw) {
        const loaded = JSON.parse(raw);
        if (loaded && typeof loaded === "object") setResults(loaded);
      }
    } catch { /* ignore corrupt cache */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [_resultsKey]);
  // The row the officer most recently launched — only THAT row's completion
  // auto-opens the modal, so a parallel background finish never steals focus.
  const lastLaunchedRef = useRef<string | null>(null);
  const [modal, setModal] = useState<RunResult | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4500);
    return () => clearTimeout(t);
  }, [toast]);

  // Faceted filters: explicit panel.filters, else auto-detect low-cardinality columns.
  const facets = useMemo(() => {
    const out: { col: string; values: string[] }[] = [];
    const candidates = panel.filters?.length ? panel.filters : cols;
    for (const c of candidates) {
      const set = new Set<string>();
      for (const r of allRows) {
        const v = r[c];
        if (v != null && v !== "") set.add(String(v));
        if (set.size > 9) break;
      }
      if (set.size > 1 && set.size <= 8) out.push({ col: c, values: [...set].sort() });
    }
    return out;
  }, [allRows, cols, panel.filters]);

  const badgeCol = useMemo(
    () =>
      panel.badge_column ??
      cols.find((c) =>
        /(^|_)(status|state|priority|disposition|compliance|verdict|decision)/i.test(c),
      ) ??
      null,
    [cols, panel.badge_column],
  );

  const titleCol = useMemo(
    () =>
      panel.title_column ??
      cols.find((c) => /(name|title|subject)/i.test(c)) ??
      cols.find((c) => /(^id$|_id$)/i.test(c)) ??
      cols[0] ??
      "",
    [cols, panel.title_column],
  );

  const searchCols = panel.searchable_columns?.length ? panel.searchable_columns : cols;

  // C5/C7 — declared badge colors + per-column display formats. The declared
  // semantic color for a value wins over the statusTone auto-detection.
  const qBadge = (value: unknown): string => {
    const declared = badgeColorFor(value, panel.badge_colors);
    if (!declared) return badgeClass(value);
    return `q-badge q-badge-${declared === "slate" ? "gray" : declared}`;
  };
  const renderFormatted = (
    value: unknown,
    col: string,
    row?: Record<string, unknown>,
  ): ReactNode => {
    const f = (panel.column_formats ?? {})[col];
    if (f === "status_pill") {
      return <span className={qBadge(value)}>{formatCell(value, col)}</span>;
    }
    if (f === "grade") {
      // A gated case has NO grade — the composite is suppressed server-side
      // because a failed policy limit decides the case. Rendering it blank
      // would read as "not yet scored" and sort alongside pending work, so it
      // is labelled instead.
      //
      // But an empty grade is NOT proof of a gate. A checklist app has no
      // grade at all by design, and a composite where nothing scored has none
      // either — labelling those "gated" tells the officer a policy limit was
      // breached when none was. The card carries `gated` as its own field;
      // trust that, and fall back to an em dash for a genuinely absent grade.
      const missing = value === null || value === undefined || value === "";
      const gated = row ? Boolean(row.gated) : missing;
      if (gated) return <span className="q-grade q-grade-gated">gated</span>;
      return <span className="q-grade">{missing ? "—" : String(value)}</span>;
    }
    if (f === "progress") {
      const frac = progressFraction(value);
      if (frac !== null) {
        const pct = Math.round(frac * 100);
        return (
          <span className="q-progress" title={`${pct}%`}>
            <span className="q-progress-track">
              <span className="q-progress-fill" style={{ width: `${pct}%` }} />
            </span>
            {pct}%
          </span>
        );
      }
    }
    const t = formatCellText(value, f, getAppLocale().locale);
    if (t !== null) return t;
    return formatCellNode(value, col);
  };

  const filtered = useMemo(() => {
    let rows = allRows;
    const q = search.trim().toLowerCase();
    if (q) {
      rows = rows.filter((r) =>
        searchCols.some((c) => String(r[c] ?? "").toLowerCase().includes(q)),
      );
    }
    for (const [col, val] of Object.entries(filters)) {
      if (val) rows = rows.filter((r) => String(r[col] ?? "") === val);
    }
    if (sortCol) {
      const s = sortCol;
      rows = [...rows].sort((a, b) => {
        const av = a[s];
        const bv = b[s];
        const an = Number(av);
        const bn = Number(bv);
        let cmp: number;
        if (Number.isFinite(an) && Number.isFinite(bn) && av !== "" && bv !== "") cmp = an - bn;
        else cmp = String(av ?? "").localeCompare(String(bv ?? ""));
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return rows;
  }, [allRows, searchCols, search, filters, sortCol, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const curPage = Math.min(page, totalPages);
  const paged = filtered.slice((curPage - 1) * pageSize, curPage * pageSize);

  useEffect(() => {
    setPage(1);
  }, [search, filters, sortCol, sortDir]);

  const rowClickAction = (panel.actions ?? []).find((a) => a.is_row_click);
  const buttonActions = (panel.actions ?? []).filter((a) => !a.is_row_click);

  function rowKey(row: Record<string, unknown>, _idx: number): string {
    // Stable identity that does NOT depend on the page/column-local index —
    // an index-based key collides across kanban columns and pages, which made
    // the action spinner / result chip render on the WRONG card. Prefer a real
    // id column; fall back to a content hash (still position-independent).
    const idVal =
      row["id"] ?? row["_id"] ?? row["record_id"] ?? row["case_natural_key"] ??
      row["workflow_execution_id"] ?? row[titleCol];
    if (idVal != null && String(idVal) !== "") return `r:${String(idVal)}`;
    let h = 0;
    const s = JSON.stringify(row);
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return `h:${h}`;
  }

  async function fireAction(
    action: QueueAction,
    row: Record<string, unknown>,
    key: string,
  ) {
    if (action.agent_action) {
      startBusy(key);
      lastLaunchedRef.current = key;
      // Re-running clears the prior assessment for THIS row (kept until re-run).
      updateResults((m) => { const n = { ...m }; delete n[key]; return n; });
      setToast({ kind: "info", msg: `Running "${action.label}"…` });
      const rowTitle = formatCell(row[titleCol] ?? row["id"] ?? key);
      try {
        // The action's input_schema may need keys the queue has no column
        // for (a fixed record_type, …) — action.args supplies those and
        // wins over row keys of the same name.
        const inputs = { ...row, ...(action.args ?? {}) };
        // Tag the run as a queue_action so the backend runs plan-then-apply
        // (LLM proposes writes in dry mode; we show the plan; officer clicks
        // Apply to commit). runtimeFetch carries the officer's JWT.
        const res = await runtimeFetch(`/api/run/${encodeURIComponent(slug)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: action.agent_action,
            inputs,
            mode: "queue_action",
          }),
        });
        // Keepalive-streamed turn (no 504 on a long action loop); the terminal
        // `done` event carries the same RunResponse shape. A pre-flight 4xx or
        // mid-turn failure throws → outer catch sets the error toast.
        const b = await readAgentStream<Record<string, unknown>>(res);
        const result = runResultFromBody(b, {
          rowKey: key, rowTitle, label: action.label, slug,
        });
        updateResults((m) => ({ ...m, [key]: result }));
        // Only auto-open the modal for the row the officer most recently
        // launched — a parallel background completion just lights its chip.
        if (lastLaunchedRef.current === key) setModal(result);
        if (b.status === "failed") {
          setToast({ kind: "err", msg: `"${action.label}" failed: ${b.error ?? "unknown"}` });
        } else if (b.status === "pending_approval") {
          setToast({ kind: "info", msg: `"${action.label}" — review the plan and Apply to commit.` });
        } else {
          setToast({ kind: "ok", msg: `"${action.label}" completed — see result.` });
        }
        // Re-pull the queue so any write-back the action made shows up.
        setReload((n) => n + 1);
      } catch (e) {
        setToast({ kind: "err", msg: `"${action.label}": ${(e as Error).message}` });
      } finally {
        endBusy(key);
      }
    }
    if (action.navigate) {
      doNavigate(router, app, slug, action.navigate, { row, params: pageParams });
    }
  }

  // F3: a staged/auto-recommended row already carries its recommendation
  // (`_recommendation`), so a card click shows it INSTANTLY — build the modal
  // from the row's own data, no agent re-run. Apply still goes through the
  // governed /approve path via the composite correlation id.
  function stagedRecommendation(row: Record<string, unknown>): Record<string, unknown> | null {
    const rec = row["_recommendation"];
    return rec && typeof rec === "object" ? (rec as Record<string, unknown>) : null;
  }
  function openStaged(row: Record<string, unknown>, key: string) {
    const rec = stagedRecommendation(row);
    if (!rec) return;
    // Only a not-yet-decided recommendation is "pending_approval" (shows
    // Apply/Reject); an already-terminal one (applied/rejected/cancelled/…)
    // keeps its status so the modal doesn't re-offer a redundant action.
    const _st = String(rec.status ?? "").toLowerCase();
    const _terminal = ["applied", "completed", "rejected", "cancelled", "canceled", "failed", "expired"];
    const result: RunResult = {
      rowKey: key,
      rowTitle: formatCell(row[titleCol] ?? row["id"] ?? key),
      label: "Recommendation",
      status: _terminal.includes(_st) ? (_st === "applied" ? "completed" : _st) : "pending_approval",
      decision: rec.decision as string | undefined,
      reasoning: rec.reasoning as string | undefined,
      outputs: (rec.evidence ? { text: rec.evidence } : {}) as Record<string, unknown>,
      correlationId: rec.correlation_id as string | undefined,
      slug,
      planHash: rec.plan_hash as string | undefined,
      plannedWrites: Array.isArray(rec.planned_writes)
        ? (rec.planned_writes as RunResult["plannedWrites"])
        : undefined,
      citedPrecedents: Array.isArray(rec.cited_precedents)
        ? (rec.cited_precedents as RunResult["citedPrecedents"])
        : undefined,
      caseFacets: Array.isArray(rec.case_facets)
        ? (rec.case_facets as string[])
        : undefined,
      citedClauses: Array.isArray(rec.cited_clauses)
        ? (rec.cited_clauses as RunResult["citedClauses"])
        : undefined,
    };
    setModal(result);
  }

  const showBody = !loading && !error && !data?.note;

  // One queue card. Shared by the cards view, the kanban columns and the
  // split view's detail pane (expanded=true → all fields) so the
  // presentations never drift.
  function renderCard(row: Record<string, unknown>, idx: number, expanded = false) {
    const key = rowKey(row, idx);
    const hasRec = !!stagedRecommendation(row);
    // A staged recommendation is clickable even without a configured row-click
    // action — clicking VIEWS the recommendation instantly.
    const clickable = !!rowClickAction || hasRec;
    const busy = busyRows.has(key);
    // Honor a configured NAVIGATE row-click first (don't hijack the app's
    // navigation); otherwise a staged row VIEWS its recommendation instantly,
    // and an agent_action row-click runs on demand.
    const onCardClick = rowClickAction?.navigate
      ? () => fireAction(rowClickAction, row, key)
      : hasRec
      ? () => openStaged(row, key)
      : rowClickAction
      ? () => fireAction(rowClickAction, row, key)
      : undefined;
    const secondaryCols = (panel.secondary_columns ?? []).filter((c) => cols.includes(c));
    const fieldCols = (expanded
      ? cols.filter((x) => x !== titleCol && x !== badgeCol && x !== "source" && !_HIDDEN_COL_RE.test(x))
      : pickCardFields(cols, titleCol, badgeCol, !!(panel.columns && panel.columns.length))
    ).filter((c) => !secondaryCols.includes(c));
    const aiRecommended = isAiRecommended(row["source"]);
    const primaryLabel = buttonActions.find((x) => !_DANGER_ACTION_RE.test(x.label))?.label;
    return (
      <div
        key={key}
        className={`q-card${clickable ? " clickable" : ""}${busy ? " q-card-busy" : ""}`}
        // Suppress the card-level row-click while THIS row is mid-action, so the
        // card body can't re-fire while a button action is in flight.
        onClick={onCardClick && !busy ? onCardClick : undefined}
      >
        <div className="q-card-head">
          <div className="q-card-title">{formatCell(row[titleCol])}</div>
          {aiRecommended && (
            <span className="q-badge q-badge-purple" title="Recommended by the app's AI agent">
              AI-recommended
            </span>
          )}
          {badgeCol && row[badgeCol] != null && (
            <span className={qBadge(row[badgeCol])}>{formatCell(row[badgeCol])}</span>
          )}
        </div>
        <div className="q-card-fields">
          {fieldCols.map((c) => (
            <div className="q-field" key={c}>
              <span className="q-field-k">{prettyKey(c)}</span>
              <span className="q-field-v">{renderFormatted(row[c], c, row)}</span>
            </div>
          ))}
        </div>
        {secondaryCols.length > 0 && (
          <div className="q-card-secondary">
            {secondaryCols.map(
              (c) =>
                row[c] != null &&
                String(row[c]) !== "" && (
                  <span className="q-sec" key={c}>
                    {prettyKey(c)}: {formatCell(row[c], c)}
                  </span>
                ),
            )}
          </div>
        )}
        {buttonActions.length > 0 && (
          <div className="q-card-actions">
            {buttonActions.map((a) => {
              const danger = _DANGER_ACTION_RE.test(a.label);
              const primary = !danger && a.label === primaryLabel;
              return (
              <button
                key={a.label}
                type="button"
                className={`q-btn${primary ? " q-btn-primary" : ""}${danger ? " q-btn-danger" : ""}`}
                disabled={busy}
                onClick={(e) => {
                  e.stopPropagation();
                  fireAction(a, row, key);
                }}
              >
                {busy ? (
                  <><span className="q-spin" /> {a.label}</>
                ) : (
                  <><Icon name={a.icon} size={13} /> {a.label}</>
                )}
              </button>
              );
            })}
          </div>
        )}
        {busy && (
          // Visible run-in-progress band — the button spinner alone is easy
          // to miss during a 20-60s agent run, which reads as "nothing is
          // happening" (live demo feedback, 2026-07-22).
          //
          // "usually under a minute" was measured wrong: real acme-bank runs
          // took 91s, 108s and 162s. A promise the run then breaks is worse
          // than no promise — the officer starts wondering if it hung at the
          // exact moment it is working normally. State the honest range and
          // say what it is doing, so waiting reads as progress.
          <div className="q-running" role="status" aria-live="polite">
            <span className="q-spin" />
            <span>
              <b>AI is analyzing this case…</b> reading the record, payment
              history and policy, and checking what your team has taught —
              usually one to three minutes.
            </span>
          </div>
        )}
        {results[key] && (
          <button
            type="button"
            className={`q-result q-result-${statusTone(results[key].decision ?? results[key].status)}`}
            onClick={(e) => {
              e.stopPropagation();
              setModal(results[key]);
            }}
          >
            <span className="q-result-dot" />
            {results[key].decision
              ? normalizeDecision(results[key].decision as string)
              : prettyKey(results[key].status)}
            <span className="q-result-more">View ›</span>
          </button>
        )}
        {!results[key] && hasRec && (
          // PERSISTENT staged-recommendation chip — the server joins the
          // pending review onto the row (`_recommendation`), so every tab and
          // every officer sees it, not just the session that fired the run.
          <button
            type="button"
            className={`q-result q-result-${statusTone(stagedRecommendation(row)?.decision ?? "pending_review")}`}
            onClick={(e) => {
              e.stopPropagation();
              openStaged(row, key);
            }}
          >
            <span className="q-result-dot" />
            {typeof stagedRecommendation(row)?.decision === "string"
              ? normalizeDecision(stagedRecommendation(row)!.decision as string)
              : "Pending review"}
            <span className="q-result-more">View ›</span>
          </button>
        )}
      </div>
    );
  }

  // Kanban: group the filtered rows by the configured column. Distinct values
  // become ordered columns (preserving first-seen order).
  const kanbanColumns = kanbanCol
    ? (() => {
        const groups = new Map<string, Record<string, unknown>[]>();
        for (const row of filtered) {
          const v = String(row[kanbanCol] ?? "—");
          if (!groups.has(v)) groups.set(v, []);
          groups.get(v)!.push(row);
        }
        return Array.from(groups.entries()).map(([value, rows]) => ({ value, rows }));
      })()
    : [];

  return (
    <div className="q-wrap">
      <div className="q-toolbar">
        <div className="q-search">
          <input
            placeholder="Search…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {facets.map((f) => (
          <select
            key={f.col}
            className={`q-select${filters[f.col] ? " active" : ""}`}
            value={filters[f.col] ?? ""}
            onChange={(e) => setFilters((p) => ({ ...p, [f.col]: e.target.value }))}
          >
            <option value="">{prettyKey(f.col)}: all</option>
            {f.values.map((v) => (
              <option key={v} value={v}>
                {prettyKey(f.col)}: {v}
              </option>
            ))}
          </select>
        ))}
        {(search.trim() !== "" || Object.values(filters).some(Boolean)) && (
          <button
            type="button"
            className="q-btn"
            title="Clear search & filters"
            onClick={() => { setSearch(""); setFilters({}); setPage(1); }}
          >
            ✕ Clear
          </button>
        )}
        <div className="q-spacer" />
        {data?.truncated && (
          <span
            className="q-badge q-badge-amber"
            title="The source has more rows than were loaded — narrow your filters to see the rest."
            style={{ marginRight: 8 }}
          >
            ⚠ first {allRows.length} only
          </span>
        )}
        <span className="q-count">
          <b>{filtered.length}</b>
          {filtered.length !== allRows.length ? ` of ${allRows.length}` : ""}{" "}
          {filtered.length === 1 ? "row" : "rows"}
        </span>
        <div className="q-viewtoggle">
          <button
            type="button"
            className={view === "cards" ? "active" : ""}
            onClick={() => setView("cards")}
          >
            ▦ Cards
          </button>
          <button
            type="button"
            className={view === "table" ? "active" : ""}
            onClick={() => setView("table")}
          >
            ≣ Table
          </button>
          <button
            type="button"
            className={view === "split" ? "active" : ""}
            onClick={() => setView("split")}
          >
            ◫ Split
          </button>
          {kanbanCol && (
            <button
              type="button"
              className={view === "kanban" ? "active" : ""}
              onClick={() => setView("kanban")}
            >
              ▤ Board
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="q-skel">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="q-skel-card" />
          ))}
        </div>
      )}
      {!loading && error && (
        <div className="q-empty">
          <span className="q-empty-icon">⚠</span>Error: {error}
        </div>
      )}
      {!loading && !error && data?.note && (
        <div className="q-empty">
          <span className="q-empty-icon">ℹ</span>
          {data.note}
        </div>
      )}
      {showBody && filtered.length === 0 && (
        <div className="q-empty">
          <span className="q-empty-icon">∅</span>
          {/* Search and filters run over the rows THIS panel loaded, and the
              panel is capped. Saying a flat "no rows match" when the set was
              truncated reports a real record as absent: searching a live
              application id that sits past the cap returned "no rows match",
              which on a credit queue reads as "this file does not exist".
              When the load was capped, say so and say what to do about it. */}
          {allRows.length === 0
            ? "No rows yet."
            : data?.truncated
            ? `Not in the ${allRows.length} rows loaded here — this panel shows `
              + "only the first page of a larger set, and search covers just "
              + "those. Narrow the filters above to load a different slice."
            : "No rows match your search / filters."}
        </div>
      )}

      {showBody && filtered.length > 0 && view === "cards" && (
        <div className="q-cards">
          {paged.map((row, idx) => renderCard(row, idx))}
        </div>
      )}

      {showBody && filtered.length > 0 && view === "split" && (() => {
        // Master-detail (C4): list on the left; the selected record's FULL
        // card (all fields + actions + chips) on the right. Selection falls
        // back to the first row of the current page.
        const sel =
          paged.find((r, i) => rowKey(r, i) === selectedKey) ?? paged[0];
        const selIdx = paged.indexOf(sel);
        return (
          <div className="q-split">
            <div className="q-split-list" role="listbox">
              {paged.map((row, idx) => {
                const key = rowKey(row, idx);
                const active = key === rowKey(sel, selIdx);
                return (
                  <button
                    key={key}
                    type="button"
                    role="option"
                    aria-selected={active}
                    className={`q-split-item${active ? " active" : ""}`}
                    onClick={() => setSelectedKey(key)}
                  >
                    <span className="q-split-item-title">
                      {formatCell(row[titleCol])}
                    </span>
                    {badgeCol && row[badgeCol] != null && (
                      <span className={qBadge(row[badgeCol])}>
                        {formatCell(row[badgeCol])}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div className="q-split-detail">
              {sel ? renderCard(sel, selIdx, true) : null}
            </div>
          </div>
        );
      })()}

      {showBody && filtered.length > 0 && view === "kanban" && kanbanCol && (
        <div className="q-kanban">
          {kanbanColumns.map(({ value, rows }) => (
            <div className="q-kanban-col" key={value}>
              <div className="q-kanban-col-head">
                <span className={badgeClass(value)}>{prettyKey(value)}</span>
                <span className="q-kanban-count">{rows.length}</span>
              </div>
              <div className="q-kanban-col-body">
                {rows.map((row, idx) => renderCard(row, idx))}
              </div>
            </div>
          ))}
        </div>
      )}

      {showBody && filtered.length > 0 && view === "table" && (
        <div className="q-table-wrap">
          <table className="q-table">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th
                    key={c}
                    className={sortCol === c ? "sorted" : ""}
                    onClick={() => {
                      if (sortCol === c) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
                      else {
                        setSortCol(c);
                        setSortDir("asc");
                      }
                    }}
                  >
                    {prettyKey(c)}
                    <span className="q-sort">
                      {sortCol === c ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                    </span>
                  </th>
                ))}
                {buttonActions.length > 0 && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {paged.map((row, idx) => {
                const key = rowKey(row, idx);
                const hasRec = !!stagedRecommendation(row);
                const clickable = !!rowClickAction || hasRec;
                const onRowClick = rowClickAction?.navigate
                  ? () => fireAction(rowClickAction, row, key)
                  : hasRec
                  ? () => openStaged(row, key)
                  : rowClickAction
                  ? () => fireAction(rowClickAction, row, key)
                  : undefined;
                return (
                  <tr
                    key={key}
                    className={clickable ? "clickable" : ""}
                    onClick={onRowClick}
                  >
                    {cols.map((c) => {
                      const aiRec = isAiRecommended(row["source"]);
                      return (
                      <td key={c}>
                        {c === titleCol && aiRec && (
                          <span
                            className="q-badge q-badge-purple"
                            title="Recommended by the app's AI agent"
                            style={{ marginRight: 6 }}
                          >
                            AI
                          </span>
                        )}
                        {c === badgeCol && row[c] != null ? (
                          <span className={qBadge(row[c])}>{formatCell(row[c])}</span>
                        ) : (
                          renderFormatted(row[c], c, row)
                        )}
                      </td>
                      );
                    })}
                    {buttonActions.length > 0 && (
                      <td>
                        {buttonActions.map((a) => {
                          const danger = _DANGER_ACTION_RE.test(a.label);
                          const primary =
                            !danger &&
                            a.label === buttonActions.find((x) => !_DANGER_ACTION_RE.test(x.label))?.label;
                          return (
                          <button
                            key={a.label}
                            type="button"
                            className={`q-btn${primary ? " q-btn-primary" : ""}${danger ? " q-btn-danger" : ""}`}
                            disabled={busyRows.has(key)}
                            style={{ marginRight: 4 }}
                            onClick={(e) => {
                              e.stopPropagation();
                              fireAction(a, row, key);
                            }}
                          >
                            {busyRows.has(key) ? (
                          <><span className="q-spin" /> {a.label}</>
                        ) : (
                          a.label
                        )}
                          </button>
                          );
                        })}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && totalPages > 1 && view !== "kanban" && (
        <div className="q-pagination">
          <button
            type="button"
            className="q-pagebtn"
            disabled={curPage === 1}
            onClick={() => setPage(curPage - 1)}
          >
            ‹ Prev
          </button>
          {pageNumbers(curPage, totalPages).map((p, i) =>
            p === "…" ? (
              <span key={`e${i}`} className="q-page-ellipsis">
                …
              </span>
            ) : (
              <button
                key={p}
                type="button"
                className={`q-pagebtn${p === curPage ? " active" : ""}`}
                onClick={() => setPage(p as number)}
              >
                {p}
              </button>
            ),
          )}
          <button
            type="button"
            className="q-pagebtn"
            disabled={curPage === totalPages}
            onClick={() => setPage(curPage + 1)}
          >
            Next ›
          </button>
        </div>
      )}

      {/* The data-source type/ref + "unbound" was internal plumbing leaking to
          the officer (and "unbound" read as an error). Removed from the
          published view — it belongs in builder/preview, not the operator UI. */}

      {toast && (
        <div className={`q-toast q-toast-${toast.kind}`}>
          <span style={{ flex: 1 }}>{toast.msg}</span>
          <button type="button" onClick={() => setToast(null)}>
            ×
          </button>
        </div>
      )}

      {modal && (
        <RunResultModal
          // Key per record so switching directly between two rows' results
          // remounts the modal — otherwise reviewedItems / overrides / reason
          // would leak from one record into the next (same component instance).
          // Stable across same-record updates (correlationId/rowKey unchanged).
          key={modal.correlationId || modal.rowKey}
          result={modal}
          itemReviewGate={app.item_review_gate ?? "hard"}
          onClose={() => setModal(null)}
          onUpdate={(next) => {
            setModal(next);
            updateResults((m) => ({ ...m, [next.rowKey]: next }));
            // Re-pull the queue so a freshly-applied write shows up.
            if (next.status !== "pending_approval") setReload((n) => n + 1);
          }}
        />
      )}
    </div>
  );
}

/** Modal showing the full outcome of an agent action — the decision, the
 *  reasoning and every structured output field. This is the "what just
 *  happened" surface for queue / form actions. */
/** Renders a proposed write's officer-overridable fields as schema-driven
 *  controls (the LLM's value is the default), with combos prepopulated from
 *  the field's OptionsSource. Non-editable payload fields stay read-only. */
function EditableProposedWrite({
  pw,
  slug,
  value,
  onChange,
}: {
  pw: PlannedWrite;
  slug: string;
  value: Record<string, unknown>;
  onChange: (field: string, v: unknown) => void;
}) {
  const editable: FieldSpec[] = pw.editable_fields ?? [];
  const editableNames = new Set(editable.map((f) => f.name));
  const [opts, setOpts] = useState<Record<string, OptionItem[]>>({});
  const [optsErr, setOptsErr] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const f of editable) {
        const src = f.options?.kind;
        if (src === "static") {
          if (!cancelled) setOpts((o) => ({ ...o, [f.name]: f.options?.values ?? [] }));
        } else if (src === "agent") {
          if (!cancelled) setOpts((o) => ({ ...o, [f.name]: pw._options?.[f.name] ?? [] }));
        } else if (src === "data_source") {
          try {
            const res = await runtimeFetch(
              `/api/apps/${encodeURIComponent(slug)}/field-options`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  field: f.name,
                  source_id: pw.source_id,
                  dataset_id: pw.dataset_id,
                  action_id: pw.action_id,
                  context: { record: pw.payload ?? {} },
                }),
              },
            );
            if (!res.ok) {
              // RULE #1: an HTTP failure must surface, not look like "no options"
              // (an empty allow-list could even reject a valid override).
              console.error("[field-options] http", res.status);
              if (!cancelled) setOptsErr((e) => ({ ...e, [f.name]: true }));
              continue;
            }
            const b = (await res.json().catch(() => ({}))) as { options?: OptionItem[] };
            if (!cancelled) {
              setOpts((o) => ({ ...o, [f.name]: b.options ?? [] }));
              setOptsErr((e) => ({ ...e, [f.name]: false }));
            }
          } catch (err) {
            // RULE #1: don't swallow — control falls back to free text, but the
            // officer must SEE that the option list couldn't be loaded.
            console.error("[field-options] load failed", err);
            if (!cancelled) setOptsErr((e) => ({ ...e, [f.name]: true }));
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, pw.action_id]);

  const payload = pw.payload ?? {};
  const ctrl: React.CSSProperties = {
    display: "block",
    width: "100%",
    marginTop: 4,
    padding: "6px 8px",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 13,
  };
  const readonlyRest = Object.fromEntries(
    Object.entries(payload).filter(([k]) => !editableNames.has(k)),
  );

  return (
    <div>
      {editable.map((f) => {
        const cur = (f.name in value ? value[f.name] : payload[f.name]) as unknown;
        const fOpts = opts[f.name] ?? [];
        const asSelect =
          f.control === "select" ||
          f.control === "radio" ||
          (f.options && f.options.kind !== undefined && fOpts.length > 0) ||
          fOpts.length > 0;
        const inputType =
          f.control === "number" || f.control === "currency"
            ? "number"
            : f.control === "date"
            ? "date"
            : f.control === "datetime"
            ? "datetime-local"
            : "text";
        return (
          <label key={f.name} style={{ display: "block", margin: "8px 0" }}>
            <span style={{ fontSize: 12, color: "#6b7280" }}>
              {f.label ?? prettyKey(f.name)}
            </span>
            {asSelect ? (
              <select
                value={String(cur ?? "")}
                onChange={(e) => onChange(f.name, e.target.value)}
                style={ctrl}
              >
                {/* The agent did not propose a value for this field (it's not in
                    the planned write — e.g. restore keeps the existing crew). Show
                    a clear "unchanged" placeholder instead of silently selecting
                    the first option, so the officer isn't misled into thinking a
                    change is proposed (and an empty value sends no override). */}
                {(cur == null || cur === "") && (
                  <option value="">— keep current (unchanged) —</option>
                )}
                {cur != null && cur !== "" &&
                  !fOpts.some((o) => String(o.value) === String(cur)) && (
                    <option value={String(cur)}>{String(cur)} (current)</option>
                  )}
                {fOpts.map((o, oi) => (
                  <option key={oi} value={String(o.value)}>
                    {o.label ?? String(o.value)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type={inputType}
                value={String(cur ?? "")}
                placeholder={f.placeholder}
                onChange={(e) => onChange(f.name, e.target.value)}
                style={ctrl}
              />
            )}
            {optsErr[f.name] && (
              <span style={{ fontSize: 11, color: "#b91c1c", display: "block" }}>
                ⚠ couldn&apos;t load options — enter a value manually
              </span>
            )}
            {f.help && (
              <span style={{ fontSize: 11, color: "#9ca3af" }}>{f.help}</span>
            )}
          </label>
        );
      })}
      {Object.keys(readonlyRest).length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 11, color: "var(--citra-muted, #6b7280)", marginBottom: 2 }}>
            Locked (set by the agent)
          </div>
          {/* Label/value rows under a clear heading — makes the editable-vs-locked
              boundary explicit instead of a headless JSON blob. */}
          <dl className="dt-fields">
            {Object.entries(readonlyRest).map(([k, v]) => (
              <div className="dt-field" key={k}>
                <dt>{prettyKey(k)}</dt>
                <dd>{formatCellNode(v, k)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

/** Renders a modal into <body> so its fixed overlay escapes the app-shell
 *  stacking context and the sticky .app-header never paints over it (the
 *  close button was getting trapped behind the header). Mount-gated so SSR
 *  emits nothing and hydration stays clean. */
function ModalPortal({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted || typeof document === "undefined") return null;
  return createPortal(children, document.body);
}

/** The correction capture control — taxonomy chips + free text.
 *
 *  ONE component for BOTH ways an officer disagrees with the agent:
 *  rejecting the recommendation outright, and overriding a field then
 *  applying. They are different EVENTS in the ledger (`reject` | `override`)
 *  but the same QUESTION, and they must capture the same two halves:
 *
 *    reason_code  — aggregatable; several officers picking the same code on
 *                   comparable cases is what forms a learned judgement
 *    reason_text  — the half a human reads
 *
 *  Previously only the reject path rendered the app's taxonomy; the override
 *  path got a hardcoded generic list that merely seeded the free text and
 *  never set a code. So the richest correction the system gets — the officer
 *  changed the AI's value AND said why — always landed with reason_code=null,
 *  and consolidation can never form a clause from an uncoded cluster.
 */
/** The correction capture control — free text, and nothing else.
 *
 *  ONE component for BOTH ways an officer disagrees with the agent: rejecting
 *  the recommendation, and overriding a field then applying. Different EVENTS
 *  in the ledger (`reject` | `override`), same question.
 *
 *  There is deliberately no reason-code picker. The taxonomy was app-specific
 *  vocabulary an LLM had to invent, and it invented DECLINE reasons — a lending
 *  app offered "FOIR above policy cap" to an officer approving a loan the agent
 *  had rejected. Every chip on screen argued for the decision being overturned.
 *  The category a judgement is scoped to comes from the case's own facets, and
 *  what changed comes from the override delta; neither needs a human to label
 *  it. What only the officer can supply is the reasoning, so that is all we ask
 *  for — and we ask for enough of it to be worth storing.
 */
function ReasonPicker({
  reason,
  onReason,
  disabled,
  placeholder,
  tone = "danger",
}: {
  reason: string;
  onReason: (s: string) => void;
  disabled: boolean;
  placeholder: string;
  tone?: "danger" | "accent";
}) {
  const words = reasonWordCount(reason);
  const short = words < MIN_REASON_WORDS;
  // Past the soft ceiling the correction is still recorded but stops
  // clustering, so it never becomes a clause. Flagged, never blocked --
  // see SOFT_MAX_REASON_WORDS in ItemFindingReview for the measurement.
  const rambling = words > SOFT_MAX_REASON_WORDS;
  return (
    <>
      <textarea
        value={reason}
        onChange={(e) => onReason(e.target.value)}
        placeholder={placeholder}
        rows={3}
        maxLength={REASON_MAX}
        disabled={disabled}
        aria-describedby="citra-reason-count"
        style={{
          width: "100%",
          fontSize: 13,
          padding: 8,
          borderRadius: 8,
          border: `1px solid ${
            short && words > 0
              ? "var(--citra-warning, #d97706)"
              : "var(--citra-border, #e5e7eb)"
          }`,
        }}
      />
      <div
        id="citra-reason-count"
        aria-live="polite"
        style={{
          alignSelf: "flex-end",
          fontSize: 11,
          color: short || rambling
            ? "var(--citra-warning,#d97706)"
            : "var(--citra-muted,#6b7280)",
        }}
      >
        {/* NOT "1/10". That reads as a CAP -- and the satisfied branch below
            really does show a cap (`${reason.length}/${REASON_MAX}`), so the
            same X/Y shape meant "limit" one moment and "floor" the next.
            Officers read ten words as the most they may write. Count DOWN what
            is still owed; no fraction appears while the rule is a minimum. */}
        {short
          ? `${MIN_REASON_WORDS - words} more word${MIN_REASON_WORDS - words === 1 ? "" : "s"} — say what the agent got wrong (${MIN_REASON_WORDS} minimum)`
          : rambling
          ? `${words} words — long reasons stop matching similar cases; keep to the one thing that was wrong`
          : `${words} words · ${reason.length}/${REASON_MAX}`}
      </div>
    </>
  );
}

/** What the officer's own team has already taught, for this kind of case.
 *
 *  The clause store fed these into the run, and until now nothing showed them.
 *  That hid the entire product: an officer could not see that colleagues had
 *  established a judgement, could not tell when the agent FOLLOWED one, and —
 *  worse — could not tell when it went against one. A judgement the agent
 *  overrode is the single most important thing on this screen when it happens,
 *  so it is called out rather than listed quietly with the others.
 */
function TeamJudgements({
  clauses,
}: {
  clauses: NonNullable<RunResult["citedClauses"]>;
}) {
  if (!clauses.length) return null;
  // The relation vocabulary is fixed by the audit-block contract the agent is
  // prompted against (runtime.py: "applied" | "overruled" | "overrode_by_rule").
  // This read "cited", which the agent never emits, so EVERY applied judgement
  // fell through to the default and rendered "available, not cited" — on a
  // screen whose entire job is to show the officer that their team's judgement
  // was used. Observed live on acme-bank with C-002 applied and named in the
  // agent's own narrative, while this block said it was not cited.
  const tone = (rel?: string) =>
    rel === "overrode_by_rule"
      ? { line: "var(--citra-warning, #d97706)", label: "the agent went against this" }
      : rel === "overruled"
      ? { line: "var(--citra-warning, #d97706)", label: "the agent set this aside" }
      : rel === "applied" || rel === "cited"
      ? { line: "var(--citra-accent, #2563eb)", label: "the agent used this" }
      : { line: "var(--citra-border, #e5e7eb)", label: "available, not cited" };
  return (
    <div className="rr-section">
      <div className="rr-section-head">
        What your team has taught ({clauses.length})
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
        {clauses.map((c, i) => {
          const t = tone(c.relation);
          return (
            <div
              key={c.clause_id ?? i}
              style={{
                borderLeft: `3px solid ${t.line}`,
                padding: "4px 0 4px 10px",
              }}
            >
              <div style={{ fontSize: 13 }}>{c.text}</div>
              <div style={{ fontSize: 11, color: "var(--citra-muted, #6b7280)" }}>
                {t.label}
                {c.support_count ? ` · ${c.support_count} officer(s) behind it` : ""}
                {c.status === "candidate" ? " · one officer's view, not yet corroborated" : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Read-only strip of the case signature the model saw. */
function FacetStrip({ facets }: { facets: string[] }) {
  if (!facets.length) return null;
  return (
    <div className="rr-section">
      <div className="rr-section-head">This case</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
        {facets.map((f) => {
          const [family, ...rest] = String(f).split(":");
          const value = rest.join(":");
          return (
            <span
              key={f}
              title={`${family} = ${value}`}
              style={{
                fontSize: 11,
                padding: "3px 8px",
                borderRadius: 999,
                border: "1px solid var(--citra-border, #e5e7eb)",
                color: "var(--citra-muted, #6b7280)",
                whiteSpace: "nowrap",
              }}
            >
              {prettyKey(family)}: {value || "—"}
            </span>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: "var(--citra-muted, #6b7280)", marginTop: 6 }}>
        Anything you teach here comes back on cases like these — and only those.
      </div>
    </div>
  );
}

function RunResultModal({
  result,
  onClose,
  onUpdate,
  itemReviewGate = "hard",
}: {
  result: RunResult;
  onClose: () => void;
  onUpdate?: (next: RunResult) => void;
  itemReviewGate?: "hard" | "soft" | "none";
}) {
  const tone = statusTone(result.decision ?? result.status);
  const outputEntries = Object.entries(result.outputs ?? {}).filter(
    ([, v]) => v != null && v !== "",
  );
  const isPending = result.status === "pending_approval";
  const plannedWrites = result.plannedWrites ?? [];
  // Only claim a write was applied when one actually committed. A completed
  // run with no committed writes is a read-only recommendation — saying "the
  // action ran and was applied" there is a false positive (e.g. a triage that
  // only classifies, or a run that proposed nothing). "Applied" is reserved
  // for the post-Apply result where writeEvents carry committed (status=ok)
  // writes; pending plans say "proposed — review and Apply to commit".
  const committedWrites = (result.writeEvents ?? []).filter(
    (w) => w.status === "ok",
  ).length;
  const verdictNote = isPending
    ? "· proposed by the agent — review the plan below and Apply to commit"
    : result.status === "failed"
    ? "· the action ran and did not complete"
    : committedWrites > 0
    ? "· the action ran and was applied"
    : "· the agent ran — recommendation only, nothing was committed";

  const [busy, setBusy] = useState<"apply" | "reject" | "cancel" | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  // Officer overrides keyed by planned-write index → { field: newValue }.
  const [overrides, setOverrides] = useState<Record<number, Record<string, unknown>>>({});
  // WHY the officer rejects/changes a recommendation — sent as decision_reason
  // so the model can learn the correction (the self-improving signal).
  const [reason, setReason] = useState("");
  // WHICH KIND of wrong — the officer's pick from the app's closed taxonomy.
  // This is the half of the correction that aggregates; the free text is the
  // half a human reads. Required (when the app declares codes) on reject.
  // Two-step reject: the first "Reject recommendation" click reveals a reason
  // prompt (mirrors the per-item ItemFindingReview flow); the reason is then
  // required before the rejection commits — and is fed back as the learning
  // signal. Without this the overall reject fired with no captured reason.
  const [showReject, setShowReject] = useState(false);
  const hasOverrides = Object.values(overrides).some(
    (o) => o && Object.keys(o).length > 0,
  );
  // "Decision from rejected to approved" — the officer sees the delta they are
  // about to commit, in the field's own words. Naming the direction is what
  // makes the prompt unambiguous: "reject" means the loan on one screen and the
  // recommendation on another, and the officer should never have to guess which
  // one they are answering about.
  const overrideSummary = Object.entries(overrides)
    .flatMap(([i, fields]) =>
      Object.entries(fields ?? {}).map(([field, to]) => {
        const before = plannedWrites[Number(i)]?.payload?.[field];
        const label =
          (plannedWrites[Number(i)]?.editable_fields ?? []).find(
            (f) => f.name === field,
          )?.label ?? prettyKey(field);
        return before === undefined || before === null || before === ""
          ? `${label} to ${String(to)}`
          : `${label} from ${String(before)} to ${String(to)}`;
      }),
    )
    .join(", ");
  // An override must carry BOTH halves of a correction before it commits: the
  // code (aggregatable) and the text (human-readable). Same bar the reject path
  // has always enforced — an override is a stronger disagreement, not a weaker
  // one, and it is the one that actually writes to the system of record.
  const overrideNeedsReason =
    hasOverrides && reasonWordCount(reason) < MIN_REASON_WORDS;

  // Per-item review gate. Only relevant when the run produced item findings
  // (analyzed images/documents). `reviewedItems` accrues each item_id the
  // officer dispositions (accept/reject/cancel) via ItemFindingReview.onResolved.
  const itemFindings = result.itemFindings ?? [];
  const [reviewedItems, setReviewedItems] = useState<Set<string>>(new Set());
  // A case-level fraud finding (modality="case") is EVIDENCE only — it stays
  // dispositionable (Confirm/Dismiss trains the L2 fraud rubric) but must NEVER
  // block Apply. Fraud highlights, it never gates. Only artifact / API findings
  // (image / document / api) count toward the review gate — and the gate copy.
  const gatedFindings = itemFindings.filter((f) => f.modality !== "case");
  const unreviewedCount = gatedFindings.filter(
    (f) => !reviewedItems.has(f.item_id),
  ).length;
  // Gate only bites when there ARE gate-eligible items and the app opted in.
  const gateActive = gatedFindings.length > 0 && itemReviewGate !== "none";
  const hardBlocked = gateActive && itemReviewGate === "hard" && unreviewedCount > 0;

  async function decide(decision: "approve" | "reject" | "cancel") {
    // Soft gate: confirm before applying with items still unreviewed. (Hard gate
    // disables the Apply button outright; reject/cancel are never gated.)
    if (
      decision === "approve" &&
      gateActive &&
      itemReviewGate === "soft" &&
      unreviewedCount > 0 &&
      !window.confirm(
        `${unreviewedCount} of ${gatedFindings.length} item(s) have not been reviewed. Apply anyway?`,
      )
    ) {
      return;
    }
    // Reject requires a reason — same contract as the per-item review, and it
    // is the signal the model learns the correction from.
    if (decision === "reject" && reasonWordCount(reason) < MIN_REASON_WORDS) {
      setActionErr(
        `Write at least ${MIN_REASON_WORDS} words — a correction the app can `
        + "learn from has to say what the agent got wrong.",
      );
      setShowReject(true);
      return;
    }
    // Overriding the agent and applying is a correction too — and unlike a
    // reject it COMMITS. Hold it to the same bar rather than letting the
    // strongest disagreement in the system through unexplained.
    if (decision === "approve" && hasOverrides
        && reasonWordCount(reason) < MIN_REASON_WORDS) {
      setActionErr(
        `Write at least ${MIN_REASON_WORDS} words — you overruled the agent, `
        + "and that is the correction the app learns from.",
      );
      return;
    }
    if (!result.slug || !result.correlationId || !onUpdate) {
      setActionErr("cannot resolve: missing slug or correlation id");
      return;
    }
    setBusy(decision === "approve" ? "apply" : decision);
    setActionErr(null);
    try {
      const body =
        decision === "approve"
          ? {
              decision,
              // Send overrides aligned to planned_writes by index. Empty {}
              // for untouched writes; the backend only accepts changes to
              // declared editable fields and re-validates via dry_run.
              overrides: plannedWrites.map((_, i) => overrides[i] ?? {}),
              // Echo the hash of the proposal we displayed so the server can
              // verify it's committing exactly what the officer reviewed.
              expected_plan_hash: result.planHash,
              // WHY the officer changed it — fed back to the model so it learns
              // the correction (the self-improving signal).
              decision_reason: reason.trim() || undefined,

            }
          : {
              decision,
              note: reason.trim() || (decision === "cancel" ? "user cancelled" : "user rejected"),
              decision_reason: reason.trim() || undefined,

            };
      const res = await runtimeFetch(
        `/api/apps/${encodeURIComponent(result.slug)}/approve/${encodeURIComponent(result.correlationId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      // Keepalive-streamed apply (no 504 on the plan replay); the terminal
      // `done` event carries the RunResponse. Errors throw → outer catch.
      const b = await readAgentStream<Record<string, unknown>>(res);
      onUpdate({
        ...result,
        status: String(b.status ?? (decision === "approve" ? "completed" : "failed")),
        decision: (b.decision as string | undefined) ?? result.decision,
        reasoning: (b.reasoning as string | undefined) ?? result.reasoning,
        outputs: (b.outputs as Record<string, unknown> | undefined) ?? result.outputs,
        error: b.error as string | undefined,
        plannedWrites: undefined,
        writeEvents: Array.isArray(b.write_events)
          ? (b.write_events as RunResult["writeEvents"])
          : result.writeEvents,
      });
    } catch (e) {
      setActionErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <ModalPortal>
    <div className="rr-overlay" onClick={onClose}>
      <div className="rr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="rr-head">
          <div>
            <div className="rr-title">{result.label}</div>
            <div className="rr-sub">{result.rowTitle}</div>
          </div>
          {/* The glyph is the whole label, so without aria-label a screen
              reader announces "multiplication sign, button" on the only way
              out of a modal that has just proposed a write to a credit file. */}
          <button type="button" className="rr-close" aria-label="Close"
                  title="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="rr-body">
          <div className="rr-section">
            <div className="rr-section-head">
              {isPending ? "Agent recommendation" : "Outcome"}
            </div>
            <div className="rr-verdict-row">
              {isPending ? (
                <>
                  <span style={{ color: "#6b7280", fontSize: 13 }}>
                    Agent says:
                  </span>
                  {/* Neutral grey badge in plan mode — colouring it
                      green/red would let the officer's eye accept the
                      LLM's verdict before they reviewed the proposed
                      changes. */}
                  <span
                    className="rr-verdict"
                    style={{
                      background: "#f3f4f6",
                      color: "#111827",
                      border: "1px solid #e5e7eb",
                    }}
                  >
                    {result.decision
                      ? normalizeDecision(result.decision)
                      : prettyKey(result.status)}
                  </span>
                </>
              ) : (
                <span className={`rr-verdict rr-verdict-${tone}`}>
                  <span className="rr-verdict-dot" />
                  {result.decision
                    ? result.decision
                    : prettyKey(result.status)}
                </span>
              )}
              <span className="rr-verdict-note">{verdictNote}</span>
            </div>
          </div>
          {result.error && <div className="error">{result.error}</div>}
          {/* The declared rubric, between the verdict and the per-item review.
              Supporting detail for the recommendation — never a replacement for
              the reasons, and never a second screen the officer has to
              reconcile against this one. */}
          {result.scorecard && (
            <div className="rr-section">
              <ScorecardView
                card={result.scorecard}
                slug={result.slug}
                correlationId={result.correlationId}
                // The recomputed card comes back from the server, so the grid
                // the officer sees and the grade the ledger records are one
                // calculation, never two that could disagree.
                onCardChange={(next) => onUpdate?.({ ...result, scorecard: next })}
              />
            </div>
          )}
          {itemFindings.length > 0 && (
            <div className="rr-section">
              <div className="rr-section-head">
                Per-item review ({itemFindings.length})
                {gateActive && (
                  <span
                    style={{
                      marginLeft: 8,
                      fontSize: 11,
                      fontWeight: 500,
                      color:
                        unreviewedCount > 0
                          ? "var(--citra-warning, #d97706)"
                          : "var(--citra-success, #16a34a)",
                    }}
                  >
                    {unreviewedCount > 0
                      ? `${unreviewedCount} not yet reviewed`
                      : "all reviewed ✓"}
                  </span>
                )}
              </div>
              {itemFindings.map((f) => {
                const cite = (f.citations || []).find(
                  (c) => (c as Record<string, unknown>).source_url
                ) as { source_url?: string } | undefined;
                return (
                  <ItemFindingReview
                    key={f.item_id}
                    slug={result.slug || ""}
                    finding={f}
                    imageUrl={cite?.source_url}
                    onResolved={() =>
                      setReviewedItems((prev) => {
                        const next = new Set(prev);
                        next.add(f.item_id);
                        return next;
                      })
                    }
                  />
                );
              })}
            </div>
          )}
          {result.reasoning && (
            <div className="rr-section">
              <div className="rr-section-head">Reasoning</div>
              <Markdown content={result.reasoning} />
            </div>
          )}
          {/* Precedent receipts — the past cases the AI relied on (≈) or
              deliberately deviated from (≠). Trust surface: the officer sees
              WHICH history backs the recommendation, not just that some does. */}
          {outputEntries.length > 0 && (
            <div className="rr-section">
              <div className="rr-section-head">
                {isPending ? "Agent narrative" : "Result"}
              </div>
              <dl className="dt-fields">
                {outputEntries.map(([k, v]) => {
                  // Wide values — the markdown narrative (`text`) and JSON blocks —
                  // must span ALL grid columns. Otherwise the auto-fill grid drops
                  // them into a single ~220px track and the content squeezes to the
                  // left with empty space beside it. Short scalar field/value pairs
                  // keep tiling across columns as before.
                  const isWide =
                    (typeof v === "object" && v !== null) ||
                    (typeof v === "string" &&
                      (k === "text" || /[\n#|]|\*\*/.test(v)));
                  return (
                  <div
                    className={isWide ? "dt-field dt-field-wide" : "dt-field"}
                    key={k}
                  >
                    <dt>{prettyKey(k)}</dt>
                    <dd>
                      {typeof v === "object" ? (
                        <pre className="rr-json">{JSON.stringify(v, null, 2)}</pre>
                      ) : typeof v === "string" &&
                        (k === "text" || /[\n#|]|\*\*/.test(v)) ? (
                        // The agent narrative (`text`) is full markdown — render
                        // it (headings/tables/lists), don't show raw ## / | / **.
                        // Shown in full here; trimming only happens in the
                        // compact history timeline, not in this review modal.
                        <Markdown content={v} />
                      ) : (
                        formatCell(v)
                      )}
                    </dd>
                  </div>
                  );
                })}
              </dl>
            </div>
          )}
          {(result.citedPrecedents?.length ?? 0) > 0 && (
            <div className="rr-section">
              <div className="rr-section-head">
                Based on past cases ({result.citedPrecedents!.length})
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                {result.citedPrecedents!.map((p, i) => (
                  <span
                    key={`${p.decision_id}-${i}`}
                    className="chip"
                    title={p.note ?? undefined}
                    style={{
                      border: "1px solid var(--citra-border, #e5e7eb)",
                      borderRadius: 12,
                      padding: "3px 10px",
                      fontSize: 12,
                      background:
                        p.relation === "differs"
                          ? "var(--citra-warn-bg, #fffbeb)"
                          : "var(--citra-ok-bg, #f0fdf4)",
                    }}
                  >
                    {p.relation === "differs" ? "≠" : "≈"} {p.decision_id}
                    {p.note ? ` — ${p.note}` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
          {/* The decision block. Everything from here down is what the officer
              ACTS on, and nothing long sits between the proposal and the
              buttons any more — the agent's narrative used to, so the officer
              read the change, scrolled through prose, then decided. */}
          <TeamJudgements clauses={result.citedClauses ?? []} />
          {isPending && <FacetStrip facets={result.caseFacets ?? []} />}
          {isPending && plannedWrites.length > 0 && (
            <div className="rr-section">
              <div className="rr-section-head">
                Proposed changes ({plannedWrites.length})
              </div>
              {plannedWrites.map((pw, i) => (
                <div
                  key={i}
                  style={{
                    border: "1px solid var(--citra-border, #e5e7eb)",
                    borderRadius: 8,
                    padding: 10,
                    marginTop: 8,
                  }}
                >
                  {/* Plain-language action label — the internal action/dataset/
                      source ids are plumbing, not decision info for the officer. */}
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>
                    {prettyKey(pw.action_id ?? "action")}
                  </div>
                  {(pw.editable_fields?.length ?? 0) > 0 ? (
                    <EditableProposedWrite
                      pw={pw}
                      slug={result.slug ?? ""}
                      value={overrides[i] ?? {}}
                      onChange={(field, v) =>
                        setOverrides((o) => ({
                          ...o,
                          [i]: { ...(o[i] ?? {}), [field]: v },
                        }))
                      }
                    />
                  ) : (
                    pw.payload &&
                    Object.keys(pw.payload).length > 0 && (
                      // Label/value rows — never a raw JSON blob for a governed write.
                      <dl className="dt-fields" style={{ marginTop: 4 }}>
                        {Object.entries(pw.payload).map(([k, v]) => (
                          <div className="dt-field" key={k}>
                            <dt>{prettyKey(k)}</dt>
                            <dd>{formatCellNode(v, k)}</dd>
                          </div>
                        ))}
                      </dl>
                    )
                  )}
                </div>
              ))}
            </div>
          )}
          {!isPending && (result.writeEvents?.length ?? 0) > 0 && (
            <div className="rr-section">
              {/* "Applied" is ONLY a committed write. A staged PLAN on a failed or
                    blocked run never committed, and calling it "Applied" tells the
                    officer a mutation happened when it did not.

                    Match the "_plan" SUFFIX, not one literal. The runtime emits TWO
                    staged kinds -- perform_action_plan and mcp_action_plan -- and this
                    knew only the first, so a blocked claim decision
                    (kind=mcp_action_plan, dry_run=false, result.ok=true) rendered as
                    "Applied changes (1 of 1)" while insurance_claims.claims was
                    untouched and decision_records held nothing. The bias is
                    deliberate: calling a commit "staged" understates and is
                    recoverable; calling a block "Applied" is the officer being told
                    the opposite of the truth. */}
              {(() => {
                const evts = result.writeEvents ?? [];
                const isPlan = (k?: string) => !!k && k.endsWith("_plan");
                const applied = evts.filter(
                  (w) => w.status === "ok" && !isPlan(w.kind),
                ).length;
                const allStaged = evts.every((w) => isPlan(w.kind));
                return (
                  <div className="rr-section-head">
                    {allStaged
                      ? `Proposed changes (${evts.length} — none committed)`
                      : `Applied changes (${applied} of ${evts.length})`}
                  </div>
                );
              })()}
              {(result.writeEvents ?? []).map((w, i) => {
                const staged = !!w.kind && w.kind.endsWith("_plan");
                const okw = w.status === "ok";
                const errDetail =
                  !okw && w.result
                    ? String(
                        (w.result as Record<string, unknown>).error ??
                          (w.result as Record<string, unknown>).detail ??
                          "",
                      )
                    : "";
                return (
                  <div
                    key={i}
                    style={{
                      border: `1px solid ${
                        staged ? "#d9770666" : okw ? "#10b98166" : "#dc262666"
                      }`,
                      borderRadius: 8,
                      padding: 10,
                      marginTop: 8,
                      background: staged ? "#fffbeb" : okw ? "#ecfdf5" : "#fef2f2",
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>
                      <span
                        className={`q-badge ${
                          staged
                            ? "q-badge-amber"
                            : okw
                              ? "q-badge-green"
                              : "q-badge-red"
                        }`}
                        style={{ marginRight: 8 }}
                      >
                        {staged
                          ? okw
                            ? "Staged — not committed"
                            : "Staging failed"
                          : okw
                            ? "Applied"
                            : "Failed"}
                      </span>
                      {w.action_id ?? "action"} on {w.dataset_id ?? "dataset"}
                    </div>
                    {/* Officer override delta — the field(s) the officer changed
                        from the agent's recommendation. Surfaced so the outcome
                        reflects what was COMMITTED, not the stale plan headline. */}
                    {w.override &&
                      Object.keys(w.override).length > 0 &&
                      Object.entries(w.override).map(([f, d]) => (
                        <div
                          key={f}
                          style={{ fontSize: 12, color: "#6d28d9", marginTop: 2 }}
                        >
                          <strong>{prettyKey(f)}</strong>:{" "}
                          <span style={{ textDecoration: "line-through", color: "#9ca3af" }}>
                            {formatCell(d?.from)}
                          </span>{" "}
                          → <strong>{formatCell(d?.to)}</strong>{" "}
                          <span style={{ color: "#6b7280" }}>(your override)</span>
                        </div>
                      ))}
                    {/* Committed field values — the payload actually written.
                        `w.args` is what was PROPOSED. Server-filled columns
                        (x-citra-fill: actor/now) are overwritten at the point
                        of write, so the proposal is wrong for exactly the
                        fields that carry the audit: this card showed
                        decided_by "credit-officer-assistant" over a row
                        reading credit-manager@acme-bank-demo.citra.ai, and a
                        decision date three weeks before the click. The MCP now
                        returns what it stamped; prefer it, and say so, so the
                        officer can tell a server fact from a model proposal. */}
                    {(() => {
                      const stamped = (w.result?.server_filled ?? {}) as Record<string, unknown>;
                      const committed = { ...(w.args ?? {}), ...stamped };
                      const HIDE = [
                        "dataset_id", "action_id", "source_id",
                        "dry_run", "idempotency_key", "confidence",
                      ];
                      const rows = Object.entries(committed).filter(
                        ([k]) => !HIDE.includes(k),
                      );
                      if (!okw || !rows.length) return null;
                      return (
                        <div style={{ fontSize: 12, color: "#374151", marginTop: 4 }}>
                          {rows.map(([k, v]) => (
                            <div key={k}>
                              <span style={{ color: "#6b7280" }}>{prettyKey(k)}:</span>{" "}
                              {typeof v === "object" && v !== null
                                ? JSON.stringify(v)
                                : formatCell(v)}
                              {k in stamped && (
                                <span style={{ color: "#6b7280" }}> · recorded by Citra</span>
                              )}
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                    {errDetail && (
                      <div style={{ fontSize: 12, color: "#991b1b", marginTop: 4 }}>
                        {errDetail}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {!isPending &&
            !result.reasoning &&
            outputEntries.length === 0 &&
            !result.error && (
              <div className="panel-empty">
                The action completed but returned no detail.
              </div>
            )}
          {isPending && (
            <div style={{ marginTop: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
              {showReject ? (
                /* Reject prompt — clicking "Reject recommendation" opens this
                   instead of firing immediately, so a reason is captured the
                   same way the per-item review asks for one. */
                <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
                  <label style={{ fontSize: 12, fontWeight: 600, color: "var(--citra-danger, #dc2626)" }}>
                    Why is the agent's proposal wrong?
                    <span style={{ fontWeight: 400, color: "var(--citra-muted)" }}>
                      {" — required; the AI learns from it"}
                    </span>
                  </label>
                  <ReasonPicker
                    reason={reason}
                    onReason={setReason}
                    disabled={busy != null}
                    placeholder="What did the agent get wrong, and what should it have done? (the AI learns from this)"
                    tone="danger"
                  />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      type="button"
                      className="q-btn q-btn-danger"
                      disabled={
                        busy != null
                        || reasonWordCount(reason) < MIN_REASON_WORDS
                      }
                      onClick={() => decide("reject")}
                    >
                      {busy === "reject" ? "Discarding…" : "Confirm — discard proposal"}
                    </button>
                    <button
                      type="button"
                      className="q-btn"
                      disabled={busy != null}
                      onClick={() => { setShowReject(false); setActionErr(null); }}
                    >
                      Back
                    </button>
                  </div>
                  {actionErr && (
                    <div style={{ color: "#dc2626", fontSize: 12, width: "100%" }}>
                      {actionErr}
                    </div>
                  )}
                </div>
              ) : (
                <>
                  {/* An override IS a correction — the officer changed the AI's
                      value. It is the richest signal the loop gets, so it asks
                      the SAME two things the reject path asks, from the SAME
                      taxonomy. (It used to offer a hardcoded generic list that
                      only seeded free text and never set a code, so every
                      override landed uncoded and could never form a clause.)
                      Named deltas, not "why did you change it?" — the officer
                      should see exactly what they are about to commit. */}
                  {hasOverrides && (
                    <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--citra-muted)" }}>
                        {overrideSummary
                          ? `You changed ${overrideSummary} — why?`
                          : "Why did you change it?"}
                        <span style={{ fontWeight: 400 }}> — required; the AI learns from it</span>
                      </label>
                      <ReasonPicker
                        reason={reason}
                        onReason={setReason}
                        disabled={busy != null}
                        placeholder="What did the agent get wrong, and what should it have done? (the AI learns from this)"
                        tone="accent"
                      />
                    </div>
                  )}
                  <button
                    type="button"
                    className="q-btn q-btn-primary"
                    disabled={busy != null || hardBlocked || overrideNeedsReason}
                    title={
                      hardBlocked
                        ? `Review all ${itemFindings.length} item(s) before applying — ${unreviewedCount} left`
                        : overrideNeedsReason
                        ? "You changed the agent's proposal — say why before applying"
                        : undefined
                    }
                    onClick={() => decide("approve")}
                  >
                    {busy === "apply" ? "Applying…" : `Apply ${plannedWrites.length || ""} change${plannedWrites.length === 1 ? "" : "s"}`}
                  </button>
                  {hardBlocked && (
                    <div style={{ width: "100%", fontSize: 12, color: "var(--citra-warning, #d97706)" }}>
                      Review all {itemFindings.length} item(s) before applying — {unreviewedCount} still pending. (Reject / Cancel are always available.)
                    </div>
                  )}
                  {/* Reject = decline the recommendation (a decision, audited as
                      rejected). Opens the reason prompt above instead of firing
                      immediately, so a reason is always captured. */}
                  <button
                    type="button"
                    className="q-btn q-btn-danger"
                    disabled={busy != null}
                    title={"Discard the agent's proposal — nothing is written to "
                           + "the record. Your reason teaches the app."}
                    onClick={() => { setActionErr(null); setShowReject(true); }}
                  >
                    Discard proposal
                  </button>
                  {/* Cancel = dismiss without deciding (audited as cancelled); both
                      leave the source untouched but record differently. */}
                  <button
                    type="button"
                    className="q-btn"
                    disabled={busy != null}
                    title={"Leave it undecided and come back later. Nothing is "
                           + "written and nothing is learned."}
                    onClick={() => decide("cancel")}
                  >
                    {busy === "cancel" ? "Closing…" : "Decide later"}
                  </button>
                  <div style={{ width: "100%", fontSize: 11,
                                color: "var(--citra-muted, #6b7280)" }}>
                    Apply writes to the record · Discard writes nothing and
                    teaches the app · Decide later writes nothing and teaches
                    nothing
                  </div>
                  {actionErr && (
                    <div style={{ color: "#dc2626", fontSize: 12, width: "100%" }}>
                      {actionErr}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
          {result.correlationId && (
            <div className="rr-cid">Run · {result.correlationId}</div>
          )}
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}

const _ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$/;

// STRICT money heuristic for table/detail CELLS — only strong monetary signals
// (deliberately excludes generic "value"/"due"/"sales" that the KPI-side
// looksMonetary matches, so a count/score column isn't rendered as rupees).
const _CELL_MONEY_RE =
  /(amount|amt|cost|price|fee|charge|balance|revenue|invoice|salary|payment|paid|billing|outstanding|inr|rupee|₹)/i;

/** Humanise an ISO-8601 date/timestamp string to the app locale. Returns null
 *  when the value isn't an ISO date (so plain strings pass through unchanged). */
function fmtCellDate(s: string): string | null {
  if (!_ISO_DATE_RE.test(s.trim())) return null;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  const { locale } = getAppLocale();
  const hasTime = /[T ]\d{2}:/.test(s);
  return hasTime
    ? d.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" })
    : d.toLocaleDateString(locale, { dateStyle: "medium" });
}

/** Detect a file/attachment-shaped value: an http(s) URL to a file, or an object
 *  descriptor {url|ref, filename?, content_type?}. */
function asFileShaped(
  v: unknown,
): { url?: string; ref?: string; filename?: string; content_type?: string } | null {
  if (typeof v === "string") {
    if (/^https?:\/\/\S+\.(png|jpe?g|gif|webp|bmp|tiff?|pdf|docx?|xlsx?|csv|txt)(\?|#|$)/i.test(v))
      return { url: v };
    return null;
  }
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    const url = typeof o.url === "string" ? o.url : undefined;
    const ref = typeof o.ref === "string" ? o.ref : undefined;
    if (url || ref)
      return {
        url,
        ref,
        filename: typeof o.filename === "string" ? o.filename : undefined,
        content_type: typeof o.content_type === "string" ? o.content_type : undefined,
      };
  }
  return null;
}

/** Enriched value→string: humanises dates, groups numbers (locale; ₹ when the
 *  column looks monetary), booleans→Yes/No, empty→"—", and summarises objects
 *  instead of dumping raw JSON. Display-only — used for cells, titles, labels. */
function formatCell(v: unknown, col?: string): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number" || typeof v === "bigint") {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    // EXACT money for cells (not compact — see fmtMoney). Use a STRICT money
    // heuristic here (not the KPI looksMonetary, which matches generic "value"/
    // "due" and would render counts/scores as ₹); else just group the number.
    return _CELL_MONEY_RE.test(col ?? "")
      ? fmtMoney(n)
      : n.toLocaleString(getAppLocale().locale);
  }
  if (typeof v === "string") {
    if (v.trim() === "") return "—";
    return fmtCellDate(v) ?? v;
  }
  if (Array.isArray(v)) {
    if (v.every((x) => x == null || typeof x !== "object"))
      return v.map((x) => formatCell(x)).join(", ");
    return `${v.length} item${v.length === 1 ? "" : "s"}`;
  }
  if (typeof v === "object") {
    const keys = Object.keys(v as object);
    return keys.length ? `{${keys.length} field${keys.length === 1 ? "" : "s"}}` : "—";
  }
  return String(v);
}

/** A compact, clickable file affordance for a cell — icon + filename, opening
 *  the (presigned) URL. An unresolved blob ref renders as a clearly-disabled
 *  "couldn't load" chip rather than a broken link (RULE #1 at the UI). */
function FileChip({ file }: { file: NonNullable<ReturnType<typeof asFileShaped>> }) {
  const fromPath = (s?: string) =>
    s ? decodeURIComponent(s.split("/").pop()?.split("?")[0] || "") : "";
  const name = file.filename || fromPath(file.url) || fromPath(file.ref) || "attachment";
  const probe = `${file.content_type ?? ""} ${file.url ?? ""} ${file.ref ?? ""}`.toLowerCase();
  const icon = /image\/|\.(png|jpe?g|gif|webp|bmp|tiff?)\b/.test(probe)
    ? "🖼"
    : /pdf/.test(probe)
    ? "📄"
    : "📎";
  const base: CSSProperties = {
    display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, maxWidth: "100%",
  };
  // No presigned URL yet (e.g. a list/queue cell, which isn't blob-resolved for
  // cost). Show a meaningful, named chip — NOT the raw ref and NOT a scary
  // "broken" state; the file opens from the record's detail view.
  if (!file.url) {
    return (
      <span style={{ ...base, color: "var(--citra-muted)" }} title="Open the record to view this file">
        {icon} {name}
      </span>
    );
  }
  return (
    <a style={{ ...base, color: "var(--citra-primary, #2563eb)" }} href={file.url} target="_blank" rel="noreferrer noopener" title={name}>
      {icon} {name}
    </a>
  );
}

/** Render a cell as a React node — a file-shaped value becomes a FileChip,
 *  everything else falls back to the enriched text formatter. */
function formatCellNode(v: unknown, col?: string): ReactNode {
  const f = asFileShaped(v);
  if (f) return <FileChip file={f} />;
  return formatCell(v, col);
}

function pageNumbers(cur: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out: (number | "…")[] = [1];
  const lo = Math.max(2, cur - 1);
  const hi = Math.min(total - 1, cur + 1);
  if (lo > 2) out.push("…");
  for (let p = lo; p <= hi; p++) out.push(p);
  if (hi < total - 1) out.push("…");
  out.push(total);
  return out;
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

interface DetailState {
  loading: boolean;
  error: string | null;
  data: DetailData | null;
}

/** Fetches GET /api/detail/{slug}/{panelId}?id= — the whole detail panel. */
function useDetailData(
  slug: string,
  panelId: string,
  recordId: string | null,
  /** Bump to re-pull — after a detail action commits a write, the record on
   *  screen is stale until it is read back. */
  reload = 0,
): DetailState {
  const [state, setState] = useState<DetailState>({
    loading: true,
    error: null,
    data: null,
  });
  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    const qs = recordId ? `?id=${encodeURIComponent(recordId)}` : "";
    // runtimeFetch carries the end-user JWT → X-User-JWT at the dept-MCP.
    runtimeFetch(`/api/detail/${encodeURIComponent(slug)}/${encodeURIComponent(panelId)}${qs}`, {
      cache: "no-store",
    })
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setState({
            loading: false,
            error: body.detail ?? body.error ?? `HTTP ${res.status}`,
            data: null,
          });
        } else {
          setState({ loading: false, error: null, data: body as DetailData });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          loading: false,
          error: err instanceof Error ? err.message : String(err),
          data: null,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [slug, panelId, recordId, reload]);
  return state;
}

function DetailPanelView({
  panel,
  app: _app,
  agent,
  slug,
  pageParams,
}: {
  panel: DetailPanel;
  app: AppSpec;
  agent: AgentSpec;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const recordId = pageParams?.id ?? pageParams?.record_id ?? null;
  const router = useRouter();
  const [reload, setReload] = useState(0);
  const { loading, error, data } = useDetailData(slug, panel.id, recordId, reload);
  // Record-level agent actions. On an embed card this is the ONLY trigger —
  // there is no queue to click, which is the whole point of DetailAction.
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [detailModal, setDetailModal] = useState<RunResult | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}>
        <div className="q-skel-card" style={{ height: 120 }} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">⚠</span>Error: {error}
      </div>
    );
  }

  const record = data?.record ?? null;
  const cols = data?.record_columns ?? [];
  const title = record ? detailTitle(record, cols) : recordId ?? "Record";
  // Surface the record's status in the header (officers expect to see "where
  // this stands" at the top, not hunt for it in the field list). A declared
  // status_field (profile layout) wins over the auto-detected column.
  const statusCol =
    panel.status_field ??
    cols.find((c) => /(^|_)(status|state|stage|disposition|verdict)$/i.test(c));
  const statusVal = statusCol && record ? record[statusCol] : null;
  const statusChipClass = (() => {
    const declared = badgeColorFor(statusVal, panel.status_colors);
    if (!declared) return badgeClass(statusVal);
    return `q-badge q-badge-${declared === "slate" ? "gray" : declared}`;
  })();
  // C6 — "profile" layout: a header card with the record's declared key facts
  // rendered large, before the sections.
  const profileFields =
    panel.layout === "profile" && record
      ? (panel.header_fields?.length
          ? panel.header_fields.filter((c) => cols.includes(c))
          : cols.filter((c) => !_HIDDEN_COL_RE.test(c)).slice(0, 4))
      : [];

  const actions = panel.actions ?? [];

  async function fireDetailAction(action: DetailAction) {
    if (!record) return;   // nothing to decide about — button is disabled anyway
    setBusyAction(action.label);
    setDetailErr(null);
    try {
      // Same contract as a queue row click: the record IS the inputs, and
      // mode="queue_action" selects plan-then-apply (the LLM proposes writes in
      // dry mode; the officer Applies to commit). The mode name is the
      // backend's existing one — it means "a button fired this", not "a queue
      // fired this", and renaming it would break every published app.
      const inputs = { ...record, ...(action.args ?? {}) };
      const res = await runtimeFetch(`/api/run/${encodeURIComponent(slug)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: action.agent_action, inputs, mode: "queue_action",
        }),
      });
      const b = await readAgentStream<Record<string, unknown>>(res);
      setDetailModal(runResultFromBody(b, {
        rowKey: String(recordId ?? panel.id),
        rowTitle: title,
        label: action.label,
        slug,
      }));
    } catch (e) {
      setDetailErr(`"${action.label}": ${(e as Error).message}`);
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="dt">
      <div className="dt-head">
        <button
          type="button"
          className="q-btn"
          onClick={() => router.back()}
          title="Back to the list"
          style={{ marginRight: 8 }}
        >
          ← Back
        </button>
        <div className="dt-title">
          {panel.icon && <Icon name={panel.icon} size={17} className="dt-title-icon" />}
          {title}
        </div>
        {statusVal != null && statusVal !== "" && (
          <span className={statusChipClass}>{formatCell(statusVal)}</span>
        )}
        {recordId && (
          <span className="chip" title="Record reference">{formatCell(recordId)}</span>
        )}
        {actions.length > 0 && (
          <div className="dt-actions" style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            {actions.map((a) => (
              <button
                key={a.label}
                type="button"
                className="q-btn q-btn-primary"
                // No record ⇒ nothing to run against. Firing anyway would send
                // an empty inputs object and the agent would answer about
                // nothing, which reads as a real recommendation.
                disabled={!record || busyAction != null}
                onClick={() => void fireDetailAction(a)}
                title={record ? undefined : "No record loaded"}
              >
                {a.icon && <Icon name={a.icon} size={15} />}
                {busyAction === a.label ? `${a.label}…` : a.label}
              </button>
            ))}
          </div>
        )}
      </div>
      {detailErr && (
        <div className="q-empty" role="alert">
          <span className="q-empty-icon">⚠</span>{detailErr}
        </div>
      )}
      {profileFields.length > 0 && record && (
        <div className="dt-profile">
          {profileFields.map((c) => (
            <div className="dt-profile-fact" key={c}>
              <div className="dt-profile-value">{formatCellNode(record[c], c)}</div>
              <div className="dt-profile-label">{prettyKey(c)}</div>
            </div>
          ))}
        </div>
      )}
      {!record && (
        <div className="q-empty">
          <span className="q-empty-icon">∅</span>
          {data?.note ?? "Record not found."}
        </div>
      )}
      {(data?.sections ?? []).map((s, i) => {
        // collapsible is authored on the spec section (not echoed by the
        // server-resolved DetailSectionData); read it by position.
        const specSec = panel.sections?.[i];
        const body = (
          <DetailSectionView
            section={s}
            record={record}
            cols={cols}
            agent={agent}
            slug={slug}
            detailPanelId={panel.id}
            recordId={recordId}
          />
        );
        const head = (
          <>
            <Icon name={specSec?.icon} size={13} className="dt-section-icon" />
            {s.title ?? prettyKey(s.type)}
          </>
        );
        if (specSec?.collapsible) {
          return (
            <details className="dt-section dt-section-collapsible" key={i} open={!specSec.collapsed}>
              <summary className="dt-section-head">{head}</summary>
              {body}
            </details>
          );
        }
        return (
          <section className="dt-section" key={i}>
            <div className="dt-section-head">{head}</div>
            {body}
          </section>
        );
      })}
      {detailModal && (
        <RunResultModal
          // Same remount rule as the queue: keyed per record so state from one
          // decision (reviewed items, overrides, the reject reason) can never
          // leak into the next.
          key={detailModal.correlationId || detailModal.rowKey}
          result={detailModal}
          itemReviewGate={_app.item_review_gate ?? "hard"}
          onClose={() => setDetailModal(null)}
          onUpdate={(next) => {
            setDetailModal(next);
            // A committed write changes the record this panel is showing —
            // re-pull so the officer is not left looking at the pre-write row.
            setReload((n) => n + 1);
          }}
        />
      )}
    </div>
  );
}

/** Best-effort human title for a record. */
function detailTitle(record: Record<string, unknown>, cols: string[]): string {
  const order = cols.length ? cols : Object.keys(record);
  const named = order.find((c) => /(name|title|subject)/i.test(c));
  if (named && record[named] != null) return formatCell(record[named]);
  const idCol = order.find((c) => /(^id$|_id$|_no$|record_id)/i.test(c));
  if (idCol && record[idCol] != null) return formatCell(record[idCol]);
  return order[0] ? formatCell(record[order[0]]) : "Record";
}

type FileValue = { url?: string; data?: string; content_type?: string; filename?: string };

/** Normalize a record column value into a file descriptor. The MCP serves a
 *  file column as a URL string or an object {url|data, content_type, filename}. */
function asFileValue(v: unknown): FileValue | null {
  if (v == null || v === "") return null;
  if (typeof v === "string") {
    // Only genuinely browser-fetchable strings become a src. A raw source-
    // storage ref (s3://, gs://, an intranet path, a DMS handle) is an OPAQUE
    // identifier — it must be streamed via the MCP (the attachment section
    // builds an /api/media URL), never used as an <img src>/href. Returning
    // null keeps such refs from rendering as a broken link.
    if (/^(https?:|data:|blob:|\/api\/media\/|\/)/i.test(v))
      return { url: v };
    return null;
  }
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    const str = (x: unknown) => (typeof x === "string" ? x : undefined);
    return {
      url: str(o.url),
      data: str(o.data),
      content_type: str(o.content_type) ?? str(o.mime),
      filename: str(o.filename) ?? str(o.name),
    };
  }
  return null;
}

function fileSrc(f: FileValue): string {
  if (f.url) return f.url;
  if (f.data) {
    return f.data.startsWith("data:")
      ? f.data
      : `data:${f.content_type ?? "application/octet-stream"};base64,${f.data}`;
  }
  return "";
}

function fileIsImage(f: FileValue): boolean {
  if (f.content_type?.startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i.test(f.url ?? f.filename ?? "");
}
function fileIsPdf(f: FileValue): boolean {
  if (f.content_type === "application/pdf") return true;
  return /\.pdf(\?|$)/i.test(f.url ?? f.filename ?? "");
}
function fileIsVideo(f: FileValue): boolean {
  if (f.content_type?.startsWith("video/")) return true;
  return /\.(mp4|webm|mov|m4v|ogv|mkv)(\?|$)/i.test(f.url ?? f.filename ?? "");
}
function fileIsAudio(f: FileValue): boolean {
  if (f.content_type?.startsWith("audio/")) return true;
  return /\.(mp3|wav|ogg|m4a|aac|flac|weba)(\?|$)/i.test(f.url ?? f.filename ?? "");
}

const _MEDIA_CT: Record<string, string> = {
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", gif: "image/gif",
  webp: "image/webp", bmp: "image/bmp", tif: "image/tiff", tiff: "image/tiff",
  pdf: "application/pdf", mp4: "video/mp4", webm: "video/webm", mp3: "audio/mpeg",
};
/** Best-effort content-type + filename from a raw media ref (an s3:// path or
 *  any string ending in a file name). Used ONLY to pick the right renderer
 *  (image vs pdf) — the ref itself is never fetched by the browser. */
function guessMediaMeta(raw: unknown): { content_type?: string; filename?: string } {
  if (typeof raw !== "string" || !raw) return {};
  const clean = raw.split("?")[0].split("#")[0];
  const base = decodeURIComponent(clean.split("/").pop() || "");
  const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";
  return { content_type: _MEDIA_CT[ext], filename: base || undefined };
}
/** Same-origin URL that streams a SoR-record media column THROUGH the dept-MCP.
 *  The column value is an opaque identifier — we send (key_field, key, col) and
 *  the MCP resolves the ref + streams the bytes. The browser never touches S3 /
 *  the source storage. */
function mcpMediaSrc(
  slug: string, dsId: string, keyField: string, keyVal: string, col: string,
): string {
  const q = new URLSearchParams({ key_field: keyField, key: keyVal, col });
  return `/api/media/${encodeURIComponent(slug)}/${encodeURIComponent(dsId)}?${q.toString()}`;
}

/** Render a file column value: image preview / open-document / download. */
function FileView({ value, label }: { value: unknown; label?: string }) {
  const f = asFileValue(value);
  const src = f ? fileSrc(f) : "";
  if (!f || !src) return <span className="cf-desc">—</span>;
  const name = f.filename ?? label ?? "file";
  return (
    <div className="dt-attachment" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      {fileIsImage(f) ? (
        <a href={src} target="_blank" rel="noopener noreferrer">
          <img
            src={src}
            alt={name}
            style={{ maxWidth: 220, maxHeight: 220, borderRadius: 6, display: "block" }}
          />
        </a>
      ) : fileIsPdf(f) ? (
        <a href={src} target="_blank" rel="noopener noreferrer" className="q-btn">📄 Open document</a>
      ) : fileIsVideo(f) ? (
        <video
          src={src}
          controls
          preload="metadata"
          style={{ maxWidth: 360, maxHeight: 240, borderRadius: 6, display: "block" }}
        />
      ) : fileIsAudio(f) ? (
        <audio src={src} controls preload="metadata" style={{ display: "block" }} />
      ) : null}
      <a href={src} download={name} className="q-btn">⬇ Download{f.filename ? ` ${f.filename}` : ""}</a>
    </div>
  );
}

function DetailSectionView({
  section,
  record,
  cols,
  agent,
  slug,
  detailPanelId,
  recordId,
}: {
  section: DetailSectionData;
  record: Record<string, unknown> | null;
  cols: string[];
  agent: AgentSpec;
  slug: string;
  detailPanelId: string;
  recordId: string | null;
}) {
  switch (section.type) {
    case "fields": {
      if (!record) return <div className="panel-empty">No record selected.</div>;
      const show = section.fields?.length ? section.fields : cols;
      return (
        <dl className="dt-fields">
          {show.map((f) => (
            <div className="dt-field" key={f}>
              <dt>{prettyKey(f)}</dt>
              <dd>{formatCellNode(record[f], f)}</dd>
            </div>
          ))}
        </dl>
      );
    }
    case "attachment": {
      if (!record) return <div className="panel-empty">No record selected.</div>;
      // Only columns the author DECLARED as media get streamed. Without a declared
      // list we fall back to showing all columns as plain values — we never turn
      // arbitrary columns into media requests.
      const mediaFields = section.fields ?? [];
      const show = mediaFields.length ? mediaFields : cols;
      const dsId = section.data_source;
      // The server hands us the AUTHORITATIVE key it matched the record on. We
      // never guess it — a wrong key (e.g. picking a foreign key like asset_id)
      // would re-read a DIFFERENT record's media.
      const keyField = section.key_field;
      const keyVal =
        keyField && record[keyField] != null ? String(record[keyField]) : null;
      return (
        <dl className="dt-fields">
          {show.map((f) => {
            const raw = record[f];
            // A value that is ALREADY browser-fetchable (http(s)/data/blob or a
            // same-origin path) renders directly. An OPAQUE source ref (s3://, a
            // bare storage key, a DMS handle) on a DECLARED media field is streamed
            // through the MCP by the server-supplied key — the browser never sees
            // the storage URL.
            const isOpaqueRef =
              typeof raw === "string" &&
              raw.trim() !== "" &&
              !/^(https?:|data:|blob:|\/)/i.test(raw);
            let node: ReactNode;
            if (mediaFields.length && isOpaqueRef) {
              if (dsId && keyField && keyVal) {
                node = (
                  <FileView
                    value={{ url: mcpMediaSrc(slug, dsId, keyField, keyVal, f), ...guessMediaMeta(raw) }}
                    label={f}
                  />
                );
              } else {
                // Fail loud rather than guess a key or emit a broken URL.
                node = (
                  <div className="panel-empty">
                    Media unavailable — the section is missing a data_source or the
                    record key for “{prettyKey(f)}”.
                  </div>
                );
              }
            } else {
              node = <FileView value={record[f]} label={f} />;
            }
            return (
              <div className="dt-field" key={f}>
                <dt>{prettyKey(f)}</dt>
                <dd>{node}</dd>
              </div>
            );
          })}
        </dl>
      );
    }
    case "markdown":
      return section.content ? (
        <Markdown content={section.content} />
      ) : (
        <div className="panel-empty">—</div>
      );
    case "documents":
      // data_source is now threaded through DetailSectionData, so detail-section
      // documents can sign an "Open original" URL too (parity with the
      // standalone document_view panel).
      return (
        <DocList
          docs={section.documents ?? []}
          note={section.note}
          slug={slug}
          dataSourceId={section.data_source}
        />
      );
    case "agent_timeline":
      return <RunTimeline runs={section.runs ?? []} />;
    case "approval":
      return (
        <ApprovalSection
          pending={section.pending ?? []}
          roles={section.roles ?? []}
          slug={slug}
        />
      );
    case "agent_chat":
      return (
        <AgentChatPanelView
          panel={{
            type: "agent_chat",
            id: `${detailPanelId}-chat`,
            agent_role: section.agent_role,
            starter_prompts: [],
          }}
          agent={agent}
          slug={slug}
        />
      );
    case "comments":
      return (
        <CommentsSection
          initial={section.comments ?? []}
          slug={slug}
          recordId={recordId}
        />
      );
    default:
      return <div className="panel-empty">Unsupported section.</div>;
  }
}

/** comments — human notes threaded to a record. Lists existing notes and lets
 *  the officer add one. App-local overlay write only — never the SoR. */
function CommentsSection({
  initial,
  slug,
  recordId,
}: {
  initial: RecordComment[];
  slug: string;
  recordId: string | null;
}) {
  const [comments, setComments] = useState<RecordComment[]>(initial);
  const [draft, setDraft] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!recordId) return;
    try {
      const res = await runtimeFetch(
        `/api/apps/${encodeURIComponent(slug)}/records/${encodeURIComponent(recordId)}/comments`,
      );
      const b = (await res.json().catch(() => ({}))) as { comments?: RecordComment[] };
      setComments(b.comments ?? []);
    } catch {
      // A refresh failure isn't fatal — keep showing the current list.
    }
  }

  async function add() {
    const text = draft.trim();
    if (!text || !recordId) return;
    setPosting(true);
    setError(null);
    try {
      const res = await runtimeFetch(
        `/api/apps/${encodeURIComponent(slug)}/records/${encodeURIComponent(recordId)}/comments`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        },
      );
      if (!res.ok) {
        const b = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(b.detail || `HTTP ${res.status}`);
      }
      setDraft("");
      await refresh();
    } catch (e) {
      // RULE #1: a failed write must be visible, not swallowed into "nothing happened".
      setError(e instanceof Error ? e.message : "could not add note");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="dt-comments">
      {comments.length === 0 ? (
        <div className="panel-empty">No notes yet.</div>
      ) : (
        <ol className="dt-comment-list">
          {comments.map((c, i) => (
            <li key={c.id ?? i} className="dt-comment">
              <div className="dt-comment-text">{c.text}</div>
              <div className="dt-comment-meta">
                {c.author ?? "—"}
                {c.created_at ? ` · ${new Date(c.created_at).toLocaleString()}` : ""}
              </div>
            </li>
          ))}
        </ol>
      )}
      {recordId ? (
        <div className="dt-comment-add">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add a note…"
            rows={2}
            disabled={posting}
          />
          <div className="dt-comment-actions">
            <button type="button" className="q-btn q-btn-primary" onClick={add} disabled={posting || !draft.trim()}>
              {posting ? "Adding…" : "Add note"}
            </button>
            {error && (
              <span className="q-empty" style={{ fontSize: 12 }}>
                <span className="q-empty-icon">⚠</span>{error}
              </span>
            )}
          </div>
        </div>
      ) : (
        <div className="panel-empty">Open a record to add notes.</div>
      )}
    </div>
  );
}

/** notifications — a notification centre: pending approvals the user can act
 *  on + SLA-breached (overdue) records. Read-only; a click routes via the
 *  panel's `navigate` target, templated with the item's row ({row.id} etc.). */
function NotificationsPanelView({
  panel,
  app,
  slug,
}: {
  panel: NotificationsPanel;
  app: AppSpec;
  slug: string;
}) {
  const router = useRouter();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await runtimeFetch(
          `/api/apps/${encodeURIComponent(slug)}/notifications/${encodeURIComponent(panel.id)}`,
        );
        const b = (await res.json().catch(() => ({}))) as {
          notifications?: NotificationItem[];
          error?: string | null;
          detail?: string;
        };
        if (!res.ok) throw new Error(b.detail || `HTTP ${res.status}`);
        if (!cancelled) {
          setItems(b.notifications ?? []);
          setError(b.error ?? null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "could not load notifications");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug, panel.id]);

  function open(item: NotificationItem) {
    if (!item.navigate) return;
    doNavigate(router, app, slug, item.navigate, {
      row: item.row ?? (item.id ? { id: item.id } : {}),
    });
  }

  if (loading) {
    return (
      <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}>
        <div className="q-skel-card" style={{ height: 64 }} />
      </div>
    );
  }

  return (
    <div className="nc">
      {error && (
        <div className="q-empty" style={{ fontSize: 12 }}>
          <span className="q-empty-icon">⚠</span>{error}
        </div>
      )}
      {items.length === 0 && !error ? (
        <div className="q-empty">
          <span className="q-empty-icon">✓</span>Nothing needs your attention.
        </div>
      ) : (
        <ol className="nc-list">
          {items.map((it, i) => {
            const itemClickable = !!it.navigate;
            return (
              <li
                key={it.correlation_id ?? it.id ?? i}
                className={`nc-item${itemClickable ? " is-clickable" : ""}`}
                onClick={itemClickable ? () => open(it) : undefined}
              >
                <span className={toneBadge(it.tone)} style={{ fontSize: 11 }}>
                  {it.label ?? (it.type === "approval" ? "Approval" : "Item")}
                </span>
                <div className="nc-item-body">
                  <div className="nc-item-title">{it.title}</div>
                  {it.sub && <div className="nc-item-sub">{it.sub}</div>}
                </div>
                {it.created_at && (
                  <span className="nc-item-time">{new Date(it.created_at).toLocaleDateString()}</span>
                )}
                {itemClickable && <span className="nc-item-arrow" aria-hidden="true">→</span>}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/** Map a NotificationFeed tone to a status-badge colour class (same q-badge
 *  palette used across queues/detail), so feed badges are visually consistent. */
function toneBadge(tone?: string): string {
  const c =
    ({ success: "green", danger: "red", warning: "amber", info: "blue", neutral: "gray" } as Record<
      string,
      string
    >)[tone ?? "neutral"] ?? "gray";
  return `q-badge q-badge-${c}`;
}

/** agent_timeline — the audit trail of agent runs. */
function RunTimeline({ runs }: { runs: DetailRun[] }) {
  if (!runs.length) {
    return <div className="panel-empty">No agent runs recorded yet.</div>;
  }
  return (
    <ol className="dt-timeline">
      {runs.map((r) => (
        <li className="dt-run" key={r.correlation_id}>
          <span className={`dt-run-dot dt-dot-${statusTone(r.decision ?? r.status)}`} />
          <div className="dt-run-body">
            <div className="dt-run-top">
              <span className="dt-run-action">{prettyKey(r.action ?? "run")}</span>
              {(r.decision || r.status) && (
                <span className={badgeClass(r.decision ?? r.status)}>
                  {formatCell(r.decision ?? r.status)}
                </span>
              )}
              <span className="dt-run-time">
                {r.created_at ? new Date(r.created_at).toLocaleString() : ""}
              </span>
            </div>
            {r.reasoning && <div className="dt-run-reason">{r.reasoning}</div>}
            <div className="dt-run-meta">
              {r.model && <span>{r.model}</span>}
              {typeof r.duration_ms === "number" && (
                <span>{(r.duration_ms / 1000).toFixed(1)}s</span>
              )}
              <span className="dt-run-cid">{r.correlation_id}</span>
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Generic key-pickers for a document row of unknown shape. */
function docField(row: Record<string, unknown>, keys: string[]): string {
  for (const k of keys) {
    const v = row[k];
    if (v != null && v !== "") return formatCell(v);
  }
  return "";
}

const _DOC_TITLE_KEYS = ["title", "doc_title", "name", "filename", "source", "id"];
const _DOC_BODY_KEYS = ["content", "text", "body", "markdown", "full_text", "chunk"];
const _DOC_SUMMARY_KEYS = ["summary", "snippet", "description", "abstract"];
const _DOC_TYPE_KEYS = ["doc_type", "type", "category"];
const _DOC_URL_KEYS = ["url", "uri", "link", "href"];

function DocList({
  docs,
  note,
  slug,
  dataSourceId,
}: {
  docs: Record<string, unknown>[];
  note?: string;
  /** Slug + ds_id are passed in so the DocViewModal can call
   *  /api/document/{slug}/{ds_id} to sign a URL back to the source
   *  artifact for that document. When either is missing the "Open
   *  source" button is hidden (e.g. for static document lists where
   *  the body is already inline). */
  slug?: string;
  dataSourceId?: string;
}) {
  const [viewing, setViewing] = useState<Record<string, unknown> | null>(null);

  if (!docs.length) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">ℹ</span>
        {note ?? "No documents found."}
      </div>
    );
  }
  return (
    <>
      <div className="dt-docs">
        {docs.map((d, i) => {
          const title = docField(d, _DOC_TITLE_KEYS) || `Document ${i + 1}`;
          const snippet = docField(d, [..._DOC_SUMMARY_KEYS, ..._DOC_BODY_KEYS]);
          const dtype = docField(d, _DOC_TYPE_KEYS);
          return (
            <button
              type="button"
              className="dt-doc dt-doc-clickable"
              key={i}
              onClick={() => setViewing(d)}
            >
              <div className="dt-doc-top">
                <span className="dt-doc-icon">▤</span>
                <span className="dt-doc-title">{title}</span>
                {dtype && <span className="q-badge q-badge-gray">{dtype}</span>}
                <span className="dt-doc-open">Open ›</span>
              </div>
              {snippet && <div className="dt-doc-snippet">{snippet}</div>}
            </button>
          );
        })}
      </div>
      {viewing && (
        <DocViewModal
          doc={viewing}
          slug={slug}
          dataSourceId={dataSourceId}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

/** Full-document viewer — opens when a DocList row is clicked. Renders the
 *  document body (Markdown) when the row carries one, else the summary,
 *  plus any extra metadata fields the row holds.
 *
 *  Also surfaces an "Open original" button when the row carries a
 *  `metadata.doc_path` AND the panel passed down a `slug` + `dataSourceId`.
 *  That button requests a short-lived signed URL from smart-app-service
 *  (→ dept-MCP → object store) and opens it in a new tab. The chunk text
 *  shown in the modal stays as a quick preview; the real artifact lives
 *  one click away. */
function DocViewModal({
  doc,
  slug,
  dataSourceId,
  onClose,
}: {
  doc: Record<string, unknown>;
  slug?: string;
  dataSourceId?: string;
  onClose: () => void;
}) {
  const title = docField(doc, _DOC_TITLE_KEYS) || "Document";
  const dtype = docField(doc, _DOC_TYPE_KEYS);
  const body = docField(doc, _DOC_BODY_KEYS);
  const summary = docField(doc, _DOC_SUMMARY_KEYS);
  const url = docField(doc, _DOC_URL_KEYS);
  const rendered = new Set([
    ..._DOC_TITLE_KEYS,
    ..._DOC_BODY_KEYS,
    ..._DOC_SUMMARY_KEYS,
    ..._DOC_TYPE_KEYS,
    ..._DOC_URL_KEYS,
  ]);
  const meta = Object.entries(doc).filter(
    ([k, v]) => !rendered.has(k) && v != null && v !== "",
  );

  // Pull a doc_path off the chunk metadata if the MCP attached one. The
  // shape can be either:
  //   { metadata: { doc_path: "policy/..." } }       (post-fix MCP)
  //   { doc_path: "policy/..." }                     (flattened by panel)
  let docPath: string | null = null;
  const metaField = (doc as Record<string, unknown>).metadata;
  if (metaField && typeof metaField === "object") {
    const dp = (metaField as Record<string, unknown>).doc_path;
    if (typeof dp === "string" && dp.trim()) docPath = dp.trim();
  }
  if (!docPath) {
    const dp = (doc as Record<string, unknown>).doc_path;
    if (typeof dp === "string" && dp.trim()) docPath = dp.trim();
  }
  const canSign = Boolean(docPath && slug && dataSourceId);

  const [signing, setSigning] = useState(false);
  const [signErr, setSignErr] = useState<string | null>(null);
  // Holds the FULL document fetched from the signed URL. Replaces the
  // chunk preview rendered above once the user clicks "Open full document".
  const [fullDoc, setFullDoc] = useState<{ kind: "markdown" | "text"; content: string } | null>(null);
  // Cached signed URL — kept around so the "Open in new tab" fallback
  // can reuse it without burning a second presign.
  const [signedUrl, setSignedUrl] = useState<string | null>(null);

  const isLikelyMarkdown = (() => {
    if (!docPath) return false;
    const lower = docPath.toLowerCase();
    return lower.endsWith(".md") || lower.endsWith(".markdown") || lower.endsWith(".txt");
  })();

  async function fetchSignedUrl(): Promise<string | null> {
    if (!canSign || !slug || !dataSourceId || !docPath) return null;
    if (signedUrl) return signedUrl;
    // runtimeFetch carries the per-tab user JWT → X-User-JWT at the dept-MCP.
    const res = await runtimeFetch(
      `/api/document/${encodeURIComponent(slug)}/${encodeURIComponent(dataSourceId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_path: docPath }),
      },
    );
    const body = await res.json().catch(() => ({} as Record<string, unknown>));
    if (!res.ok) {
      throw new Error(String(body.detail ?? body.error ?? `HTTP ${res.status}`));
    }
    const u = typeof body.url === "string" && body.url ? body.url : null;
    if (!u) throw new Error("server returned no url");
    setSignedUrl(u);
    return u;
  }

  /** Fetch the signed URL, then GET the artifact directly from S3 and
   *  render its text inline. The bucket has a GET/HEAD CORS policy so
   *  the cross-origin fetch is allowed. Works for markdown + plain-text;
   *  binary formats fall through to "Open in new tab". */
  const openInline = async () => {
    setSigning(true);
    setSignErr(null);
    try {
      const u = await fetchSignedUrl();
      if (!u) return;
      const r = await fetch(u);
      if (!r.ok) throw new Error(`HTTP ${r.status} fetching artifact`);
      const text = await r.text();
      setFullDoc({ kind: isLikelyMarkdown ? "markdown" : "text", content: text });
    } catch (err) {
      setSignErr(err instanceof Error ? err.message : String(err));
    } finally {
      setSigning(false);
    }
  };

  /** Fallback for non-text artifacts (PDF / images / etc) — sign and
   *  open in a new tab so the browser's native viewer handles it. */
  const openInTab = async () => {
    setSigning(true);
    setSignErr(null);
    try {
      const u = await fetchSignedUrl();
      if (!u) return;
      window.open(u, "_blank", "noopener,noreferrer");
    } catch (err) {
      setSignErr(err instanceof Error ? err.message : String(err));
    } finally {
      setSigning(false);
    }
  };

  return (
    <ModalPortal>
    <div className="rr-overlay" onClick={onClose}>
      <div className="rr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="rr-head">
          <div>
            <div className="rr-title">{title}</div>
            {dtype && <div className="rr-sub">{prettyKey(dtype)}</div>}
          </div>
          {/* The glyph is the whole label, so without aria-label a screen
              reader announces "multiplication sign, button" on the only way
              out of a modal that has just proposed a write to a credit file. */}
          <button type="button" className="rr-close" aria-label="Close"
                  title="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="rr-body">
          {meta.length > 0 && (
            <dl className="dt-fields">
              {meta.map(([k, v]) => (
                <div className="dt-field" key={k}>
                  <dt>{prettyKey(k)}</dt>
                  <dd>{formatCellNode(v, k)}</dd>
                </div>
              ))}
            </dl>
          )}
          {fullDoc ? (
            // Full artifact mode — replaces the chunk preview entirely.
            // For markdown the existing renderer handles headings/lists/
            // code; for plain text we wrap in <pre> to preserve whitespace.
            fullDoc.kind === "markdown" ? (
              <Markdown content={fullDoc.content} />
            ) : (
              <pre className="md-p" style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>
                {fullDoc.content}
              </pre>
            )
          ) : body ? (
            <Markdown content={body} />
          ) : summary ? (
            <p className="md-p">{summary}</p>
          ) : (
            <div className="panel-empty">
              This document has no preview content — only its catalogue
              entry is available.
            </div>
          )}
          {url && (
            <a href={url} target="_blank" rel="noreferrer noopener">
              Open original document ›
            </a>
          )}
          {canSign && (
            <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
              {!fullDoc && isLikelyMarkdown && (
                <button
                  type="button"
                  className="q-btn q-btn-primary"
                  onClick={openInline}
                  disabled={signing}
                >
                  {signing ? "Loading…" : "Open full document"}
                </button>
              )}
              <button
                type="button"
                className="q-btn"
                onClick={openInTab}
                disabled={signing}
              >
                {signing && !isLikelyMarkdown ? "Signing…" : "Open in new tab ↗"}
              </button>
              {signErr && (
                <div style={{ color: "#dc2626", marginTop: 6, fontSize: 12, width: "100%" }}>
                  Could not sign URL: {signErr}
                </div>
              )}
              {docPath && (
                <div style={{ color: "#6b7280", marginTop: 4, fontSize: 11, width: "100%" }}>
                  Source: {docPath}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}

/** approval — list pending runs with Approve / Reject controls. */
function ApprovalSection({
  pending,
  roles,
  slug,
}: {
  pending: DetailPendingRun[];
  roles: string[];
  slug: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<FormMsg>(null);

  async function resolve(cid: string, decision: "approve" | "reject" | "cancel") {
    setBusy(cid);
    setMsg(null);
    try {
      // runtimeFetch carries the per-tab user JWT so smart-app-service can
      // attribute the approver to a real user (and enforce the non-self-
      // approval gate); otherwise the service-token caller is both requester
      // and approver and the backend rejects "cannot approve your own request".
      const item = pending.find((p) => p.correlation_id === cid);
      const res = await runtimeFetch(`/api/apps/${encodeURIComponent(slug)}/approve/${encodeURIComponent(cid)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          decision === "approve"
            ? { decision, expected_plan_hash: item?.plan_hash }
            : { decision },
        ),
      });
      const body = await res.json().catch(() => ({} as Record<string, unknown>));
      const label =
        decision === "approve" ? "Approved" : decision === "cancel" ? "Cancelled" : "Rejected";
      if (!res.ok) {
        setMsg({ kind: "err", text: `${body.detail ?? body.error ?? `HTTP ${res.status}`}` });
      } else {
        setDone((d) => ({ ...d, [cid]: label }));
        setMsg({ kind: "ok", text: `Run ${label.toLowerCase()}.` });
      }
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="dt-approvals">
      {roles.length > 0 && (
        <div className="dt-approval-roles">
          Approver roles:{" "}
          {roles.map((r) => (
            <span key={r} className="chip">
              {r}
            </span>
          ))}
        </div>
      )}
      {pending.length === 0 && (
        <div className="q-empty">
          <span className="q-empty-icon">✓</span>Nothing awaiting approval.
        </div>
      )}
      {pending.map((p) => (
        <div className="dt-approval" key={p.correlation_id}>
          <div className="dt-approval-info">
            <span className="dt-run-action">{prettyKey(p.action ?? "run")}</span>
            <span className="dt-run-cid">{p.correlation_id}</span>
            {p.requested_by && (
              <span className="dt-run-time">by {p.requested_by}</span>
            )}
          </div>
          {p.decision && (
            <div style={{ marginTop: 6, fontSize: 13 }}>
              <span style={{ color: "#6b7280", marginRight: 6 }}>
                Agent says:
              </span>
              {/* Neutral grey badge — green/red would prematurely
                  signal acceptance/rejection before the approver has
                  reviewed the proposed changes. */}
              <span
                className="q-badge"
                style={{
                  marginRight: 8,
                  background: "#f3f4f6",
                  color: "#111827",
                  border: "1px solid #e5e7eb",
                }}
              >
                {p.decision ? normalizeDecision(p.decision) : "—"}
              </span>
            </div>
          )}
          {p.reasoning && (
            <div style={{ marginTop: 6, fontSize: 12.5, color: "#374151" }}>
              <Markdown content={p.reasoning} />
            </div>
          )}
          {p.planned_writes && p.planned_writes.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 12.5, color: "#374151", marginBottom: 4 }}>
                Proposed changes ({p.planned_writes.length}):
              </div>
              {p.planned_writes.map((pw, i) => (
                <div
                  key={i}
                  style={{
                    border: "1px solid var(--citra-border, #e5e7eb)",
                    borderRadius: 6,
                    padding: 8,
                    marginTop: 4,
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>
                    {prettyKey(pw.action_id ?? "action")}
                  </div>
                  {pw.payload && Object.keys(pw.payload).length > 0 && (
                    <dl className="dt-fields" style={{ marginTop: 4 }}>
                      {Object.entries(pw.payload).map(([k, v]) => (
                        <div className="dt-field" key={k}>
                          <dt>{prettyKey(k)}</dt>
                          <dd>{formatCellNode(v, k)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              ))}
            </div>
          )}
          {done[p.correlation_id] ? (
            <span
              className={`q-badge ${
                done[p.correlation_id] === "Approved"
                  ? "q-badge-green"
                  : done[p.correlation_id] === "Cancelled"
                  ? "q-badge"
                  : "q-badge-red"
              }`}
            >
              {done[p.correlation_id]}
            </span>
          ) : (
            <div className="dt-approval-actions" style={{ marginTop: 10 }}>
              <button
                type="button"
                className="q-btn q-btn-primary"
                disabled={busy !== null}
                onClick={() => resolve(p.correlation_id, "approve")}
              >
                {busy === p.correlation_id ? (
                  <><span className="q-spin" /> Apply</>
                ) : (
                  p.planned_writes && p.planned_writes.length > 0 ? "Apply" : "Approve"
                )}
              </button>
              <button
                type="button"
                className="q-btn"
                disabled={busy !== null}
                onClick={() => resolve(p.correlation_id, "reject")}
              >
                Reject
              </button>
              {/* Cancel = dismiss/withdraw (terminal, no write) — distinct from
                  Reject ("recommendation is wrong"). Both commit nothing. */}
              <button
                type="button"
                className="q-btn"
                disabled={busy !== null}
                onClick={() => resolve(p.correlation_id, "cancel")}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      ))}
      {msg && <span className={`cf-msg cf-msg-${msg.kind}`}>{msg.text}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function DashboardPanelView({
  panel,
  slug,
  pageParams
}: {
  panel: DashboardPanel;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const enabled = panel.metrics.some((m) => !!m.data_source);
  const { data, loading, error } = usePanelData(slug, panel.id, enabled, 0, pageParams);

  // Prefer source-computed aggregates (true COUNT/SUM over the whole table).
  // Fall back to client-side aggregation of rows only when the server didn't
  // return metrics (non-SQL source / older backend).
  const serverMetrics = data?.metrics;

  return (
    <div className="kpi-row">
      {panel.metrics.map((m, i) => {
        const accent = EXEC_PALETTE[i % EXEC_PALETTE.length];
        const sm = serverMetrics?.find((s) => s.name === m.name);
        const kpi = sm
          ? kpiFromServer(sm, m)
          : computeMetric(m, data?.rows ?? []);
        // Clean subtitle: prefer the declared label; never leak the raw field id.
        const subtitle =
          (sm?.label ?? m.label) ||
          `${m.agg}${m.window ? ` · ${m.window}` : ""}`;
        return (
          <div className="kpi" key={m.name} style={{ borderTopColor: accent }}>
            <div className="kpi-head">
              <div className="kpi-name">
                <Icon name={m.icon ?? autoMetricIcon(m)} size={13} className="kpi-icon" />
                {prettyKey(m.name)}
              </div>
              {!loading && !error && kpi.delta && (
                <span className={`kpi-delta kpi-delta-${kpi.delta.dir}`}>
                  <span className="kpi-delta-arrow">
                    {kpi.delta.dir === "up"
                      ? "▲"
                      : kpi.delta.dir === "down"
                      ? "▼"
                      : "▬"}
                  </span>
                  {kpi.delta.text}
                </span>
              )}
            </div>
            <div className="kpi-value">
              {loading ? (
                <span className="kpi-skel" />
              ) : error ? (
                "—"
              ) : (sm as { error?: string } | undefined)?.error ? (
                // M2: a FAILED metric query shows a distinct error affordance —
                // never the same "—" that reads as a genuine zero.
                <span className="kpi-err" title={(sm as { error?: string }).error} style={{ color: "#b91c1c" }}>
                  ⚠ unavailable
                </span>
              ) : (
                kpi.display
              )}
            </div>
            {!loading && !error && kpi.spark && kpi.spark.length > 2 && (
              <KpiSparkline
                points={kpi.spark}
                color={accent}
                labels={kpi.sparkLabels}
              />
            )}
            {!loading && !error && m.target != null && m.target > 0 && typeof kpi.value === "number" && (
              <KpiProgress value={kpi.value} target={m.target} thresholds={m.thresholds} />
            )}
            <div className="kpi-sub">{subtitle}</div>
          </div>
        );
      })}
    </div>
  );
}

// KpiSparkline/KpiProgress moved to ./KpiSparkline; kpiFromServer/
// computeMetric/looksMonetary/KpiResult moved to @/lib/kpi (shared with
// the stat_strip + hero panels).

// ---------------------------------------------------------------------------
// Chart
// ---------------------------------------------------------------------------

function ChartPanelView({ panel, slug, pageParams }: { panel: ChartPanel; slug: string; pageParams: Record<string, string> }) {
  const { data, loading, error } = usePanelData(slug, panel.id, true, 0, pageParams);

  if (loading) {
    return (
      <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}>
        <div className="q-skel-card" style={{ height: 240 }} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">⚠</span>Error: {error}
      </div>
    );
  }
  const rows = data?.rows ?? [];
  // A note alongside rows (e.g. the truncation note) must NOT replace the
  // chart — render the chart and show the note as a caption beneath it. Only
  // treat a note as an empty-state when there are genuinely no rows.
  if (data?.note && !rows.length) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">ℹ</span>
        {data.note}
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">∅</span>No data to chart.
      </div>
    );
  }

  const height = 280;

  // ONE renderer: the panel.x / panel.y / group_by / stacked contract is the
  // same SPEC the in-chat chart blocks carry. All styling lives in the
  // "citra-exec" echarts theme + chartToEchartsOption.
  const option = chartToEchartsOption(
    {
      chart_type: panel.chart_type,
      x: panel.x,
      y: panel.y,
      group_by: panel.group_by,
      stacked: panel.stacked
    },
    rows as Record<string, unknown>[]
  );

  return (
    <>
      <ReactECharts
        option={option}
        theme={EXEC_THEME_NAME}
        notMerge
        style={{ height, width: "100%" }}
        opts={{ renderer: "svg" }}
      />
      {data?.note && (
        <div className="q-trunc-note" style={{ fontSize: 11.5, color: "var(--citra-muted)", marginTop: 4 }}>
          {data.note}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Agent chat
// ---------------------------------------------------------------------------

type ChatMsg = { role: "user" | "assistant"; content: string; error?: boolean };

function AgentChatPanelView({
  panel,
  agent,
  slug,
}: {
  panel: AgentChatPanel;
  agent: AgentSpec;
  slug: string;
}) {
  const role =
    panel.agent_role ??
    agent.sub_agents?.find((s) => s.id === panel.agent_role)?.role ??
    agent.name;
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || sending) return;
    const next: ChatMsg[] = [...messages, { role: "user", content: msg }];
    setMessages(next);
    setInput("");
    setSending(true);
    try {
      // runtimeFetch carries the end-user JWT → X-User-JWT at the dept-MCP
      // when the agent runs its query tools mid-conversation.
      const res = await runtimeFetch(`/api/chat/${encodeURIComponent(slug)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next }),
      });
      // Keepalive-streamed turn (no 504 on long turns); the terminal `done`
      // event carries the same {reply} shape. Errors throw → outer catch.
      const body = await readAgentStream<Record<string, unknown>>(res);
      const reply = String(body.reply ?? "(no response)");
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (e) {
      // Mark as an error turn so it renders distinctly — a transient failure
      // must NOT masquerade as a grounded assistant answer.
      setMessages((m) => [
        ...m,
        { role: "assistant", error: true, content: `Couldn't get a response: ${(e as Error).message}` },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat">
      <div className="chat-head">
        <span className="chat-avatar">✦</span>
        <div>
          <div className="chat-role">{role}</div>
          <div className="chat-sub">AI assistant · grounded in this app&apos;s data</div>
        </div>
      </div>
      <div className="chat-log" ref={logRef}>
        {messages.length === 0 && (
          <div className="chat-welcome">
            <p>Ask a question about this app&apos;s data — answers are grounded in the connected sources.</p>
            {(panel.starter_prompts ?? []).length > 0 && (
              <div className="chat-starters">
                {(panel.starter_prompts ?? []).map((p) => (
                  <button
                    key={p}
                    type="button"
                    className="chat-starter"
                    onClick={() => send(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <div
              className="chat-bubble"
              style={
                m.error
                  ? { background: "#fef2f2", border: "1px solid #fecaca", color: "#991b1b" }
                  : undefined
              }
            >
              {m.role === "assistant" && !m.error ? (
                <Markdown content={m.content} className="md-chat" />
              ) : m.error ? (
                <span>⚠ {m.content}</span>
              ) : (
                m.content
              )}
            </div>
            {/* Don't offer "copy" on an error turn — it isn't an answer. */}
            {m.role === "assistant" && !m.error && (
              <button
                type="button"
                className="chat-copy"
                title="Copy"
                onClick={() => navigator.clipboard?.writeText(m.content)}
              >
                ⧉
              </button>
            )}
          </div>
        ))}
        {sending && (
          <div className="chat-msg chat-msg-assistant">
            <div className="chat-bubble chat-typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Message ${role}…`}
          disabled={sending}
        />
        <button
          type="submit"
          className="q-btn q-btn-primary"
          disabled={sending || !input.trim()}
        >
          {sending ? <span className="q-spin" /> : "Send"}
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Document view
// ---------------------------------------------------------------------------

function DocumentViewPanelView({
  panel,
  slug,
}: {
  panel: DocumentViewPanel;
  slug: string;
}) {
  const { loading, error, data } = usePanelData(slug, panel.id, true);
  const allDocs = useMemo(() => data?.rows ?? [], [data]);
  const [search, setSearch] = useState("");

  const docs = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allDocs;
    return allDocs.filter((d) =>
      Object.values(d).some((v) => String(v ?? "").toLowerCase().includes(q)),
    );
  }, [allDocs, search]);

  if (loading) {
    return (
      <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}>
        <div className="q-skel-card" style={{ height: 90 }} />
        <div className="q-skel-card" style={{ height: 90 }} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">⚠</span>Error: {error}
      </div>
    );
  }

  return (
    <div className="q-wrap">
      <div className="q-toolbar">
        <div className="q-search">
          <input
            placeholder="Search documents…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {(panel.doc_types ?? []).map((dt) => (
          <span key={dt} className="chip">
            {dt}
          </span>
        ))}
        <div className="q-spacer" />
        <span className="q-count">
          <b>{docs.length}</b> {docs.length === 1 ? "document" : "documents"}
        </span>
      </div>
      <DocList
        docs={docs}
        note={data?.note}
        slug={slug}
        dataSourceId={panel.data_source}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Markdown
// ---------------------------------------------------------------------------

function MarkdownPanelView({ panel }: { panel: MarkdownPanel }) {
  return <Markdown content={panel.content} />;
}

/** Static callout band — info / warn / error / success. No data binding;
 *  content is rendered through the safe Markdown component (no raw HTML). */
function NoticePanelView({ panel }: { panel: NoticePanel }) {
  const tone = panel.tone ?? "info";
  const icon =
    tone === "error" ? "⛔" : tone === "warn" ? "⚠️" : tone === "success" ? "✅" : "ℹ️";
  return (
    <div className={`notice notice-${tone}`} role={tone === "error" || tone === "warn" ? "alert" : "note"}>
      <span className="notice-icon" aria-hidden="true">{icon}</span>
      <div className="notice-body">
        {panel.title && <div className="notice-title">{panel.title}</div>}
        <Markdown content={panel.content} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Calendar — month grid of records with a date column (read-only)
// ---------------------------------------------------------------------------

const _MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const _WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Parse a row's date value to a Date (date-only), or null. */
function parseEventDate(v: unknown): Date | null {
  if (v == null) return null;
  const s = String(v);
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function CalendarPanelView({ panel, slug, pageParams }: { panel: CalendarPanel; slug: string; pageParams: Record<string, string> }) {
  const { data, loading, error } = usePanelData(slug, panel.id, true, 0, pageParams);
  // Events grouped by YYYY-M-D key.
  const { events, defaultMonth } = useMemo(() => {
    const map = new Map<string, { title: string; color?: string }[]>();
    let earliest: Date | null = null;
    for (const row of data?.rows ?? []) {
      const d = parseEventDate(row[panel.date_field]);
      if (!d) continue;
      if (!earliest || d < earliest) earliest = d;
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      const list = map.get(key) ?? [];
      list.push({
        title: String(row[panel.title_field] ?? "—"),
        color: panel.color_field ? String(row[panel.color_field] ?? "") : undefined,
      });
      map.set(key, list);
    }
    return { events: map, defaultMonth: earliest ?? new Date() };
  }, [data, panel.date_field, panel.title_field, panel.color_field]);

  const [cursor, setCursor] = useState<Date | null>(null);
  const month = cursor ?? new Date(defaultMonth.getFullYear(), defaultMonth.getMonth(), 1);

  if (loading) return <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}><div className="q-skel-card" style={{ height: 260 }} /></div>;
  if (error) return <div className="q-empty"><span className="q-empty-icon">⚠</span>Error: {error}</div>;
  const rowCount = data?.rows?.length ?? 0;
  if (data?.note && !rowCount) return <div className="q-empty"><span className="q-empty-icon">ℹ</span>{data.note}</div>;
  // Rows came back but none had a parseable date — say so distinctly, never
  // render a blank month that reads as "nothing scheduled" (RULE #1).
  if (rowCount > 0 && events.size === 0) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">∅</span>
        {rowCount} row(s) had no valid “{panel.date_field}” date to place on the calendar.
      </div>
    );
  }

  const year = month.getFullYear();
  const mon = month.getMonth();
  const first = new Date(year, mon, 1);
  const startPad = first.getDay();
  const daysInMonth = new Date(year, mon + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < startPad; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  const step = (delta: number) => setCursor(new Date(year, mon + delta, 1));

  return (
    <div className="cal">
      <div className="cal-head">
        <button type="button" className="q-btn" onClick={() => step(-1)}>‹</button>
        <span className="cal-title">{_MONTHS[mon]} {year}</span>
        <button type="button" className="q-btn" onClick={() => step(1)}>›</button>
      </div>
      <div className="cal-grid">
        {_WEEKDAYS.map((w) => (
          <div key={w} className="cal-weekday">{w}</div>
        ))}
        {cells.map((d, i) => {
          const evs = d != null ? events.get(`${year}-${mon}-${d}`) ?? [] : [];
          const t = new Date();
          const isToday =
            d != null && d === t.getDate() && mon === t.getMonth() && year === t.getFullYear();
          return (
            <div
              key={i}
              className={`cal-cell${d == null ? " cal-cell-empty" : ""}${isToday ? " cal-today" : ""}`}
            >
              {d != null && <div className="cal-daynum">{d}</div>}
              {evs.slice(0, 4).map((e, j) => (
                <div key={j} className={`cal-event ${e.color ? badgeClass(e.color) : "q-badge q-badge-blue"}`} title={e.title}>
                  {e.title}
                </div>
              ))}
              {evs.length > 4 && (
                <div className="cal-more" title={evs.slice(4).map((e) => e.title).join(", ")}>
                  +{evs.length - 4} more
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Map — geospatial markers (Leaflet). Loaded ssr:false (Leaflet needs window).
// ---------------------------------------------------------------------------

const LeafletMap = dynamic(() => import("./LeafletMap"), {
  ssr: false,
  loading: () => <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}><div className="q-skel-card" style={{ height: 320 }} /></div>,
});

function MapPanelView({ panel, slug, pageParams }: { panel: MapPanel; slug: string; pageParams: Record<string, string> }) {
  const { data, loading, error } = usePanelData(slug, panel.id, true, 0, pageParams);
  if (loading) return <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}><div className="q-skel-card" style={{ height: 320 }} /></div>;
  if (error) return <div className="q-empty"><span className="q-empty-icon">⚠</span>Error: {error}</div>;
  const allRows = data?.rows ?? [];
  if (data?.note && !allRows.length) return <div className="q-empty"><span className="q-empty-icon">ℹ</span>{data.note}</div>;

  let dropped = 0;
  const points: MapPoint[] = allRows.flatMap((row) => {
    const lat = Number(row[panel.lat_field]);
    const lng = Number(row[panel.lng_field]);
    // Validate the actual coordinate RANGE, not just finiteness — a sentinel
    // like 0,0 (null-island), 999, or swapped lat/lng would otherwise drop a
    // marker in the wrong place. Count drops so they're surfaced, not silent.
    const valid =
      Number.isFinite(lat) && Number.isFinite(lng) &&
      lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180 &&
      !(lat === 0 && lng === 0);
    if (!valid) { dropped++; return []; }
    return [{ lat, lng, label: panel.label_field ? String(row[panel.label_field] ?? "") : undefined }];
  });

  if (!points.length) {
    return (
      <div className="q-empty">
        <span className="q-empty-icon">∅</span>
        {allRows.length
          ? `${allRows.length} row(s) had no valid ${panel.lat_field}/${panel.lng_field} coordinates.`
          : `No mappable rows (need numeric ${panel.lat_field}/${panel.lng_field}).`}
      </div>
    );
  }
  return (
    <>
      <LeafletMap points={points} />
      {(dropped > 0 || data?.truncated) && (
        <div style={{ fontSize: 11.5, color: "var(--citra-muted)", marginTop: 4 }}>
          {dropped > 0 && `${dropped} row(s) had invalid coordinates and were not plotted. `}
          {data?.truncated && `Showing first ${allRows.length} — more exist; narrow the filter.`}
        </div>
      )}
    </>
  );
}
