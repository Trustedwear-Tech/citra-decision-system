// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Fixture AppSpecs (one per panel type) + the data returned for each state.
 * The rendering matrix renders each panel type in every data state; the negative
 * states (source_error, unauthorized, non_columnar) are the fail-loud cells — a
 * broken/empty panel must NEVER render silently blank.
 */

// QueuePanel.columns is string[] (column NAMES), not {name,type} objects — the
// runtime does row[col] / col.toLowerCase() on these, so passing objects crashes
// the client render. Match the real spec contract.
const COLUMNS = ["id", "status", "amount"];

function page(panel) {
  return { spec_version: "v0", kind: "app", title: `Fixture ${panel.type}`, audience: "org",
    data_sources: [{ id: "ds", type: "mcp", ref: "src.dataset", filters: {} }],
    pages: [{ id: "pg", kind: "standard", panels: [panel] }] };
}

// The editable planned-write a queue recommendation carries — drives override.
const REC_EDITABLE = [
  { name: "outcome", label: "Outcome", control: "select",
    options: { kind: "static", values: [
      { value: "Pass", label: "Pass" }, { value: "Repair", label: "Repair" }, { value: "Fail", label: "Fail" }] } },
  { name: "note", label: "Note", control: "text" },
];

/** A minimal, valid single-panel spec for the given panel type. `state` lets a
 *  few interaction slugs (e.g. queue "rec") vary the spec. */
export function fixtureSpec(panelType, state) {
  switch (panelType) {
    // "rec": a queue WITHOUT a row-click navigate action, so clicking a card that
    // carries `_recommendation` opens the RunResultModal (the override surface)
    // instead of navigating.
    case "queue":
      if (state === "rec")
        return page({ id: "p", type: "queue", title: "Queue", data_source: "ds", columns: COLUMNS });
      // A row-click action makes rows clickable (navigate); enables click_row/navigate cells.
      return page({ id: "p", type: "queue", title: "Queue", data_source: "ds", columns: COLUMNS,
        actions: [{ id: "open", label: "Open", is_row_click: true,
          navigate: { page: "pg", params: { id: "{row.id}" } } }] });
    case "detail": return page({ id: "p", type: "detail", title: "Detail", linked_to: "p",
      sections: [{ type: "fields", title: "Fields", fields: ["id", "status", "amount"] }] });
    // on_submit carries a direct tool (deterministic "Saved.") AND a navigate —
    // exercises both the submit and navigate cells.
    // "authnav": NAVIGATE-ONLY submit (no tool call before leaving the page) —
    // the exact shape that out-ran the lazy token capture in prod. The mock
    // requires auth on this slug's spec fetch, so the test proves the ?_t=
    // token is mirrored into the SSR cookie BEFORE the first navigation.
    case "form":
      if (state === "authnav")
        return page({ id: "p", type: "form", title: "Form",
          schema_inline: { type: "object", required: ["id"], properties: { id: { type: "string", title: "ID" } } },
          on_submit: { navigate: { page: "pg", params: { id: "{form.id}" } } } });
      return page({ id: "p", type: "form", title: "Form",
      schema_inline: { type: "object", required: ["id"], properties: { id: { type: "string", title: "ID" } } },
      on_submit: { tool_name: "save_intake", navigate: { page: "pg", params: { id: "{form.id}" } } } });
    case "chart": return page({ id: "p", type: "chart", title: "Chart", data_source: "ds",
      chart_type: "bar", x: "status", y: "amount" });
    case "markdown": return page({ id: "p", type: "markdown", title: "MD", content: "# Hello\n\ntext" });
    case "notice": return page({ id: "p", type: "notice", title: "Notice", content: "Heads up." });
    case "notifications": return page({ id: "p", type: "notifications", title: "Alerts" });
    case "document_view": return page({ id: "p", type: "document_view", title: "Docs", data_source: "ds" });
    case "agent_chat": return page({ id: "p", type: "agent_chat", title: "Copilot", agent_role: "narrator" });
    // DashboardPanel: metrics[] with a data_source each → fetches /data, renders .kpi tiles.
    case "dashboard": return page({ id: "p", type: "dashboard", title: "KPIs",
      metrics: [{ name: "total", data_source: "ds", agg: "count", label: "Total" },
                { name: "open", data_source: "ds", agg: "count", label: "Open" }] });
    // CalendarPanel: date_field/title_field over rows.
    case "calendar": return page({ id: "p", type: "calendar", title: "Calendar", data_source: "ds",
      date_field: "due", title_field: "name" });
    // MapPanel: lat_field/lng_field over rows (LeafletMap, ssr:false).
    case "map": return page({ id: "p", type: "map", title: "Map", data_source: "ds",
      lat_field: "lat", lng_field: "lng", label_field: "name" });
    // FilterBarPanel: controls[]; the bar itself fetches no panel data — it drives
    // URL params consumed by sibling panels.
    case "filter_bar": return page({ id: "p", type: "filter_bar", title: "Filters",
      controls: [{ param: "status", label: "Status", data_source: "ds", field: "status" }] });
    default: return page({ id: "p", type: panelType, title: panelType, data_source: "ds", columns: COLUMNS });
  }
}

