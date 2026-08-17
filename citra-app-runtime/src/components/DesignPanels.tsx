// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

"use client";

// Designed panels (runtime-ui-modernization-plan.md U3):
//   HeroPanelView      — C1 page-header band (icon + headline + live metric + actions)
//   StatStripPanelView — C2 compact KPI band with delta arrows + sparklines
//   TimelinePanelView  — C3 vertical event feed bound to a tabular source
// New components live OUTSIDE PanelRenderer.tsx by design — the monolith only
// grows its dispatch switch.

import { useRouter } from "next/navigation";
import Icon from "./Icon";
import { KpiSparkline } from "./KpiSparkline";
import { usePanelData } from "@/lib/usePanelData";
import { kpiFromServer, computeMetric, autoMetricIcon } from "@/lib/kpi";
import { badgeClass, badgeColorFor } from "@/lib/format";
import { EXEC_PALETTE, getAppLocale } from "@/lib/executiveTheme";
import { listPages, buildNavigateHref, substituteParams } from "@/lib/pages";
import type {
  AppSpec,
  HeroPanel,
  StatStripPanel,
  TimelinePanel,
  PanelMetricValue,
} from "@/types/spec";

// ---------------------------------------------------------------------------
// Hero — C1
// ---------------------------------------------------------------------------

