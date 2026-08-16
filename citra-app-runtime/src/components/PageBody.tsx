"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import type { AgentSpec, AppSpec, Page, Panel, PageKind } from "@/types/spec";
import PanelRenderer from "@/components/PanelRenderer";
import Icon from "@/components/Icon";

interface Props {
  appSpec: AppSpec;
  agentSpec: AgentSpec;
  page: Page;
  pageParams: Record<string, string>;
  slug: string;
}

/**
 * Renders the panels of a single page, applying the layout option declared
 * on the page (grid / stack / split / tabs).
 */
export default function PageBody({
  appSpec,
  agentSpec,
  page,
  pageParams,
  slug,
}: Props) {
  // The LIVE URL params win over the server-passed props: on a client-side
  // navigation to the same route with different search params (queue row A →
  // back → row B), Next's router cache can replay a stale RSC payload, so the
  // prop still carries row A's ?id= — the detail panel then silently renders
  // the wrong record. useSearchParams always reflects the current URL and
  // re-renders on change, so merging it on top makes every panel's params
  // (detail ?id=, filter_bar selections) follow what the address bar shows.
  const liveSearch = useSearchParams();
  const liveParams: Record<string, string> = {};
  liveSearch?.forEach((v, k) => {
    liveParams[k] = v;
  });
  const effectiveParams: Record<string, string> = {
    ...pageParams,
    ...liveParams,
  };

  const layout = page.layout ?? "grid";
  const pageKind = page.kind ?? "standard";
  const isDashboard = pageKind === "dashboard";
  const panels = page.panels;

  return (
    <section
      className={`app-page app-page-${layout}${isDashboard ? " app-page-dashboard" : ""}`}
      data-page-id={page.id}
      data-page-kind={pageKind}
    >
      {/* A hero panel IS the page header — suppress the plain title so the
          page doesn't say its name twice. */}
      {page.title && !panels.some((p) => p.type === "hero") && (
        <h2 className="app-page-title">
          <Icon name={page.icon} size={17} className="app-page-title-icon" />
          {page.title}
        </h2>
      )}

      {layout === "tabs" ? (
        <TabsLayout
          panels={panels}
          app={appSpec}
          agent={agentSpec}
          slug={slug}
          pageParams={effectiveParams}
          pageKind={pageKind}
        />
      ) : (
        <div className={`panel-host panel-host-${layout}`}>
          {panels.map((panel, idx) => (
            <section
              key={panel.id}
              className={panelLayoutClass(layout, panel, idx, isDashboard)}
            >
              {panel.title && panel.type !== "hero" && (
                <div className="panel-title">
                  <Icon name={panel.icon} size={13} className="panel-title-icon" />
                  {panel.title}
                </div>
              )}
              <PanelRenderer
                panel={panel}
                app={appSpec}
                agent={agentSpec}
                slug={slug}
                pageParams={effectiveParams}
                pageKind={pageKind}
              />
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function panelLayoutClass(
  layout: string,
  panel: Panel,
  _idx: number,
  isDashboard = false,
): string {
  // Designed bands render full-width WITHOUT the card chrome (they carry
  // their own), regardless of layout.
  if (panel.type === "hero") return "panel-hero-host";
  if (panel.type === "stat_strip") return "panel panel-statstrip";
  // Stack: one column, every panel full-width.
  if (layout === "stack") return "panel panel-stack";
  // Split = two equal 50% columns; every panel is span-6. (Was a no-op ternary
  // whose two branches were identical — simplified; behavior unchanged.)
  if (layout === "split") return "panel span-6";
  // Dashboard page (grid): KPI row full-width, charts in a 2-up grid.
  if (isDashboard) {
    if (panel.type === "dashboard") return "panel"; // KPI strip, full width
    if (panel.type === "chart") return "panel span-6";
    if (panel.type === "markdown") return "panel"; // brief / note, full width
    return "panel";
  }
  // Grid (default): dashboards full-width, chart/form/chat half-width.
  if (panel.type === "dashboard" || panel.type === "detail") return "panel";
  if (panel.type === "chart") return "panel span-6";
  if (panel.type === "form" || panel.type === "agent_chat") return "panel span-6";
  return "panel";
}

function TabsLayout({
  panels,
  app,
  agent,
  slug,
  pageParams,
  pageKind,
}: {
  panels: Panel[];
  app: AppSpec;
  agent: AgentSpec;
  slug: string;
  pageParams: Record<string, string>;
  pageKind: PageKind;
}) {
  const [active, setActive] = useState(panels[0]?.id ?? "");
  const current = panels.find((p) => p.id === active) ?? panels[0];
  return (
    <div className="panel-tabs">
      <div className="panel-tab-bar" role="tablist">
        {panels.map((p) => (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={p.id === active}
            className={`panel-tab${p.id === active ? " active" : ""}`}
            onClick={() => setActive(p.id)}
          >
            {p.title ?? p.id}
          </button>
        ))}
      </div>
      {current && (
        <section className="panel">
          <PanelRenderer
            panel={current}
            app={app}
            agent={agent}
            slug={slug}
            pageParams={pageParams}
            pageKind={pageKind}
          />
        </section>
      )}
    </div>
  );
}
