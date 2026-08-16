# citra-app-ui-design — Composing a UI app with triggers + dashboard + chat

Read this when the BA mentions a **chat panel, dashboard/KPI tiles, charts,
multiple dashboards, or automatic (trigger) runs** during the question script.
UI apps (`kind="app"`) are the only kind that can host all three Citra surfaces
in one experience.

**Chat panel inside a UI app.** Two ways:
- **Per-page chat** — drop an `agent_chat` panel into a single page (typical: the detail page so the BA can ask "what's this claim's risk?"). Set `agent_role` to a sub-agent if the agent_spec defines one.
- **Global floating chat** — set `navigation.show_chat_globally: true`. The runtime renders the chat as a persistent panel across every page. Use this when the agent's free-form Q&A is useful regardless of context (e.g. policy lookup, "explain this field").

**Dashboard tiles inside a UI app.** The `dashboard` panel (KPI cards) and `chart` panel are first-class on any page. Drop them on any page that summarises data. Typical placement:
- A `dashboard` panel at the top of the inbox page showing throughput / SLA-breach counts.
- A `chart` panel under it visualising the same metric over time.
- The auto-chart injector (Phase 3.5 / `citra-app-spec`) will add a chart automatically when a queue has numeric columns — you don't have to design it, but you do have to tell the BA *which page* it lands on.

**Dashboard pages — one or many.** When the BA wants an executive KPI/chart surface (a CMD briefing, an exec overview), make it a page with `kind: "dashboard"`. That page gets the executive ECharts theme + the automatic hero-brief copilot at the top. It holds only KPI/chart/markdown panels; queues, documents, forms, and a dedicated assistant go on **standard** pages of the same app. Author dashboard pages with `citra-dashboard-spec`; author the standard pages as usual. A "dashboard" the BA asks for in isolation is simply an app with a single `kind: "dashboard"` page.

**There is no cap on pages, and no "only one dashboard" rule.** An app can have **multiple dashboard pages** (e.g. "Operations", "Finance", "Compliance" — each its own `kind: "dashboard"` page with its own KPIs/charts and its own page-scoped hero brief) **and multiple standard pages** (e.g. several operations/queue pages, document pages, form pages). Design exactly what the BA describes — if they list five distinct views, that's five pages. Don't collapse distinct dashboards onto one page, and don't put queues/forms on a dashboard page. List every page in the nav; pick the landing page via `navigation.default_page`.

**Automation inside a UI app = AI triggers + the recommendation inbox.** When the BA wants the agent to run automatically (triage / classify / score / precompute), add an **app trigger** (`app_spec.triggers[]` — schedule/webhook/poll) and surface the results as a **`workflow_staging` queue panel** (the officer inbox) with a detail page that shows the recommendation + Approve/Reject. (Direct, deterministic writes the user clicks are a separate `tool_button` → see citra-app-spec "Two write paths".)

If the BA asks for something the platform doesn't support today, record it in `requirements_unmet` and tell them in one sentence — don't invent.
