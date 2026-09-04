// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * SmartAppService.js — client for smart-app-service (Smart Apps).
 *
 * Calls smart-app-service directly using urls.smartAppService from config.
 * In dev: http://localhost:9100. In prod: routed through the API gateway.
 *
 * All routes except /health and /publish require a Citra user JWT, so we
 * use authService.authenticatedFetch — the same wrapper DiscoveryService /
 * other Citra-UI services use.
 */

import { CONFIG } from '../config/config';
import authService from './authService';

class SmartAppService {
  constructor() {
    this.baseURL = CONFIG.urls.smartAppService;
    this.appsBaseURL = CONFIG.urls.appsBaseUrl;
    this.defaultTimeout = 15000;
  }

  appUrl(slug) {
    if (!slug) return this.appsBaseURL;
    return `${this.appsBaseURL.replace(/\/$/, '')}/${encodeURIComponent(slug)}`;
  }

  async _fetch(path, options = {}) {
    const controller = new AbortController();
    let timedOut = false;
    // A timed-out request used to surface as the browser's raw abort
    // exception -- "signal is aborted without reason" -- which names neither
    // the request nor the timeout, and reads like a bug rather than a slow
    // backend. The flag distinguishes OUR timer from any other abort, so a
    // genuine cancellation is still reported as itself.
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, this.defaultTimeout);
    try {
      const res = await authService.authenticatedFetch(`${this.baseURL}${path}`, {
        ...options,
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          ...(options.body ? { 'Content-Type': 'application/json' } : {}),
          ...(options.headers || {}),
        },
      });
      const text = await res.text();
      const body = text ? JSON.parse(text) : null;
      if (!res.ok) {
        const detail = body && (body.detail || body.error || body.message);
        throw new Error(detail || `${path} → ${res.status}`);
      }
      return body;
    } catch (e) {
      if (timedOut) {
        throw new Error(
          `${path} did not respond within ${Math.round(this.defaultTimeout / 1000)}s`,
        );
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  // ── apps ──────────────────────────────────────────────────────────
  /**
   * List apps under one of five scopes (matches smart-app-service
   * GET /apps): 'all' | 'mine' | 'shared' | 'admin' | 'test'.
   *
   *   'all'    — everything visible via owner-SA OR audience match. UI
   *              groups locally by app_spec.audience to render the
   *              Mine / Team / Department / Organization sections.
   *   'mine'   — owner-SA membership only (apps the caller can edit).
   *   'shared' — visible but NOT through owner-SA.
   *   'admin'  — admin lens (dept_admin / org_admin / super_admin).
   *   'test'   — the caller's own apps in the TEST environment (test_
   *              store), awaiting Promote to Prod. Empty when no test
   *              environment is configured.
   *
   * @param {{ scope?: 'all'|'mine'|'shared'|'admin'|'test',
   *           includeArchived?: boolean,
   *           kind?: 'app'|'dashboard' }} [opts]
   */
  async listApps({ scope = 'all', includeArchived = false, kind } = {}) {
    const params = new URLSearchParams();
    params.set('scope', scope);
    // Server default is 100 but the UI derives its tab/chip counts from the
    // returned rows — ask for the server max so counts match `total`.
    params.set('limit', '500');
    if (includeArchived) params.set('include_archived', 'true');
    if (kind) params.set('kind', kind);
    return this._fetch(`/apps?${params.toString()}`);
  }

  /**
   * Promote a TEST app to production — copies the spec from the test_ store
   * to the prod store (smart-app-service POST /apps/{slug}/promote-to-prod).
   * The BA picks the prod audience here; omit to carry over the test value.
   *
   * @param {string} slug
   * @param {{ audience?: string }} [opts]  audience: owner|org|team:<id>|dept:<id>
   */
  // Promote a test app to prod. For a GROUNDED app the server requires the
  // few-shot memory to be fresh: pass refresh_grounding:true to refresh it as
  // part of promote (response carries grounding_refresh_run_id to poll), or
  // promote_ungrounded:true to ship without it. Omitting both on a never/stale
  // grounded app returns 409 { code:'grounding_refresh_required' }.
  async promoteToProd(slug, { audience, refresh_grounding, promote_ungrounded } = {}) {
    const body = {};
    if (audience) body.audience = audience;
    if (refresh_grounding) body.refresh_grounding = true;
    if (promote_ungrounded) body.promote_ungrounded = true;
    return this._fetch(`/apps/${encodeURIComponent(slug)}/promote-to-prod`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  // ── Kill switches ──────────────────────────────────────────────────────
  // Active halts/pauses (global/org/dept/app) — drives the banner + red button.
  async listHalt() {
    return this._fetch('/admin/halt');
  }
  // Set/clear a halt at global|org|dept scope (the RED BUTTON + dept/org freeze).
  async setHalt({ scopeType, scopeId = null, enabled, reason = '' }) {
    return this._fetch('/admin/halt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope_type: scopeType, scope_id: scopeId, enabled, reason }),
    });
  }
  // Pause / resume ONE app (stops runs/writes/automation, keeps reads+audit).
  async pauseApp(slug, reason = '') {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  }
  async resumeApp(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/resume`, { method: 'POST' });
  }

  // Fleet control-panel: every scheduled/triggered app in the admin's scope,
  // with each trigger's mode, enabled, schedule, next_run + last_run, per-app
  // stats and an incidents feed.
  async listAutomation() {
    return this._fetch('/admin/automation');
  }

  // ── Learning batch (clause consolidation) ──────────────────────────────
  // The job that folds officer corrections into learned clauses. It replaced a
  // synchronous per-reject summarizer that ran inside the officer's approve
  // request, so it needs its own operator surface. Separate from the kill
  // switches: pausing this stops LEARNING, not operations.
  async consolidationStatus() {
    return this._fetch('/admin/consolidation');
  }
  // Pausing is lossless — corrections keep queuing and fold on the next
  // unpaused pass. Nothing an officer does changes.
  async setConsolidationPaused(paused, reason = '') {
    return this._fetch(`/admin/consolidation/pause?paused=${paused ? 'true' : 'false'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  }
  // Run one pass now, bypassing the count/age thresholds (honours pause).
  async runConsolidationNow() {
    return this._fetch('/admin/consolidation/run', { method: 'POST' });
  }
  // PUBLIC status for the non-admin halt banner — is automation halted for the
  // caller's org/dept? { halted, scope, reason, actor, since }.
  async automationStatus() {
    return this._fetch('/automation-status');
  }
  // Start/stop or retune ONE trigger (enabled / cron / every_seconds). Admin-
  // authorized in scope by the backend (_can_edit_app).
  async setTrigger(slug, triggerId, patch) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/ai-triggers/${encodeURIComponent(triggerId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch || {}),
    });
  }

  // Start an ASYNC rebuild of a grounded app's few-shot-from-history samples.
  // Returns {run_id, status:'running'} immediately; poll getGroundingRefreshStatus
  // for phase/progress + the completion event. 409 if not grounded or a refresh
  // is already running.
  async startGroundingRefresh(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/grounding/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // Poll a grounding refresh: {status:'running'|'complete'|'failed', phase,
  // progress (0-100), counts, result|error}. Omit runId for the latest run.
  async getGroundingRefreshStatus(slug, runId) {
    const q = runId ? `?run_id=${encodeURIComponent(runId)}` : '';
    return this._fetch(`/apps/${encodeURIComponent(slug)}/grounding/refresh/status${q}`);
  }

  // DURABLE grounding freshness for the card: { never_refreshed, last_refreshed_at,
  // sample_count, canonical_samples, neighbor_samples, in_progress }. Survives the
  // transient run TTL so the card can show "Pending — never refreshed" vs the last
  // refresh time + sample count.
  async getGroundingStatus(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/grounding/status`);
  }

  // L3 fraud calibration (IT/owner-triggered). Returns a report:
  // { screenings_considered, matched_to_decisions, per_signal_hit_rate:
  //   {signal: {cases, officer_rejected, rejection_rate}}, notes }. Reads past
  // decisions only — changes nothing. 409 if fraud screening isn't enabled.
  async calibrateFraud(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/fraud-calibration`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // ── Memory screen (per-app memory: clauses + item ledger) ─────────
  // The learned JUDGEMENTS this app applies (RULES are the SOP, always
  // supreme), with the evidence behind each one.
  // Replaces the old rubrics endpoint — there is no summary blob any more.
  async getMemoryClauses(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/memory/clauses`);
  }
  // The actual officer rejects that taught one clause. This is the answer to
  // "why does it say that?" — traceable, unlike the blob it replaced.
  async getClauseProvenance(slug, clauseId) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/provenance`,
    );
  }
  // Retire a clause. Retire, never edit: the text is provenanced to specific
  // corrections, so rewriting it would leave a rule citing evidence that does
  // not say that.
  async retireClause(slug, clauseId, reason = '') {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/retire`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modality: 'record', task_type: 'decision', summary: reason }),
      },
    );
  }
  // Quarantine: hold a judgement (reversible), e.g. taught by a dismissed
  // officer pending review.
  async quarantineClause(slug, clauseId, reason = '') {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/quarantine`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modality: 'record', task_type: 'decision', summary: reason }),
      },
    );
  }
  // Two-tap SOP-conflict resolution: action = 'retire' | 'acknowledge'.
  async resolveClauseSopConflict(slug, clauseId, action) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/sop-resolution`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modality: 'record', task_type: 'decision', summary: action }),
      },
    );
  }
  // Stop a judgement pending adjudication. The lever an experienced officer
  // needs: corroboration is a headcount, so three juniors who agree outvote the
  // one person who knows better. Not a trust tier — it parks ONE clause and
  // forces a named human to decide, and who/when/why is recorded.
  async challengeClause(slug, clauseId, reason) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/challenge`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modality: 'record', task_type: 'decision', summary: reason }),
      },
    );
  }

  // Adjudicate a challenge. action = 'uphold' | 'dismiss', optionally
  // 'uphold: <reason>'.
  async resolveClauseChallenge(slug, clauseId, action, reason) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/challenge/resolve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          modality: 'record', task_type: 'decision',
          summary: reason ? `${action}: ${reason}` : action,
        }),
      },
    );
  }

  // Put a judgement the precision monitor withdrew back into service, with a
  // fresh measurement window. Only for monitor-parked clauses.
  async reinstateClause(slug, clauseId, reason) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/${encodeURIComponent(clauseId)}/reinstate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modality: 'record', task_type: 'decision', summary: reason || '' }),
      },
    );
  }

  // Every judgement one officer helped teach — the dismissed-officer drill.
  async clausesTaughtBy(slug, officer) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/clauses/by-officer/${encodeURIComponent(officer)}`,
    );
  }

  // Org-wide memory impact for the App Memory card subtitle.
  async orgMemoryImpact() {
    return this._fetch('/org/memory-impact');
  }

  async getMemoryItems(slug, { disposition, modality, limit = 50, cursor } = {}) {
    const params = new URLSearchParams();
    if (disposition) params.set('disposition', disposition);
    if (modality) params.set('modality', modality);
    if (limit) params.set('limit', String(limit));
    if (cursor) params.set('cursor', cursor);
    const q = params.toString();
    return this._fetch(`/apps/${encodeURIComponent(slug)}/memory/items${q ? `?${q}` : ''}`);
  }

  // Exclude a ledger item from precedent retrieval (or lift the flag). The
  // row stays in the ledger — curation affects retrieval, never the record.
  async setItemExclusion(slug, itemId, excluded) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/items/${encodeURIComponent(itemId)}/exclusion`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ excluded: !!excluded }),
      },
    );
  }

  // Manual full memory export (admin action): one open-schema JSON document —
  // {schema, exported_at, app, counts, collections:{decision_records,
  // item_decision_records, analysis_rubrics}, notes}. Curator roles only (403).
  async getMemoryExport(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/memory/export`);
  }

  // Manually push the memory asset to the customer's bucket as gzipped JSONL
  // (curator-only). mode='incremental' (default) exports rows changed since the
  // last push and advances the watermark; mode='snapshot' exports everything.
  // Triggered on demand from the Memory UI — scheduling is a future addition.
  // 400 if the export bucket isn't configured.
  async runMemoryExport(slug, mode = 'incremental') {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/memory/export/run?mode=${encodeURIComponent(mode)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    );
  }

  // Self-learning (auto-run) state: {slug, enabled, auto_refresh}. enabled =
  // outcomes are tracked; auto_refresh = memory updates continuously vs manual-only.
  async getSelfLearning(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/self-learning`);
  }

  // Turn per-app auto-learning ON/OFF. OFF (default) = manual refresh only.
  // 409 if the app isn't grounded / has no outcome tracking configured.
  async setSelfLearning(slug, autoRefresh) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/self-learning`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_refresh: !!autoRefresh }),
    });
  }

  // Self-improving loop metrics for an app (read-only): {override_rate,
  // good_rate, automation_rate, total, trend_weekly[], by_model{}, ...}.
  async getLoopMetrics(slug, days = 90) {
    const q = days ? `?days=${encodeURIComponent(days)}` : '';
    return this._fetch(`/apps/${encodeURIComponent(slug)}/loop-metrics${q}`);
  }

  // Decision API URL (headless / external-UI integration): an external UI POSTs
  // here to get a recommendation, then calls approve. This is the URL to copy.
  decisionApiUrl(slug) {
    return `${this.baseURL.replace(/\/$/, '')}/apps/${encodeURIComponent(slug)}/run`;
  }

  // The copy-paste handoff for an embeddable card:
  // {embed_key, script_url, environment, snippet, version}.
  //
  // Built SERVER-side deliberately. Only the runtime knows its own public
  // origin, which bundle version it currently serves, and whether the app has a
  // published embed page at all — templating the snippet here would let all
  // three drift, and a snippet that renders an empty card in a customer's
  // production screen gets blamed on their integration, not on us.
  //
  // 409 when the app has no embed page, or was published before embed keys
  // existed; the detail names the fix.
  async getEmbedSnippet(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/embed/snippet`);
  }

  // Self-describing decision-API contract: {endpoints, request_schema,
  // run_actions, write_actions, response_shape, approve_request, example, ...}.
  async getDecisionContract(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/decision-contract`);
  }

  // Run the decision agent (recommend): body {action, inputs}. Returns RunResponse
  // {correlation_id, status, decision, reasoning, planned_writes, ...}.
  async runDecision(slug, body) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  // Approve / override / reject / cancel a recommendation by correlation_id.
  // body: { decision: 'approve'|'reject'|'cancel', overrides?: [...], note?: string }.
  async approveDecision(slug, correlationId, body) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/run/${encodeURIComponent(correlationId)}/approve`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) },
    );
  }

  async getApp(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}`);
  }

  // Save a hand-edited spec for an existing app. body: { app_spec, agent_spec? }.
  // Server validates (Pydantic) + preserves identity (slug/tenant/agent/owner).
  async saveSpec(slug, body) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/spec`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  // Record that a human reviewed this app's facet families (publish rule
  // CS-04). The SERVER decides who confirmed, from the token — we only send
  // the list we actually displayed, so a stale screen cannot certify a spec
  // that has moved underneath it.
  async confirmCaseSignature(slug, families) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/case-signature/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ families: families || [] }),
    });
  }

  async archiveApp(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}`, { method: 'DELETE' });
  }

  // Advisory spec REVIEW (correctness + performance smells). Read-only — never
  // mutates the spec. live=true augments findings with real distinct-value
  // cardinality. Returns { findings, counts, live, live_error }.
  async lintSpec(slug, { live = false } = {}) {
    const qs = live ? '?live=true' : '';
    return this._fetch(`/apps/${encodeURIComponent(slug)}/spec/lint${qs}`);
  }

  /**
   * Admin / owner transfer. Re-homes the app's owner_type/owner_id.
   * Server-side RBAC matches the existing endpoint at
   * smart-app-service/main.py:3116.
   */
  async transferApp(slug, { targetOwnerType, targetOwnerId, reason } = {}) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/transfer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        new_owner_type: targetOwnerType,
        new_owner_id: targetOwnerId,
        reason: reason || '',
      }),
    });
  }

  // ── builder ───────────────────────────────────────────────────────
  /**
   * Spawn a builder pod.
   *
   * Chat-first: there is no goal. The builder is a conversational agent and
   * the BA's first chat message drives the build, so ``goal`` is optional and
   * normally omitted (the backend's BuildRequest.goal is Optional). Every
   * build is an ``app``; dashboards are app pages and workflows are added in
   * conversation, so the UI sends ``build_kinds: ['app']``.
   *
   * The legacy 'dashboard' coercion below is kept defensively for any older
   * caller, but the current UI never sends it.
   *
   * The build-kind picker seeds the surface: ``headless`` builds a UI-less
   * Decision API (the pod skips the UI-design phases), and ``primaryPageKind``
   * = 'dashboard' makes the app's primary page a dashboard. Both are seeds the
   * builder agent can still change in conversation — not locks.
   *
   * @param {{
   *   goal?: string,
   *   starterTemplate?: string,
   *   buildKinds?: Array<'app'|'dashboard'>,
   *   headless?: boolean,
   *   primaryPageKind?: 'standard'|'dashboard'|'embed'|null,
   * }} [opts]
   */
  async startBuild({ goal, starterTemplate, buildKinds, headless, primaryPageKind } = {}) {
    const body = { goal, starter_template: starterTemplate };
    if (Array.isArray(buildKinds) && buildKinds.length > 0) {
      const wantsDashboard = buildKinds.includes('dashboard');
      const normalized = buildKinds.map((k) => (k === 'dashboard' ? 'app' : k));
      // de-dup while preserving order
      body.build_kinds = normalized.filter((k, i) => normalized.indexOf(k) === i);
      if (wantsDashboard) {
        body.primary_page_kind = 'dashboard';
      }
    }
    // Explicit picker choice wins over the legacy buildKinds-derived value.
    if (primaryPageKind) body.primary_page_kind = primaryPageKind;
    if (headless) body.build_headless = true;
    return this._fetch('/build', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async getBuildSession(sessionId) {
    return this._fetch(`/build/${encodeURIComponent(sessionId)}`);
  }

  async stopBuildSession(sessionId) {
    return this._fetch(`/build/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    });
  }

  /**
   * Best-effort builder teardown for the page-unload path (tab close /
   * refresh / navigate away), where the in-app close button never fires.
   *
   * Uses `keepalive: true` so the request outlives the page being torn
   * down, and deliberately avoids the _fetch AbortController (whose timeout
   * would otherwise cancel an in-flight request the moment the page goes
   * away). Errors are swallowed — this is opportunistic cleanup. The robust
   * guarantees that there's never more than one builder pod per target are
   * server-side: reuse-on-reopen, reap-on-spawn, and the host TTL reaper.
   */
  async stopBuildSessionBeacon(sessionId) {
    if (!sessionId) return;
    try {
      await authService.authenticatedFetch(
        `${this.baseURL}/build/${encodeURIComponent(sessionId)}`,
        { method: 'DELETE', keepalive: true, headers: { Accept: 'application/json' } },
      );
    } catch { /* best-effort — server-side reaper is the safety net */ }
  }

  async editApp(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/edit`, {
      method: 'POST',
    });
  }

  // ── AI triggers (fire the app's OWN agent on schedule / webhook / poll) ──
  // app_spec.triggers[] — the recommendation-precompute surface that replaces
  // SmartApp recommendation workflows. The agent runs ahead of the click and
  // stages a recommendation into the same inbox.
  async getAiTriggers(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/ai-triggers`);
  }

  // Enable/disable or retune one AI trigger. patch: { enabled?, cron?, every_seconds? }
  async updateAiTrigger(slug, triggerId, patch) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/ai-triggers/${encodeURIComponent(triggerId)}`,
      { method: 'PATCH', body: JSON.stringify(patch || {}) },
    );
  }

  // Fire ONE run of a trigger now (manual). Poll pulls the next single new row;
  // webhook/schedule run once with optional sample `inputs`. Works on a
  // deactivated trigger (used to test before activating). Click again for the
  // next case. Returns { fired, activity:[...] }.
  async runAiTriggerNow(slug, triggerId, inputs) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/ai-triggers/${encodeURIComponent(triggerId)}/run`,
      { method: 'POST', body: JSON.stringify(inputs ? { inputs } : {}) },
    );
  }

  // Run history for ONE trigger — last N firings (newest first), every firing
  // recorded (scheduler / webhook / poll / manual) incl. failures. Returns
  // { trigger_id, runs:[{ status, fired, fired_via, error, correlation_id,
  // decision, write_count, started_at, finished_at, duration_ms, created_at }],
  // count }.
  async listTriggerRuns(slug, triggerId, { limit = 50 } = {}) {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/ai-triggers/${encodeURIComponent(triggerId)}/runs?${params.toString()}`,
    );
  }

  // ── version history + rollback ────────────────────────────────────
  // Every publish / spec-edit / promote snapshots the superseded (app+agent)
  // spec. The last N (default 3) are kept per app and can be rolled back to.

  // List version history: the live version (is_current) + retained snapshots,
  // newest first. Returns { slug, current_version, max_kept, versions:[{ version,
  // is_current, title, trigger_count, active_trigger_count, agent_version,
  // status, snapshotted_at, snapshotted_by, reason }] }.
  async listAppVersions(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/versions`);
  }

  // Roll back to a retained snapshot. Forward-only: the snapshot is re-validated
  // and re-published as a NEW version (current + 1); AI triggers land DISABLED.
  // Returns { ok, slug, rolled_back_to, new_version, triggers_disabled }.
  async rollbackAppVersion(slug, version, reason) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}/rollback`,
      { method: 'POST', body: JSON.stringify(reason ? { reason } : {}) },
    );
  }

  // ── audience (publish-level distribution) ─────────────────────────
  // Audience controls who can SEE and RUN the app:
  //   'owner'          — only owner_sa_id members (default)
  //   'team:<sa_id>'   — members of a specific team SA
  //   'dept:<dept_id>' — everyone in a department
  //   'org'            — everyone in the tenant
  // Edit access is independent and always controlled by owner_sa_id
  // membership. Audience changes follow the higher-of(current, target)
  // RBAC rule (see smart-app-service _can_change_audience).

  /**
   * Return the audience values the caller can pick for this app. Drives
   * the publish picker — disallowed options come back with a ``reason``
   * code (``not_team_admin`` / ``needs_dept_admin`` / ``needs_org_admin``)
   * the UI surfaces as a tooltip.
   *
   * @param {string} slug
   * @returns {Promise<{slug, current, options: Array<{value, label, level, target_id, allowed, reason}>}>}
   */
  async getPublishOptions(slug) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/publish-options`);
  }

  /**
   * Change an app's audience. The server applies the higher-of(current,
   * target) rule and returns 403 if the caller can't satisfy both.
   *
   * @param {string} slug
   * @param {{ audience: string, reason?: string }} body
   * @returns {Promise<{slug, audience, previous_audience}>}
   */
  async setAudience(slug, { audience, reason } = {}) {
    return this._fetch(`/apps/${encodeURIComponent(slug)}/audience`, {
      method: 'POST',
      body: JSON.stringify({ audience, reason: reason || '' }),
    });
  }

  /** Returns the canonical runtime URL for a Smart App (auth-gated). */
  shareUrl(slug) {
    return this.appUrl(slug);
  }

  // ── run audit (backs the Smart App "Audit" tab) ──────────────────────
  /**
   * List audited /run invocations for an app — newest first. Each row is
   * a lightweight summary; fetch full evidence with getRunAudit().
   *
   * @param {string} slug
   * @param {{ limit?: number, offset?: number, decision?: string,
   *           action?: string, status?: string, flagged?: boolean }} [opts]
   * @returns {Promise<{slug,total,limit,offset,runs:Array}>}
   */
  async listAppRuns(slug, { limit = 50, offset = 0, decision, action, status, flagged } = {}) {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (offset) params.set('offset', String(offset));
    if (decision) params.set('decision', decision);
    if (action) params.set('action', action);
    if (status) params.set('status', status);
    if (flagged) params.set('flagged', 'true');
    const qs = params.toString() ? `?${params.toString()}` : '';
    return this._fetch(`/apps/${encodeURIComponent(slug)}/runs${qs}`);
  }

  /**
   * Full audit trail for one run — the decision, reasoning, citations,
   * retrieved evidence, timeline and agent_spec version. Usually one row;
   * two when the run was approved.
   *
   * @param {string} slug
   * @param {string} correlationId
   * @returns {Promise<{slug,correlation_id,runs:Array,auto_commits:Array}>}
   */
  async getRunAudit(slug, correlationId) {
    return this._fetch(
      `/apps/${encodeURIComponent(slug)}/runs/${encodeURIComponent(correlationId)}/audit`,
    );
  }

  /**
   * App-wide auto-approve ledger — every auto-process commit attempt, with the
   * policy rule that allowed it, the payload, and the outcome. This is the read
   * surface for the highest-scrutiny (no-human) writes.
   *
   * @param {string} slug
   * @param {{ limit?: number, offset?: number, committed?: boolean }} [opts]
   * @returns {Promise<{slug,total,limit,offset,rows:Array}>}
   */
  async listAppAutoCommits(slug, { limit = 50, offset = 0, committed } = {}) {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (offset) params.set('offset', String(offset));
    if (committed === true) params.set('committed', 'true');
    if (committed === false) params.set('committed', 'false');
    const qs = params.toString() ? `?${params.toString()}` : '';
    return this._fetch(`/apps/${encodeURIComponent(slug)}/auto-commits${qs}`);
  }

  /**
   * The Change Ledger — what actually changed and who caused it, newest first.
   * AI auto-process commits + human-caused changes (approvals / queue actions /
   * overlay edits). Recommendations that committed nothing are excluded.
   *
   * @param {string} slug
   * @param {{ limit?: number, offset?: number, actor?: string, outcome?: string }} [opts]
   *        actor: 'ai_auto' | 'user' ; outcome: 'applied' | 'failed'
   * @returns {Promise<{slug,total,limit,offset,changes:Array}>}
   */
  async listAppChanges(slug, { limit = 50, offset = 0, actor, outcome } = {}) {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (offset) params.set('offset', String(offset));
    if (actor) params.set('actor', actor);
    if (outcome) params.set('outcome', outcome);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return this._fetch(`/apps/${encodeURIComponent(slug)}/changes${qs}`);
  }
}

export default new SmartAppService();
