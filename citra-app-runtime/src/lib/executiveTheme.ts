/**
 * executiveTheme — the beauty layer for the native render stack.
 *
 * The LLM (chart spec / dashboard panel) NEVER emits colors, sizes, or number
 * formatting. All of that lives here:
 *   - EXEC_PALETTE      refined muted executive palette (slate/blue/teal + 1 accent)
 *   - fmtINR/fmtNum/…   number formatters tuned for Indian executives
 *   - ECHARTS_BASE      house-style base option fragment (gridlines, tooltip, …)
 *   - "citra-exec"      a registered echarts theme combining the two
 *
 * Importing this module has the side effect of registering the "citra-exec"
 * echarts theme exactly once.
 */

import * as echarts from "echarts";

// ---------------------------------------------------------------------------
// Palette — deep blues/teals/slate with ONE warm accent (amber). No bright
// primaries; reads as a boardroom deck, not a dashboard demo.
// ---------------------------------------------------------------------------

export const EXEC_PALETTE = [
  "#2f4b7c", // deep indigo-slate
  "#0e7490", // teal
  "#3b6ea5", // muted blue
  "#5f8a8b", // sage teal
  "#1e3a5f", // navy
  "#7c93b3", // dusty slate-blue
  "#4c6b8a", // steel
  "#c7793f", // accent — burnt amber (the ONE warm)
];

export const EXEC_ACCENT = "#c7793f";
export const EXEC_UP = "#15803d"; // delta up (muted green)
export const EXEC_DOWN = "#b91c1c"; // delta down (muted red)
export const EXEC_FLAT = "#64748b";

const AXIS_LABEL = "#64748b";
const GRID_LINE = "#eef1f5";

// ---------------------------------------------------------------------------
// Chart palettes (Theme v2 — theme.chart_palette). "calm" is the classic
// exec palette; "brand" derives a ramp from the app's primary color. Set once
// per app alongside the locale (LocaleSetter); charts read the active palette
// at option-build time (chartToEcharts injects `color`).
// ---------------------------------------------------------------------------

const VIVID_PALETTE = [
  "#2563eb", "#0891b2", "#7c3aed", "#059669",
  "#d97706", "#dc2626", "#0284c7", "#9333ea",
];
const MONO_PALETTE = [
  "#1e3a5f", "#2f4b7c", "#3b6ea5", "#5b83b5",
  "#7c9ac6", "#9db3d6", "#bfcde5", "#dfe7f2",
];

/** 8-step ramp derived from a brand hex: alternating darker/lighter mixes. */
function _brandRamp(hex: string): string[] {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return EXEC_PALETTE;
  const n = parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  const mix = (t: number, to: number) =>
    "#" +
    [r, g, b]
      .map((c) => Math.round(c + (to - c) * t).toString(16).padStart(2, "0"))
      .join("");
  return [
    hex, mix(0.35, 0), mix(0.3, 255), mix(0.55, 0),
    mix(0.5, 255), mix(0.72, 0), mix(0.68, 255), "#c7793f",
  ];
}

let _activePalette: string[] | null = null;

/** Set the active chart palette from theme.chart_palette (+ theme.primary for
 * "brand"). Unset / "calm" → null → the registered exec theme's palette. */
export function setChartPalette(
  name?: string | null,
  primary?: string | null,
): void {
  if (!name || name === "calm") _activePalette = null;
  else if (name === "vivid") _activePalette = VIVID_PALETTE;
  else if (name === "mono") _activePalette = MONO_PALETTE;
  else if (name === "brand") _activePalette = _brandRamp(primary || "");
  else _activePalette = null;
}

export function getChartPalette(): string[] | null {
  return _activePalette;
}

// ---------------------------------------------------------------------------
// Locale / currency — set ONCE per app from AppSpec.theme (see setAppLocale).
// Every formatter + date axis honors it. Defaults to en-US / USD (the
// platform's primary market) so apps with no declared locale render for the US.
// Intl does the locale-correct work: $2.6M (en-US), ₹2.6 Cr (en-IN),
// €1,2 Mio. (de-DE), etc. — symbol, grouping, and compact suffix all per-locale.
// ---------------------------------------------------------------------------

let _localeCfg: { locale: string; currency: string } = {
  locale: "en-US",
  currency: "USD",
};

/** Set the active locale/currency for all chart + KPI formatting. Call once
 * when an app's spec loads (from theme.locale / theme.currency). Falls back to
 * en-US / USD when a value is missing. */
export function setAppLocale(locale?: string | null, currency?: string | null): void {
  _localeCfg = { locale: locale || "en-US", currency: currency || "USD" };
  _nfCache.clear();
}

export function getAppLocale(): { locale: string; currency: string } {
  return _localeCfg;
}