// Panel-appropriate row shape — a calendar needs a date, a map needs coords, a
// document list needs a title, or the panel legitimately renders "nothing to show".
function rowFor(panelType, i = 1) {
  const base = { id: `INS-${i}`, status: ["open", "repair", "closed"][i % 3], amount: 1000 + i };
  if (panelType === "calendar") return { ...base, name: `Inspection ${i}`, due: `2026-07-${String((i % 27) + 1).padStart(2, "0")}` };
  if (panelType === "map") return { ...base, name: `Site ${i}`, lat: 25.6 + i * 0.01, lng: 85.1 + i * 0.01 };
  if (panelType === "document_view") return { id: `DOC-${i}`, title: `Policy ${i}.pdf`, summary: `Section ${i} summary`, doc_type: "policy" };
  return base;
}

// A detail record + the governed-decision sections (fields, attachment media,
// an editable pending approval, the run timeline) — drives approve/override/
// reject/media_open. record.photo is an opaque ref → runtime builds an /api/media URL.
function detailData() {
  const record = { id: "INS-1", status: "pending_review", amount: 1200, photo: "dms://INS-1/photo.jpg" };
  return { code: 200, body: {
    panel_id: "p", linked_to: "p", record_id: "INS-1", record, record_columns: Object.keys(record),
    sections: [
      { type: "fields", title: "Fields", fields: ["id", "status", "amount"] },
      { type: "attachment", title: "Media", fields: ["photo"], data_source: "ds", key_field: "id" },
      { type: "approval", roles: ["inspector"], pending: [{
        correlation_id: "run-1", action: "set_outcome", requested_by: "system",
        decision: "Fail", reasoning: "Duplicate defect photo across 3 inspections.",
        plan_hash: "hash-1",
        planned_writes: [{ action_id: "set_outcome", dataset_id: "ds",
          payload: { outcome: "Fail", note: "" },
          editable_fields: [
            { name: "outcome", label: "Outcome", control: "select",
              options: { kind: "static", values: [
                { value: "Pass", label: "Pass" }, { value: "Repair", label: "Repair" }, { value: "Fail", label: "Fail" }] } },
            { name: "note", label: "Note", control: "text" },
          ] }] }] },
      { type: "agent_timeline", runs: [{ correlation_id: "run-0", action: "analyze",
        status: "completed", decision: "Fail", reasoning: "Photo reused.", created_at: "2026-07-04T10:00:00Z" }] },
    ] } };
}

/** The runtime PanelDataResponse for a given state. Returns {code, body}. */
export function dataForState(panelType, state) {
  const row = rowFor(panelType);
  switch (state) {
    case "rec":   // a queue row carrying a staged recommendation (override surface)
      return { code: 200, body: { total: 1, truncated: false, rows: [{
        id: "INS-1", status: "repair", amount: 1200,
        _recommendation: { status: "pending_approval", decision: "Fail",
          reasoning: "Duplicate defect photo across 3 inspections.",
          correlation_id: "run-1", plan_hash: "hash-1",
          planned_writes: [{ action_id: "set_outcome", dataset_id: "ds",
            payload: { outcome: "Fail", note: "" }, editable_fields: REC_EDITABLE }] } }] } };
    case "loading": return { code: 200, body: { rows: [], _loading: true } };
    case "empty": return { code: 200, body: { rows: [], total: 0, truncated: false } };
    case "single_row":
      if (panelType === "detail") return detailData();
      return { code: 200, body: { rows: [row], total: 1, truncated: false } };
    case "many_rows":
      return { code: 200, body: { rows: Array.from({ length: 25 }, (_, i) => rowFor(panelType, i)),
        total: 25, truncated: false } };
    case "truncated":
      return { code: 200, body: { rows: Array.from({ length: 50 }, (_, i) => rowFor(panelType, i)),
        total: 500, truncated: true } };
    case "source_error":
      // fail-loud: the runtime must show an ERROR, not an empty panel
      return { code: 502, body: { detail: "mcp src (sql) returned 500: connection refused" } };
    case "unauthorized":
      return { code: 403, body: { detail: "not permitted" } };
    case "non_columnar":
      // A REST single-object response reaching a columnar panel. smart-app-service
      // (panel_data.py `_is_unrenderable_object`) REJECTS this before it reaches the
      // client, so the runtime receives an error envelope and must fail loud — it is
      // never handed a raw {credit_score,...} object to silently drop.
      return { code: 422, body: { detail: "panel data is not columnar: expected rows, got a single object {credit_score, status, count}" } };
    default:
      return { code: 200, body: { rows: [row], total: 1 } };
  }
}
