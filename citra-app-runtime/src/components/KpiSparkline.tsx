"use client";

// KpiSparkline + KpiProgress — extracted from PanelRenderer (U2/U3) so the
// dashboard tiles AND the stat_strip band share one implementation.

import ReactECharts from "echarts-for-react";
import { fmtNum, getAppLocale } from "@/lib/executiveTheme";

/** Tiny axis-less sparkline (executive KPI). Hover surfaces the underlying
 * trend point — "<date>: <value>" when bucket labels are available, else the
 * value alone — so the tile is no longer a decoration-only number. */
export function KpiSparkline({
  points,
  color,
  labels,
  height = 34,
}: {
  points: number[];
  color: string;
  labels?: string[];
  height?: number;
}) {
  const humanLabel = (s: string): string => {
    if (!/^\d{4}-\d{2}-\d{2}/.test(s)) return s;
    const d = new Date(s);
    return Number.isNaN(d.getTime())
      ? s
      : d.toLocaleDateString(getAppLocale().locale, { day: "numeric", month: "short" });
  };
  const option = {
    animation: false,
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: {
      type: "category",
      show: false,
      boundaryGap: false,
      data: labels ?? points.map((_, i) => i)
    },
    yAxis: { type: "value", show: false, scale: true },
    tooltip: {
      show: true,
      trigger: "axis",
      backgroundColor: "rgba(15,23,42,0.92)",
      borderWidth: 0,
      padding: [5, 9],
      textStyle: { color: "#f1f5f9", fontSize: 11 },
      extraCssText: "border-radius:8px;box-shadow:0 6px 18px rgba(15,23,42,0.22);",
      axisPointer: { lineStyle: { color, width: 1, opacity: 0.5 } },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          value?: unknown;
        }>;
        if (!arr.length) return "";
        const p = arr[0];
        const v = fmtNum(p.value);
        const head =
          labels && p.axisValue ? `${humanLabel(String(p.axisValue))}: ` : "";
        return `${head}<b>${v}</b>`;
      }
    },
    series: [
      {
        type: "line",
        data: points,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color },
        areaStyle: { opacity: 0.12, color },
        emphasis: { focus: "series", itemStyle: { color, borderColor: "#fff" } }
      }
    ]
  };
  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      opts={{ renderer: "svg" }}
    />
  );
}

/** Progress-to-target bar for a KPI tile. `thresholds` are ascending
 *  fraction-of-target cut points (e.g. [0.5, 0.8]) that band the colour:
 *  below the first → red, between → amber, at/above the last → green. */
export function KpiProgress({
  value,
  target,
  thresholds,
}: {
  value: number;
  target: number;
  thresholds?: number[];
}) {
  const frac = Math.max(0, Math.min(1, value / target));
  const cuts = (thresholds && thresholds.length ? thresholds : [0.5, 0.8])
    .slice()
    .sort((a, b) => a - b);
  let band = "green";
  if (frac < cuts[0]) band = "red";
  else if (frac < cuts[cuts.length - 1]) band = "amber";
  const pct = Math.round(frac * 100);
  return (
    <div className="kpi-progress" title={`${pct}% of target (${fmtNum(value)} / ${fmtNum(target)})`}>
      <div className="kpi-progress-track">
        <div className={`kpi-progress-fill kpi-progress-${band}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="kpi-progress-label">{pct}% of {fmtNum(target)}</span>
    </div>
  );
}