// Intl.NumberFormat is expensive to construct — cache per (locale|currency|opts).
const _nfCache = new Map<string, Intl.NumberFormat>();
function _nf(key: string, opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const ck = `${_localeCfg.locale}|${_localeCfg.currency}|${key}`;
  let f = _nfCache.get(ck);
  if (!f) {
    try {
      f = new Intl.NumberFormat(_localeCfg.locale, opts);
    } catch {
      f = new Intl.NumberFormat("en-US", opts); // invalid locale/currency → safe default
    }
    _nfCache.set(ck, f);
  }
  return f;
}

function toNum(v: unknown): number | null {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Locale-aware compact CURRENCY for KPI tiles / monetary axes.
 *   en-US/USD → "$2.6M"   en-IN/INR → "₹2.6 Cr", "₹68 L"   de-DE/EUR → "1,2 Mio. €"
 */
export function fmtCurrency(value: unknown): string {
  const n = toNum(value);
  if (n === null) return "—";
  return _nf("cur", {
    style: "currency",
    currency: _localeCfg.currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

/** @deprecated Use fmtCurrency. Name kept for existing call sites — it is now
 * locale/currency-aware, not ₹-only. */
export const fmtINR = fmtCurrency;

/** EXACT locale currency for table/detail CELLS — full precision, grouped, NOT
 * compact. An officer deciding on an amount needs ₹1,23,456.50, not "₹1.2 L".
 * (fmtCurrency is for KPI tiles/axes where compact is appropriate.) */
export function fmtMoney(value: unknown): string {
  const n = toNum(value);
  if (n === null) return String(value ?? "");
  return _nf("money", {
    style: "currency",
    currency: _localeCfg.currency,
    maximumFractionDigits: 2,
  }).format(n);
}

/** Locale-aware compact number for axes / chips: 12.3K / 4.2M (en), per-locale
 * suffixes elsewhere. Plain (locale-grouped) for small values. */
export function fmtNum(value: unknown): string {
  const n = toNum(value);
  if (n === null) return String(value ?? "");
  return _nf("num", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/** Signed percent, 1 decimal: +4.2%, -1.0%, 0.0%. Locale-neutral sign + symbol. */
export function fmtPct(value: unknown): string {
  const n = toNum(value);
  if (n === null) return "—";
  const s = Math.abs(n).toLocaleString(_localeCfg.locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  return `${sign}${s}%`;
}

export interface Delta {
  text: string;
  dir: "up" | "down" | "flat";
}

/** KPI delta chip model. Input is a percent (already computed). */
export function fmtDelta(value: unknown): Delta {
  const n = toNum(value);
  if (n === null) return { text: "—", dir: "flat" };
  // ±0.5% flat band: a sub-half-percent wobble reads as "basically unchanged"
  // rather than firing a green/red sentiment chip on trivial noise (was ±0.05%).
  const dir: Delta["dir"] = n > 0.5 ? "up" : n < -0.5 ? "down" : "flat";
  return { text: fmtPct(n), dir };
}

// ---------------------------------------------------------------------------
// ECHARTS_BASE — house style fragment. Spread into every option.
// ---------------------------------------------------------------------------

export const ECHARTS_BASE = {
  animationDuration: 400,
  animationEasing: "cubicOut" as const,
  textStyle: {
    fontFamily:
      'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    color: "#334155",
  },
  grid: {
    left: 12,
    right: 18,
    top: 28,
    bottom: 12,
    containLabel: true,
  },
  tooltip: {
    trigger: "item" as const,
    backgroundColor: "rgba(15,23,42,0.92)",
    borderWidth: 0,
    padding: [8, 12],
    textStyle: { color: "#f1f5f9", fontSize: 12 },
    extraCssText:
      "border-radius:10px;box-shadow:0 8px 24px rgba(15,23,42,0.22);",
  },
  legend: {
    bottom: 0,
    icon: "circle",
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 16,
    textStyle: { color: AXIS_LABEL, fontSize: 11 },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: AXIS_LABEL, fontSize: 11 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: AXIS_LABEL,
      fontSize: 11,
      formatter: (v: number) => fmtNum(v),
    },
    splitLine: {
      show: true,
      lineStyle: { color: GRID_LINE, type: "dashed" as const },
    },
  },
};

// ---------------------------------------------------------------------------
// Registered theme: "citra-exec"
// ---------------------------------------------------------------------------

const CITRA_EXEC_THEME = {
  color: EXEC_PALETTE,
  ...ECHARTS_BASE,
  // echarts theme axis keys are top-level, not nested under categoryAxis.
  categoryAxis: ECHARTS_BASE.categoryAxis,
  valueAxis: ECHARTS_BASE.valueAxis,
  title: {
    textStyle: { color: "#1e293b", fontSize: 13, fontWeight: 600 },
    left: 0,
    top: 0,
  },
};

let _registered = false;
export function ensureExecTheme(): void {
  if (_registered) return;
  echarts.registerTheme("citra-exec", CITRA_EXEC_THEME);
  _registered = true;
}

// Register on import (module init).
ensureExecTheme();

export const EXEC_THEME_NAME = "citra-exec";
