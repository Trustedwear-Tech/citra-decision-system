/**
 * chartToEcharts — pure mapper from the shared chart SPEC + rows to a styled
 * echarts option. ONE renderer for both dashboard chart panels and in-chat
 * chart blocks. The spec carries NO styling; all of it comes from
 * executiveTheme (ECHARTS_BASE + formatters + the "citra-exec" palette).
 *
 * No React here.
 */

import type { EChartsOption } from "echarts";
import { ECHARTS_BASE, fmtNum, getAppLocale, getChartPalette } from "./executiveTheme";

export interface ChartSpec {
  chart_type: "bar" | "line" | "area" | "pie" | "funnel" | "scatter";
  title?: string;
  x: string;
  y: string | string[];
  group_by?: string;
  stacked?: boolean;
}

export interface ChartToEchartsOpts {
  /** In-chat compact mode: smaller fonts, fewer ticks, legend off if single. */
  compact?: boolean;
}

type Row = Record<string, unknown>;

function num(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

// Date-bucket recognisers — one per grain the backend emits via date_trunc /
// time_grain. The axis x value is the bucket string; we humanise the rendered
// tick (the underlying data key stays raw, so tooltips remain exact).
//   day / datetime  2026-03-02 (T..)   week start is also a day
//   month           2026-03
//   quarter         2026-Q1
//   year            2026  (1900–2199, so a 4-digit category code isn't mistaken)
const _DAY_RE = /^(\d{4})-(\d{2})-(\d{2})([T ][\d:.]+([Zz]|[+-]\d{2}:?\d{2})?)?$/;
const _MONTH_RE = /^(\d{4})-(\d{2})$/;
const _QTR_RE = /^(\d{4})-[Qq]([1-4])$/;
const _YEAR_RE = /^(19|20|21)\d{2}$/;

type Grain = "day" | "month" | "quarter" | "year";

function parseBucket(v: string): { grain: Grain; date: Date; year: string; q?: number } | null {
  let m: RegExpExecArray | null;
  if ((m = _DAY_RE.exec(v))) {
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? null : { grain: "day", date: d, year: m[1] };
  }
  if ((m = _MONTH_RE.exec(v))) {
    const mo = Number(m[2]);
    if (mo < 1 || mo > 12) return null;
    return { grain: "month", date: new Date(Number(m[1]), mo - 1, 1), year: m[1] };
  }
  if ((m = _QTR_RE.exec(v))) {
    return { grain: "quarter", date: new Date(Number(m[1]), 0, 1), year: m[1], q: Number(m[2]) };
  }
  if ((m = _YEAR_RE.exec(v))) {
    return { grain: "year", date: new Date(Number(v), 0, 1), year: v };
  }
  return null;
}

/**
 * Compact, human axis label for a category value, at ANY date grain:
 *   day "2026-03-02T00:00:00" → "2 Mar" (or "Mar '26" multi-year)
 *   month "2026-03"           → "Mar '26" (or "Mar" single-year)
 *   quarter "2026-Q1"         → "Q1 '26"
 *   year "2026"               → "2026"
 * Non-date categories pass through unchanged (formatter returns undefined when
 * fewer than 60% of values parse as a date bucket).
 */
function makeCategoryLabelFormatter(
  categories: string[],
): ((v: string) => string) | undefined {
  const dateLike = categories.map(parseBucket).filter(Boolean) as NonNullable<
    ReturnType<typeof parseBucket>
  >[];
  if (dateLike.length < Math.max(2, Math.ceil(categories.length * 0.6))) {
    return undefined; // not a date axis — leave labels as-is
  }
  const multiYear = new Set(dateLike.map((p) => p.year)).size > 1;
  const loc = getAppLocale().locale;
  return (v: string) => {
    const p = parseBucket(v);
    if (!p) return v;
    switch (p.grain) {
      case "year":
        return p.year;
      case "quarter":
        return `Q${p.q} '${p.year.slice(2)}`;
      case "month":
        return p.date.toLocaleDateString(
          loc,
          multiYear ? { month: "short", year: "2-digit" } : { month: "short" },
        );
      case "day":
      default:
        return p.date.toLocaleDateString(
          loc,
          multiYear ? { month: "short", year: "2-digit" } : { day: "numeric", month: "short" },
        );
    }
  };
}

export function chartToEchartsOption(
  spec: ChartSpec,
  rows: Row[],
  opts: ChartToEchartsOpts = {},
): EChartsOption {
  const option = _chartToEchartsOption(spec, rows, opts);
  // Theme v2 palette override (theme.chart_palette): option-level `color`
  // wins over the registered "citra-exec" theme; null → classic palette.
  const palette = getChartPalette();
  return palette ? { color: palette, ...option } : option;
}

function _chartToEchartsOption(
  spec: ChartSpec,
  rows: Row[],
  opts: ChartToEchartsOpts = {},
): EChartsOption {
  const compact = !!opts.compact;
  const ySeries = Array.isArray(spec.y) ? spec.y : [spec.y];

  const title: EChartsOption["title"] = spec.title
    ? {
        text: spec.title,
        left: 0,
        top: 0,
        textStyle: {
          color: "#1e293b",
          fontSize: compact ? 12 : 13,
          fontWeight: 600,
        },
      }
    : undefined;

  // ---- pie --------------------------------------------------------------
  if (spec.chart_type === "pie") {
    const yField = ySeries[0];
    // Cap slices so a high-cardinality dimension (e.g. 30 districts) doesn't
    // become an unreadable confetti ring — keep the top 7 by value and fold the
    // rest into one "Other" slice.
    const PIE_CAP = 8;
    let pieData = rows
      .map((r) => ({ name: String(r[spec.x] ?? ""), value: num(r[yField]) }))
      .sort((a, b) => b.value - a.value);
    if (pieData.length > PIE_CAP) {
      const head = pieData.slice(0, PIE_CAP - 1);
      const rest = pieData.slice(PIE_CAP - 1);
      pieData = [
        ...head,
        { name: `Other (${rest.length})`, value: rest.reduce((s, d) => s + d.value, 0) },
      ];
    }
    return {
      ...ECHARTS_BASE,
      title,
      tooltip: {
        ...ECHARTS_BASE.tooltip,
        trigger: "item",
        formatter: (params: unknown) => {
          const p = params as {
            name?: string;
            value?: unknown;
            percent?: number;
          };
          return `${p.name}: ${fmtNum(p.value)} (${p.percent}%)`;
        },
      },
      legend: compact
        ? { ...ECHARTS_BASE.legend, type: "scroll" as const }
        : ECHARTS_BASE.legend,
      series: [
        {
          type: "pie",
          radius: compact ? ["44%", "70%"] : ["48%", "72%"],
          center: ["50%", compact ? "46%" : "48%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: "#fff", borderWidth: 2 },
          label: { show: !compact, formatter: "{b}: {d}%", fontSize: 11 },
          labelLine: { show: !compact },
          data: pieData,
        },
      ],
    };
  }

  // ---- funnel (pipeline / stage conversion) -----------------------------
  // x = stage label column, y = value column. Sorted descending so the
  // widest stage sits on top regardless of row order.
  if (spec.chart_type === "funnel") {
    const yField = ySeries[0];
    const data = rows
      .map((r) => ({ name: String(r[spec.x] ?? ""), value: num(r[yField]) }))
      .sort((a, b) => b.value - a.value);
    return {
      ...ECHARTS_BASE,
      title,
      tooltip: {
        ...ECHARTS_BASE.tooltip,
        trigger: "item",
        formatter: (params: unknown) => {
          const p = params as { name?: string; value?: unknown };
          return `${p.name}: <b>${fmtNum(p.value)}</b>`;
        },
      },
      legend: { ...ECHARTS_BASE.legend, type: "scroll" as const },
      series: [
        {
          type: "funnel",
          left: "8%",
          right: "8%",
          top: title ? (compact ? 34 : 40) : 12,
          bottom: 12,
          minSize: "0%",
          maxSize: "100%",
          sort: "descending",
          gap: 2,
          label: { show: !compact, position: "inside", formatter: "{b}: {c}" },
          itemStyle: { borderColor: "#fff", borderWidth: 1 },
          data,
        },
      ],
    };
  }

  // ---- scatter (correlation: numeric x vs numeric y) --------------------
  // Both x and y are numeric columns. One series per group_by value (or a
  // single series when ungrouped); each point is [x, y].
  if (spec.chart_type === "scatter") {
    const yField = ySeries[0];
    let scatterSeries: Record<string, unknown>[];
    if (spec.group_by) {
      const groups = uniq(rows.map((r) => String(r[spec.group_by!] ?? "")));
      scatterSeries = groups.map((g) => ({
        name: g,
        type: "scatter",
        symbolSize: compact ? 7 : 9,
        data: rows
          .filter((r) => String(r[spec.group_by!] ?? "") === g)
          .map((r) => [num(r[spec.x]), num(r[yField])]),
      }));
    } else {
      scatterSeries = [
        {
          name: yField,
          type: "scatter",
          symbolSize: compact ? 7 : 9,
          data: rows.map((r) => [num(r[spec.x]), num(r[yField])]),
        },
      ];
    }
    const showScatterLegend = scatterSeries.length > 1;
    return {
      ...ECHARTS_BASE,
      title,
      grid: {
        ...ECHARTS_BASE.grid,
        bottom: showScatterLegend ? 44 : 24,
        top: title ? (compact ? 30 : 36) : ECHARTS_BASE.grid.top,
      },
      tooltip: {
        ...ECHARTS_BASE.tooltip,
        trigger: "item",
        formatter: (params: unknown) => {
          const p = params as { seriesName?: string; value?: [number, number] };
          const v = p.value ?? [0, 0];
          return `${p.seriesName ?? ""}<br/>${spec.x}: <b>${fmtNum(v[0])}</b><br/>${yField}: <b>${fmtNum(v[1])}</b>`;
        },
      },
      legend: showScatterLegend
        ? { ...ECHARTS_BASE.legend, show: true, type: "scroll" as const }
        : { show: false },
      xAxis: {
        type: "value",
        name: spec.x,
        ...ECHARTS_BASE.valueAxis,
        axisLabel: { ...ECHARTS_BASE.valueAxis.axisLabel, fontSize: compact ? 10 : 11 },
      },
      yAxis: {
        type: "value",
        name: yField,
        ...ECHARTS_BASE.valueAxis,
        axisLabel: { ...ECHARTS_BASE.valueAxis.axisLabel, fontSize: compact ? 10 : 11 },
      },
      series: scatterSeries as EChartsOption["series"],
    };
  }

  // ---- cartesian (bar / line / area) ------------------------------------
  const categories = uniq(rows.map((r) => String(r[spec.x] ?? "")));

  let series: Record<string, unknown>[];

  if (spec.group_by) {
    // One series per group_by value, single y field.
    const yField = ySeries[0];
    const groups = uniq(rows.map((r) => String(r[spec.group_by!] ?? "")));
    series = groups.map((g) => {
      const byCat = new Map<string, number>();
      for (const r of rows) {
        if (String(r[spec.group_by!] ?? "") !== g) continue;
        byCat.set(String(r[spec.x] ?? ""), num(r[yField]));
      }
      return baseSeries(
        g,
        // Missing (group × category) cell → null (a gap), NOT a fabricated 0
        // that reads as "collapsed to zero" on a line/bar.
        categories.map((c) => byCat.get(c) ?? null),
        spec,
      );
    });
  } else {
    // One series per y field.
    series = ySeries.map((field) => {
      const byCat = new Map<string, number>();
      for (const r of rows) byCat.set(String(r[spec.x] ?? ""), num(r[field]));
      return baseSeries(
        field,
        // Missing (group × category) cell → null (a gap), NOT a fabricated 0
        // that reads as "collapsed to zero" on a line/bar.
        categories.map((c) => byCat.get(c) ?? null),
        spec,
      );
    });
  }

  const multiSeries = series.length > 1;
  const showLegend = multiSeries && !(compact && !multiSeries);

  // Dense category axes (esp. daily time series) must NOT force every tick —
  // let echarts thin them. Small categorical axes still show all labels.
  const dense = categories.length > 12;
  const labelFormatter = makeCategoryLabelFormatter(categories);

  return {
    ...ECHARTS_BASE,
    title,
    grid: {
      ...ECHARTS_BASE.grid,
      // Reserve enough band for the x-labels AND the legend so they never
      // collide (legend sits at bottom:0; labels stack just above the grid).
      bottom: showLegend ? 44 : 24,
      top: title ? (compact ? 30 : 36) : ECHARTS_BASE.grid.top,
    },
    tooltip: {
      ...ECHARTS_BASE.tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      // Custom formatter so the header date is humanised (not raw ISO) and
      // every series value runs through fmtNum.
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          seriesName?: string;
          value?: unknown;
          marker?: string;
        }>;
        if (!arr.length) return "";
        const raw = String(arr[0].axisValue ?? "");
        const head = labelFormatter ? labelFormatter(raw) : raw;
        const lines = arr
          .map(
            (p) =>
              `${p.marker ?? ""} ${p.seriesName ?? ""}: <b>${fmtNum(p.value)}</b>`,
          )
          .join("<br/>");
        return `<div style="font-weight:600;margin-bottom:3px">${head}</div>${lines}`;
      },
    },
    legend: showLegend
      ? { ...ECHARTS_BASE.legend, show: true, type: "scroll" as const }
      : { show: false },
    xAxis: {
      type: "category",
      data: categories,
      ...ECHARTS_BASE.categoryAxis,
      axisLabel: {
        ...ECHARTS_BASE.categoryAxis.axisLabel,
        fontSize: compact ? 10 : 11,
        interval: dense || compact ? ("auto" as const) : 0,
        hideOverlap: true,
        ...(labelFormatter ? { formatter: labelFormatter } : {}),
      },
    },
    yAxis: {
      type: "value",
      ...ECHARTS_BASE.valueAxis,
      axisLabel: {
        ...ECHARTS_BASE.valueAxis.axisLabel,
        fontSize: compact ? 10 : 11,
      },
      splitNumber: compact ? 3 : 5,
    },
    series: series as EChartsOption["series"],
  };
}

function baseSeries(
  name: string,
  data: (number | null)[],
  spec: ChartSpec,
): Record<string, unknown> {
  const stack = spec.stacked ? "s" : undefined;
  if (spec.chart_type === "bar") {
    return {
      name,
      type: "bar",
      data,
      stack,
      barMaxWidth: 36,
      itemStyle: { borderRadius: [3, 3, 0, 0] },
    };
  }
  // line / area
  return {
    name,
    type: "line",
    data,
    stack,
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 2 },
    ...(spec.chart_type === "area"
      ? { areaStyle: { opacity: 0.18 } }
      : {}),
  };
}

function uniq(arr: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of arr) {
    if (!seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
}