export function HeroPanelView({
  panel,
  app,
  slug,
  pageParams,
}: {
  panel: HeroPanel;
  app: AppSpec;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const router = useRouter();
  const hasMetric = !!panel.metric;
  const { data, loading, error } = usePanelData(slug, panel.id, hasMetric, 0, pageParams);

  let metricNode: React.ReactNode = null;
  if (hasMetric && panel.metric) {
    const sm = data?.metrics?.find((s) => s.name === panel.metric!.name);
    const kpi = sm ? kpiFromServer(sm, panel.metric) : computeMetric(panel.metric, data?.rows ?? []);
    metricNode = (
      <div className="hero-metric">
        <div className="hero-metric-value">
          {loading ? <span className="kpi-skel" /> : error ? "—" : kpi.display}
        </div>
        <div className="hero-metric-label">
          {(sm?.label ?? panel.metric.label) || panel.metric.name}
          {!loading && !error && kpi.delta && (
            <span className={`kpi-delta kpi-delta-${kpi.delta.dir}`} style={{ marginLeft: 8 }}>
              {kpi.delta.dir === "up" ? "▲" : kpi.delta.dir === "down" ? "▼" : "▬"} {kpi.delta.text}
            </span>
          )}
        </div>
      </div>
    );
  }

  const onAction = (a: NonNullable<HeroPanel["actions"]>[number]) => {
    if (!a.navigate) return;
    const pages = listPages(app);
    const page = pages.find((p) => p.id === a.navigate!.page);
    if (!page) {
      console.warn("[hero] navigate to unknown page id", a.navigate.page);
      return;
    }
    const resolved = substituteParams(a.navigate.params, { params: pageParams });
    router.push(buildNavigateHref(slug, page, resolved));
  };

  return (
    <div className="hero-band">
      {panel.icon && (
        <div className="hero-icon" aria-hidden="true">
          <Icon name={panel.icon} size={26} />
        </div>
      )}
      <div className="hero-text">
        <div className="hero-headline">{panel.headline}</div>
        {panel.subtitle && <div className="hero-subtitle">{panel.subtitle}</div>}
      </div>
      {metricNode}
      {(panel.actions ?? []).length > 0 && (
        <div className="hero-actions">
          {(panel.actions ?? []).map((a) => (
            <button key={a.label} type="button" className="hero-action" onClick={() => onAction(a)}>
              <Icon name={a.icon} size={14} />
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat strip — C2
// ---------------------------------------------------------------------------

export function StatStripPanelView({
  panel,
  slug,
  pageParams,
}: {
  panel: StatStripPanel;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const enabled = panel.metrics.some((m) => !!m.data_source);
  const { data, loading, error } = usePanelData(slug, panel.id, enabled, 0, pageParams);
  const serverMetrics = data?.metrics;

  return (
    <div className="stat-strip">
      {panel.metrics.map((m, i) => {
        const accent = EXEC_PALETTE[i % EXEC_PALETTE.length];
        const sm = serverMetrics?.find((s) => s.name === m.name);
        const kpi = sm ? kpiFromServer(sm, m) : computeMetric(m, data?.rows ?? []);
        const failed = (sm as PanelMetricValue & { error?: string } | undefined)?.error;
        const iconName = m.icon ?? autoMetricIcon(m);
        return (
          <div className="stat" key={m.name}>
            <div className="stat-head">
              {iconName && <Icon name={iconName} size={14} className="stat-icon" />}
              <span className="stat-label">{(sm?.label ?? m.label) || m.name}</span>
            </div>
            <div className="stat-value">
              {loading ? (
                <span className="kpi-skel" />
              ) : error || failed ? (
                <span className="kpi-err" title={failed || error || undefined}>⚠</span>
              ) : (
                kpi.display
              )}
            </div>
            <div className="stat-foot">
              {!loading && !error && kpi.delta && (
                <span className={`kpi-delta kpi-delta-${kpi.delta.dir}`}>
                  {kpi.delta.dir === "up" ? "▲" : kpi.delta.dir === "down" ? "▼" : "▬"} {kpi.delta.text}
                </span>
              )}
              {!loading && !error && kpi.spark && kpi.spark.length > 2 && (
                <div className="stat-spark">
                  <KpiSparkline points={kpi.spark} color={accent} labels={kpi.sparkLabels} height={22} />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Timeline — C3
// ---------------------------------------------------------------------------

export function TimelinePanelView({
  panel,
  slug,
  pageParams,
}: {
  panel: TimelinePanel;
  slug: string;
  pageParams: Record<string, string>;
}) {
  const { data, loading, error } = usePanelData(slug, panel.id, true, 0, pageParams);
  const locale = getAppLocale().locale;

  if (loading) {
    return (
      <div className="q-skel" style={{ gridTemplateColumns: "1fr" }}>
        <div className="q-skel-card" style={{ height: 160 }} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="panel-error" role="alert">
        <strong>Timeline failed to load.</strong>
        <span>{error}</span>
      </div>
    );
  }

  const rows = [...(data?.rows ?? [])];
  // Newest first by the declared date column (client sort — the fetch is
  // already capped server-side by panel.limit).
  rows.sort((a, b) => {
    const da = new Date(String(a[panel.date_field] ?? "")).getTime() || 0;
    const db = new Date(String(b[panel.date_field] ?? "")).getTime() || 0;
    return db - da;
  });

  if (!rows.length) {
    return (
      <div className="empty-state">
        <Icon name={panel.icon ?? "history"} size={22} className="empty-state-icon" />
        <div className="empty-state-title">No events yet</div>
        <div className="empty-state-sub">Entries appear here as they are recorded.</div>
      </div>
    );
  }

  const fmtDate = (v: unknown) => {
    const d = new Date(String(v ?? ""));
    return Number.isNaN(d.getTime())
      ? String(v ?? "")
      : d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
  };

  return (
    <ol className="timeline">
      {rows.map((r, i) => {
        const iconName =
          (panel.icon_field ? String(r[panel.icon_field] ?? "") : "") || panel.icon || "";
        const badgeVal = panel.badge_field ? r[panel.badge_field] : undefined;
        return (
          <li className="timeline-item" key={i}>
            <span className="timeline-dot" aria-hidden="true">
              {iconName ? <Icon name={iconName} size={13} /> : null}
            </span>
            <div className="timeline-body">
              <div className="timeline-head">
                <span className="timeline-title">{String(r[panel.title_field] ?? "")}</span>
                {badgeVal !== undefined && badgeVal !== null && String(badgeVal) !== "" && (
                  <span className={`badge ${badgeClass(badgeColorFor(badgeVal, panel.badge_colors))}`}>
                    {String(badgeVal)}
                  </span>
                )}
                <span className="timeline-date">{fmtDate(r[panel.date_field])}</span>
              </div>
              {panel.subtitle_field && r[panel.subtitle_field] != null && (
                <div className="timeline-sub">{String(r[panel.subtitle_field])}</div>
              )}
            </div>
          </li>
        );
      })}
      {data?.truncated && (
        <li className="timeline-item timeline-truncated">
          Showing the {rows.length} most recent — more exist.
        </li>
      )}
    </ol>
  );
}
