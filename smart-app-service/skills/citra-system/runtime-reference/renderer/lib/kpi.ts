// KPI value computation shared by the dashboard tiles, the stat_strip band
// and the hero headline metric. Extracted from PanelRenderer (U2/U3).

import { fmtINR, fmtNum, type Delta } from "@/lib/executiveTheme";
import type { PanelMetricValue } from "@/types/spec";

export interface KpiResult {
  display: string;
  /** Raw numeric value (for progress-to-target gauges). */
  value?: number;
  delta?: Delta;
  spark?: number[];
  /** Date labels aligned with `spark` (server path only) for the hover tooltip. */
  sparkLabels?: string[];
}

/** Heuristic: monetary fields format as currency. */
export function looksMonetary(field?: string, name?: string): boolean {
  const s = `${field ?? ""} ${name ?? ""}`.toLowerCase();
  return /(amount|revenue|cost|price|value|sales|spend|inr|rupee|₹|crore|lakh|billing|payment|due|outstanding)/.test(
    s
  );
}

/** Auto-pick a tile icon by metric semantics (Track B) — used only when the
 * spec declares none. Money → banknote, time → calendar, people → users. */
export function autoMetricIcon(metric: {
  name?: string;
  field?: string;
  agg?: string;
}): string | undefined {
  const s = `${metric.field ?? ""} ${metric.name ?? ""}`.toLowerCase();
  if (looksMonetary(metric.field, metric.name)) return "banknote";
  if (/(date|day|week|month|time|sla|due)/.test(s)) return "calendar";
  if (/(user|consumer|customer|officer|staff|people)/.test(s)) return "users";
  if (/(case|claim|dispute|ticket|complaint|inspection)/.test(s)) return "clipboard-list";
  if (/(rate|ratio|percent|pct)/.test(s) || metric.agg === "ratio") return "percent";
  return undefined;
}

/** Format a source-computed metric (accurate path — no row cap). Surfaces the
 * REAL prior-period delta + trend the backend computed (not fabricated). */
export function kpiFromServer(
  sm: PanelMetricValue,
  metric: { name?: string; agg: string; field?: string }
): KpiResult {
  if (sm.value === null || !Number.isFinite(sm.value)) return { display: "—" };
  // A ratio value is a fraction (0..1) from the server — show it as a percent,
  // never a bare "0.42" which reads as a count.
  const display =
    metric.agg === "ratio"
      ? `${(sm.value * 100).toFixed(1)}%`
      : looksMonetary(metric.field, metric.name)
      ? fmtINR(sm.value)
      : fmtNum(sm.value);
  const delta: Delta | undefined = sm.delta
    ? { dir: sm.delta.dir, text: sm.delta.text }
    : undefined;
  const spark =
    Array.isArray(sm.trend) && sm.trend.length >= 2 ? sm.trend : undefined;
  const sparkLabels =
    spark && Array.isArray(sm.trend_labels) && sm.trend_labels.length === spark.length
      ? sm.trend_labels
      : undefined;
  return { display, value: sm.value, delta, spark, sparkLabels };
}

export function computeMetric(
  metric: { name?: string; agg: string; field?: string },
  rows: Record<string, unknown>[]
): KpiResult {
  if (!rows.length) return { display: "—" };

  const series: number[] = [];
  if (metric.field) {
    for (const r of rows) {
      const v = r[metric.field];
      const n = typeof v === "number" ? v : Number(v);
      if (Number.isFinite(n)) series.push(n);
    }
  }

  let value: number | null;
  switch (metric.agg) {
    case "count":
      value = rows.length;
      break;
    case "sum":
      value = series.reduce((a, b) => a + b, 0);
      break;
    case "avg":
      value = series.length
        ? series.reduce((a, b) => a + b, 0) / series.length
        : null;
      break;
    case "min":
      value = series.length ? Math.min(...series) : null;
      break;
    case "max":
      value = series.length ? Math.max(...series) : null;
      break;
    default:
      value = null; // ratio — needs numerator/denominator config
  }

  if (value === null) return { display: "—" };

  const display = looksMonetary(metric.field, metric.name)
    ? fmtINR(value)
    : fmtNum(value);

  // No client-side delta/sparkline here. The old "avg(first half) vs avg(second
  // half)" was computed over ARBITRARY row order (no time field on this
  // fallback path), so it manufactured a trend/percentage that could be pure
  // noise — and rendered identically to the server's real prior-period delta.
  // Show the value alone; an honest trend comes only from the server path
  // (kpiFromServer), which has a true time-ordered series.
  return { display, value };
}
