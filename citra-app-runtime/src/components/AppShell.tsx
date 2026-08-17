// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

import Link from "next/link";
import type { AppSpec, Page } from "@/types/spec";
import { pageHref } from "@/lib/pages";
import Icon from "@/components/Icon";
import HeroBriefCopilot from "@/components/HeroBriefCopilot";
import TestEnvironmentBanner from "@/components/TestEnvironmentBanner";

interface Props {
  appSpec: AppSpec;
  pages: Page[];
  currentPageId: string;
  showNav: boolean;
  /** "test" → render the TEST banner above the header. Absent → prod. */
  environment?: "test" | "prod";
  children: React.ReactNode;
}

/**
 * Renders the app header + (optional) page navigation, with the page body
 * slotted as children. Server component — relies on Next.js <Link> for
 * client-side transitions without a client component boundary.
 */
export default function AppShell({
  appSpec,
  pages,
  currentPageId,
  showNav,
  environment,
  children,
}: Props) {
  const navStyle = appSpec.navigation?.style ?? "sidebar";
  const visiblePages = pages.filter((p) => !p.hide_in_nav);
  // The hero-brief copilot tops every DASHBOARD PAGE that has a narrator
  // agent. It follows the page, not the app: an app shows the brief only on
  // pages whose kind === 'dashboard' (it replaces the inline agent_chat panel
  // there). The brief runs the app agent in read-only chat_mode.
  const currentPage = pages.find((p) => p.id === currentPageId);
  const showHeroBrief =
    currentPage?.kind === "dashboard" && !!appSpec.agent_id;
  const status = appSpec.status ?? "draft";

  return (
    <>
      {environment === "test" && <TestEnvironmentBanner />}
      <header className="app-header">
        <div className="app-brand">
          {appSpec.theme?.logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              className="app-logo"
              src={appSpec.theme.logo_url}
              alt={appSpec.theme?.company_name ?? ""}
            />
          ) : (
            <span className="app-logo-mono" aria-hidden="true">
              {(appSpec.theme?.company_name || appSpec.title || "?")
                .trim()
                .charAt(0)
                .toUpperCase()}
            </span>
          )}
          <div style={{ minWidth: 0 }}>
            {/* Company identity (Theme v2, inherited from the ontology's
                organization block at publish): "Acme Power · Recovery Tracker"
                instead of a bare app name. */}
            {appSpec.theme?.company_name ? (
              <div className="app-title">
                <span className="app-company">{appSpec.theme.company_name}</span>
                <span className="app-title-sep" aria-hidden="true">
                  ·
                </span>
                {appSpec.title}
              </div>
            ) : (
              <div className="app-title">{appSpec.title}</div>
            )}
            {appSpec.description && (
              <div className="app-desc">{appSpec.description}</div>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="chip">v{appSpec.version ?? 1}</span>
          <span
            className={`chip${
              status === "published"
                ? " chip-published"
                : status === "draft"
                ? " chip-draft"
                : ""
            }`}
          >
            {status}
          </span>
        </div>
      </header>

      {showNav && navStyle === "topbar" && (
        <nav className="app-topbar">
          {visiblePages.map((p) => (
            <Link
              key={p.id}
              href={pageHref(appSpec.slug, p)}
              className={`app-nav-item${p.id === currentPageId ? " active" : ""}`}
            >
              <Icon name={p.icon} size={14} className="app-nav-icon" />
              {p.title ?? p.id}
            </Link>
          ))}
        </nav>
      )}

      <div className={showNav && navStyle === "sidebar" ? "app-layout-with-nav" : "app-layout"}>
        {showNav && navStyle === "sidebar" && (
          <aside className="app-sidebar">
            <ul>
              {visiblePages.map((p) => (
                <li key={p.id}>
                  <Link
                    href={pageHref(appSpec.slug, p)}
                    className={`app-nav-item${p.id === currentPageId ? " active" : ""}`}
                  >
                    <Icon name={p.icon} size={14} className="app-nav-icon" />
                    {p.title ?? p.id}
                  </Link>
                </li>
              ))}
            </ul>
          </aside>
        )}
        <div className="app-page-host">
          {showHeroBrief && currentPage && (
            <HeroBriefCopilot
              slug={appSpec.slug}
              pageId={currentPage.id}
              pageTitle={currentPage.title ?? currentPage.id}
            />
          )}
          {children}
        </div>
      </div>
    </>
  );
}
