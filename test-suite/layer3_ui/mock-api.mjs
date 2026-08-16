// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

/**
 * Mock smart-app API for the UI rendering matrix.
 *
 * Serves EXACTLY the endpoints the runtime calls — GET /apps/{slug},
 * GET /apps/{slug}/data/{panel}, /detail, /field-options, /media — returning a
 * fixture APP SPEC (one panel type at a time) and a chosen DATA STATE. The state
 * is selected per request via the `x-fixture-state` header (set by the Playwright
 * test), so ONE runtime instance renders every (panel × state) cell.
 *
 * Point the runtime's server-side API base at this server, e.g.
 *   SMART_APP_SERVICE_URL=http://localhost:8899 next start
 * Dependency-free (Node http).
 */
import http from "node:http";
import { fixtureSpec, dataForState } from "./fixtures.mjs";

const PORT = process.env.MOCK_API_PORT || 8899;

function json(res, code, body) {
  const s = JSON.stringify(body);
  res.writeHead(code, { "content-type": "application/json", "content-length": Buffer.byteLength(s) });
  res.end(s);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://x`);
  const parts = url.pathname.split("/").filter(Boolean); // ["apps", slug, ...]
  // State rides in the SLUG (`fx-<panel>-<state>`) so it survives the runtime's
  // server-side fetch — a browser header would not propagate through SSR.
  const slug = parts[1] || "";
  const m = /^fx-([a-z_]+)-([a-z_]+)$/.exec(slug);
  const panelType = m ? m[1] : (req.headers["x-fixture-panel"] || "queue");
  const state = m ? m[2] : (req.headers["x-fixture-state"] || "single_row");

  // GET /apps/{slug}
  if (parts[0] === "apps" && parts.length === 2 && req.method === "GET") {
    // "authnav" slugs mimic smart-app-service's auth: the spec fetch REQUIRES a
    // user token. Proves the runtime mirrors ?_t= into the SSR cookie before
    // the first navigation (the lazy-capture bug found in prod).
    if (state === "authnav" && req.headers["authorization"] !== "Bearer e2e-token") {
      return json(res, 401, { detail: "401 missing user token" });
    }
    return json(res, 200, { app_spec: fixtureSpec(panelType, state), agent_spec: null });
  }
  // GET /apps/{slug}/data/{panel}
  if (parts[0] === "apps" && parts[2] === "data" && req.method === "GET") {
    const { code, body } = dataForState(panelType, state);
    return json(res, code, body);
  }
  // GET /apps/{slug}/detail/{panel}
  if (parts[0] === "apps" && parts[2] === "detail") {
    const { code, body } = dataForState("detail", state);
    return json(res, code, body);
  }
  // GET /apps/{slug}/document/{dsId} — the document_view panel's data source
  if (parts[0] === "apps" && parts[2] === "document") {
    const { code, body } = dataForState(panelType, state);
    if (code !== 200) return json(res, code, body);         // surface error states
    return json(res, 200, { documents: [{ id: "DOC-1", title: "Policy.pdf",
      url: `/apps/${slug}/media/ds`, mime: "application/pdf" }] });
  }
  // POST /apps/{slug}/field-options
  if (parts[0] === "apps" && parts[2] === "field-options") {
    return json(res, 200, { options: [{ value: "a", label: "Alpha" }] });
  }
  // media — tiny 1x1 png
  if (parts[0] === "apps" && parts[2] === "media") {
    const png = Buffer.from("89504e470d0a1a0a0000000d494844520000000100000001080600000" +
      "01f15c4890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082", "hex");
    res.writeHead(200, { "content-type": "image/png", "content-disposition": "inline; filename=\"x.png\"" });
    return res.end(png);
  }
  // POST /apps/{slug}/tool/{tool} — direct form submit (no LLM). Success -> "Saved."
  if (parts[0] === "apps" && parts[2] === "tool") {
    return json(res, 200, { result: { ok: true, id: "INS-1" } });
  }
  // POST /apps/{slug}/chat — agent_chat. Plain JSON (readAgentStream's non-SSE
  // fallback parses it and reads .reply).
  if (parts[0] === "apps" && parts[2] === "chat") {
    return json(res, 200, { reply: "Grounded answer: 42 inspections are open this week." });
  }
  // GET /apps/{slug}/notifications/{panelId} — notification feed (clickable items).
  if (parts[0] === "apps" && parts[2] === "notifications") {
    return json(res, 200, { notifications: [
      { id: "N1", correlation_id: "N1", label: "Approval", tone: "warning", title: "Inspection INS-1 needs review",
        sub: "Risk 85", navigate: { page: "pg", params: { id: "{row.id}" } }, row: { id: "INS-1" } }] });
  }
  // POST /apps/{slug}/run/{cid}/approve — APPLY/reject a decision. Returns the
  // committed result (status=completed + write_events) so the modal shows
  // "Applied changes"; the detail section only checks res.ok.
  if (parts[0] === "apps" && parts.includes("approve")) {
    return json(res, 200, { correlation_id: "run-1", status: "completed", decision: "approve",
      write_events: [{ status: "ok", action_id: "set_outcome", dataset_id: "ds" }] });
  }
  // POST /apps/{slug}/run — a fresh recommendation (plan-then-apply).
  if (parts[0] === "apps" && parts[2] === "run") {
    return json(res, 200, { correlation_id: "run-1", status: "pending_approval",
      decision: "Fail", reasoning: "duplicate photo across 3 inspections",
      planned_writes: [{ dataset_id: "ds", action_id: "set_outcome", payload: { outcome: "Fail" },
        editable_fields: ["outcome"] }] });
  }
  json(res, 404, { detail: `mock: no route ${req.method} ${url.pathname}` });
});

server.listen(PORT, () => console.log(`[mock-api] listening on :${PORT}`));
