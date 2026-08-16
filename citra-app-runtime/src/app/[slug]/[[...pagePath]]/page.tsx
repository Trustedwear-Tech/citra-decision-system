import { notFound } from "next/navigation";
import { cookies } from "next/headers";
import { fetchAppDetail, UNAUTHORIZED } from "@/lib/specClient";
import { resolvePage, listPages, isMultiPage } from "@/lib/pages";
import AppShell from "@/components/AppShell";
import PageBody from "@/components/PageBody";
import EmbedPageNotice from "@/components/EmbedPageNotice";
import LocaleSetter from "@/components/LocaleSetter";
import TokenCapture from "@/components/TokenCapture";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ slug: string; pagePath?: string[] }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

/** Distinct per-app browser-tab title (was a single hardcoded global, so every
 *  open app tab read "Citra Power AI App" — indistinguishable bookmarks/history
 *  and no context for screen readers). Slug-derived to stay zero-fetch. */
export async function generateMetadata(props: PageProps) {
  const params = await props.params;
  const name = params.slug
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return { title: `${name} · Citra` };
}

export default async function AppPage(props: PageProps) {
  const searchParams = await props.searchParams;
  const params = await props.params;
  // The end-user JWT is handed off from Citra-UI as ?_t= on launch and mirrored
  // into a per-tab cookie (userToken.ts) so a full reload's SSR — which can't
  // read sessionStorage — still has it. SSR authorizes with this REAL user
  // token (validated by smart-app-service with the shared JWT_SECRET); there is
  // no service/god token. No token → smart-app-service 404s → notFound().
  const tokenParam = searchParams?._t;
  const tFromQuery = Array.isArray(tokenParam) ? tokenParam[0] : tokenParam;
  const tFromCookie = (await cookies()).get("citra_user_token")?.value;
  const userToken = tFromQuery || tFromCookie || null;
  const detail = await fetchAppDetail(params.slug, userToken);
  if (detail === UNAUTHORIZED) {
    // No/expired handoff token reached SSR (e.g. a stale tab after browser
    // restart, or a deep link without ?_t=). A clear, recoverable state —
    // never the generic error boundary.
    return (
      <main className="app-shell">
        <div className="panel-error" role="alert" style={{ margin: "15vh auto", maxWidth: 460 }}>
          <strong>Your session is missing or has expired.</strong>
          <span>
            Reopen this app from Citra to start a fresh session — app links
            only carry access when launched from the Citra workspace.
          </span>
        </div>
      </main>
    );
  }
  if (!detail) notFound();

  const { app_spec, agent_spec, environment } = detail;

  // Dashboards ALWAYS render native panels now (Superset embedding removed).
  // The hero-brief copilot is injected below the header by AppShell when
  // kind='dashboard' && agent_id is present.

  const page = resolvePage(app_spec, params.pagePath);
  if (!page) notFound();

  // Collapse repeated query params to first value (panels expect string|undefined).
  const queryParams: Record<string, string> = {};
  for (const [k, v] of Object.entries(searchParams)) {
    if (Array.isArray(v)) queryParams[k] = v[0] ?? "";
    else if (typeof v === "string") queryParams[k] = v;
  }

  // Per-tenant brand: primary + (optional) accent flow into CSS custom props
  // that the whole runtime stylesheet is built on. Theme v2 tokens
  // (font/radius/density/surface/mode) map to CSS vars + data attributes here
  // — this is the ONLY place spec → token translation happens.
  const theme = app_spec.theme;
  const themeVars: Record<string, string> = {};
  if (theme?.primary) themeVars["--citra-primary"] = theme.primary;
  if (theme?.accent) themeVars["--citra-accent"] = theme.accent;
  // Bundle-safe font STACKS (no external font fetch — a customer-installed
  // Inter/Plex is used when present, else the platform stack).
  const FONT_STACKS: Record<string, string> = {
    inter: '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
    "source-sans":
      '"Source Sans 3", "Source Sans Pro", "Segoe UI", system-ui, sans-serif',
    "ibm-plex": '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
    system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  };
  if (theme?.font && FONT_STACKS[theme.font]) {
    themeVars["--citra-font"] = FONT_STACKS[theme.font];
  }
  const themeStyle = Object.keys(themeVars).length
    ? (themeVars as React.CSSProperties)
    : undefined;
  // mode supersedes the legacy dark_mode bool; "auto" defers to the OS via
  // the media query paired with [data-mode="auto"] in globals.css.
  const colorMode = theme?.mode ?? (theme?.dark_mode ? "dark" : undefined);

  const pages = listPages(app_spec);
  const showNav = isMultiPage(app_spec) && app_spec.navigation?.style !== "none";

  return (
    <main
      className="app-shell"
      style={themeStyle}
      data-radius={theme?.radius}
      data-density={theme?.density}
      data-surface={theme?.surface}
      data-mode={colorMode}
    >
      {/* Mirror ?_t= into the SSR cookie BEFORE any interaction can navigate —
          a navigate-only first action must not out-run the token capture. */}
      <TokenCapture />
      <LocaleSetter
        locale={theme?.locale}
        currency={theme?.currency}
        title={
          theme?.company_name
            ? `${app_spec.title} · ${theme.company_name}`
            : undefined
        }
        chartPalette={theme?.chart_palette}
        primary={theme?.primary}
      />
      <AppShell
        appSpec={app_spec}
        pages={pages}
        currentPageId={page.id}
        showNav={showNav}
        environment={environment}
      >
        {/* An EMBED page opened directly, with no record, has nothing to show:
            its queue filters on the host-supplied id and its detail resolves by
            that id, so both come back empty and the app looks broken. Explain
            it instead. With ?id= present this is a legitimate preview, so the
            panels render normally. */}
        {page.kind === "embed" && !queryParams.id && !queryParams.record_id ? (
          <EmbedPageNotice title={app_spec.title} slug={params.slug} />
        ) : (
          <PageBody
            appSpec={app_spec}
            agentSpec={agent_spec}
            page={page}
            pageParams={queryParams}
            slug={params.slug}
          />
        )}
      </AppShell>
    </main>
  );
}
