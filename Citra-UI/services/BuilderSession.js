// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * BuilderSession.js — client for the smart-app-service builder relay.
 *
 * The browser cannot reach the builder pod's adapter directly (it's on
 * a private host port and gated by a server-side control secret). We
 * tunnel through smart-app-service:
 *
 *   POST /build/{session_id}/chat/stream  body={message: "..."}
 *   POST /build/{session_id}/cancel
 *   POST /build/{session_id}/steer        body={message: "..."}
 *
 * The chat/stream relay forwards to ``<adapter_url>/task`` and pipes the
 * SSE stream straight back. The adapter — now a thin WebChat channel
 * into OpenClaw — emits these event types verbatim:
 *
 *   message      → model text delta
 *   thinking     → model reasoning block (right pane)
 *   tool_call    → tool invocation start
 *   tool_result  → tool invocation end
 *   plan         → TodoWrite output (right todo pane)
 *   canvas_event → A2UI / canvas tool activity
 *   status       → transient progress marker
 *   artifact     → published-deliverable from citra_toolkit
 *   cancelled    → run aborted
 *   done         → turn finished
 *   error        → fatal turn error
 *
 * cancel/steer are pure pass-throughs to OpenClaw chat.abort / chat.send.
 * For steer, the payload is the BA's text verbatim — including OpenClaw
 * inline directives like ``/queue collect`` which the gateway parses
 * before reaching the LLM.
 */

import { CONFIG } from '../config/config';
import authService from './authService';
import {
  QueueMode,
  buildQueueModeDirective,
} from './openClawShared';

const SMART_APP_BASE = CONFIG.urls.smartAppService;

const buildUrl = (sessionId, suffix) =>
  `${SMART_APP_BASE}/build/${encodeURIComponent(sessionId)}${suffix}`;

/**
 * Send one BA chat turn to the builder pod and stream the response.
 *
 * @param {string} sessionId
 * @param {string} message — BA-typed text
 * @param {{
 *   onEvent?: (evt: object) => void,
 *   onError?: (err: Error) => void,
 *   onDone?:  () => void,
 * }} [handlers]
 * @returns {{ cancel: () => void }}
 */
export function streamBuilderChat(sessionId, message, handlers = {}) {
  if (!sessionId) {
    handlers.onError?.(new Error('sessionId is required'));
    return { cancel: () => {} };
  }
  if (!message || !message.trim()) {
    handlers.onError?.(new Error('message is required'));
    return { cancel: () => {} };
  }

  const controller = new AbortController();
  const url = buildUrl(sessionId, '/chat/stream');

  (async () => {
    try {
      const resp = await authService.authenticatedFetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify({ message }),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`relay rejected ${resp.status}: ${txt.slice(0, 200)}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const lines = frame.split('\n');
          let data = '';
          for (const line of lines) {
            if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          if (!data) continue;
          try {
            const evt = JSON.parse(data);
            handlers.onEvent?.(evt);
          } catch {
            // Non-JSON keepalive comments etc. — ignore.
          }
        }
      }
      handlers.onDone?.();
    } catch (e) {
      if (e?.name === 'AbortError') return;
      handlers.onError?.(e);
    }
  })();

  return { cancel: () => controller.abort() };
}

/**
 * Abort the in-flight builder turn (OpenClaw native chat.abort).
 * Returns true on success.
 */
export async function cancelBuilderTurn(sessionId) {
  if (!sessionId) return { ok: false, error: 'no session' };
  try {
    const r = await authService.authenticatedFetch(
      buildUrl(sessionId, '/cancel'),
      { method: 'POST' },
    );
    if (r.ok) return { ok: true, ...(await r.json().catch(() => ({}))) };
    // The reason matters and used to be discarded: 409 "build session has no
    // live builder pod" is a normal outcome after the idle sweep reaps a pod,
    // and is not the same as a stop that worked.
    const body = await r.json().catch(() => ({}));
    return { ok: false, status: r.status, error: body.detail || body.error || `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, error: e?.message || 'cancel request failed' };
  }
}

/**
 * Forward a follow-up message into the in-flight builder turn. The text
 * is sent verbatim to OpenClaw chat.send; the configured queue mode
 * (default ``steer``) decides whether to inject mid-flight or queue.
 *
 * Returns: { ok: boolean, queued?: boolean, runId?: string|null, error? }
 */
export async function steerBuilder(sessionId, message) {
  if (!sessionId) return { ok: false, error: 'sessionId required' };
  if (!message || !message.trim()) return { ok: false, error: 'message required' };
  try {
    const r = await authService.authenticatedFetch(
      buildUrl(sessionId, '/steer'),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      },
    );
    if (!r.ok) {
      let body = '';
      try { body = await r.text(); } catch (_) { /* ignore */ }
      return { ok: false, error: `steer ${r.status}: ${body.slice(0, 200)}` };
    }
    const data = await r.json().catch(() => ({}));
    return { ok: true, queued: data.queued !== false, runId: data.runId || null };
  } catch (e) {
    return { ok: false, error: e?.message || 'steer transport error' };
  }
}

/**
 * Switch the builder session's queue mode by sending the inline
 * ``/queue <mode>`` directive through chat.send. OpenClaw parses it
 * before reaching the LLM (no model tokens spent).
 *
 * Modes from QueueMode enum: steer | steer-backlog | collect | followup.
 */
export async function setBuilderQueueMode(sessionId, mode) {
  if (!sessionId) return { ok: false, error: 'sessionId required' };
  const directive = buildQueueModeDirective(mode);
  if (!directive) return { ok: false, error: `unknown queue mode: ${mode}` };
  return steerBuilder(sessionId, directive);
}

/**
 * Normalise a raw adapter event into the shape the 3-pane UI expects.
 *
 *   { kind: 'chat'|'reasoning'|'tool'|'plan'|'canvas'|'artifact'|'status'|'error',
 *     role?: 'assistant',
 *     text?, delta?,
 *     tool?, toolStatus?: 'running'|'done'|'error',
 *     phase?,
 *     planItems?,            // for kind:'plan'
 *     canvasAction?,         // for kind:'canvas'
 *     raw }
 *
 * The classifier supports BOTH the new WebSocket-channel adapter event
 * shape (`message`, `thinking`, `tool_call`, `tool_result`, `plan`,
 * `canvas_event`) AND the legacy adapter shape (`agent.text`,
 * `agent.thinking`, `tool.start`, `tool.result`) so this UI keeps
 * working during a rolling deploy of mixed-version builder pods.
 */
/**
 * Generic file-system / shell / exec plumbing tools. These are how the agent
 * carries out work (reading SKILL.md files, writing specs, running probes) but
 * they are NOT meaningful "reasoning" for the BA — exposing them in the
 * reasoning pane is noise. The builder screen filters these out; domain tools
 * (e.g. the dept-MCP `Citra__*` calls) are kept so the BA still sees real
 * progress.
 */
const PLUMBING_TOOLS = new Set([
  'read', 'write', 'edit', 'multiedit', 'notebookedit',
  'exec', 'bash', 'shell', 'run', 'process',
  'glob', 'grep', 'ls', 'cat', 'applypatch', 'apply_patch',
  'todowrite', 'todo_write',
]);

/** True when a tool name is generic plumbing that should NOT appear in the
 * reasoning pane. Matches case-insensitively and strips any `skill:` prefix. */
export function isPlumbingTool(tool) {
  if (!tool || typeof tool !== 'string') return false;
  const c = tool.replace(/^skill[:/]/i, '').trim().toLowerCase();
  return PLUMBING_TOOLS.has(c);
}

export function normalizeEvent(evt) {
  if (!evt || typeof evt !== 'object') return null;
  const t = (evt.type || evt.event || '').toLowerCase();

  // ── new WebSocket-channel adapter events ─────────────────────────
  if (t === 'message') {
    // Adapter emits one block of model text per session.message text
    // block. Treat as a delta so the UI can append-and-stream.
    return { kind: 'chat', role: 'assistant', delta: evt.text || '', raw: evt };
  }
  if (t === 'thinking') {
    return { kind: 'reasoning', text: evt.text || '', raw: evt };
  }
  if (t === 'tool_call') {
    const tool = evt.name || evt.tool;
    return {
      kind: 'tool',
      tool,
      toolStatus: 'running',
      text: stringifyArgs(evt.args),
      callId: evt.call_id || null,
      raw: evt,
    };
  }
  if (t === 'tool_result') {
    const tool = evt.name || evt.tool;
    return {
      kind: 'tool',
      tool,
      toolStatus: evt.ok === false ? 'error' : 'done',
      text: typeof evt.output === 'string' ? evt.output : '',
      callId: evt.call_id || null,
      raw: evt,
    };
  }
  if (t === 'plan') {
    return {
      kind: 'plan',
      planItems: Array.isArray(evt.items) ? evt.items : [],
      raw: evt,
    };
  }
  if (t === 'canvas_event') {
    return {
      kind: 'canvas',
      canvasAction: evt.action || '',
      surfaceId: evt.surface_id || null,
      args: evt.args || null,
      raw: evt,
    };
  }
  if (t === 'artifact') {
    return { kind: 'artifact', artifact: evt, raw: evt };
  }
  if (t === 'status') {
    return { kind: 'status', text: evt.text || evt.stage || 'working', raw: evt };
  }
  if (t === 'done') {
    return { kind: 'status', text: 'done', done: true, raw: evt };
  }
  if (t === 'cancelled') {
    return { kind: 'status', text: 'cancelled', raw: evt };
  }
  if (t === 'error') {
    return { kind: 'error', text: evt.message || 'unknown error', raw: evt };
  }

  // ── legacy adapter events (older builder pod versions) ───────────
  if (t === 'agent.text' || t === 'agent.message') {
    return { kind: 'chat', role: 'assistant', text: evt.text || evt.content || evt.message || '', raw: evt };
  }
  if (t === 'agent.text.delta' || t === 'agent.delta') {
    return { kind: 'chat', role: 'assistant', delta: evt.delta || evt.text || '', raw: evt };
  }
  if (t === 'agent.text.done' || t === 'agent.done') {
    return { kind: 'chat', role: 'assistant', done: true, raw: evt };
  }
  if (t === 'agent.thinking' || t === 'agent.start' || t === 'agent') {
    return { kind: 'status', text: 'thinking', raw: evt };
  }
  if (t === 'tool.start' || t === 'tool.call') {
    const tool = evt.tool || evt.name;
    return { kind: 'tool', tool, toolStatus: 'running', text: evt.description || stringifyArgs(evt.args), raw: evt };
  }
  if (t === 'tool.result' || t === 'tool.done') {
    const tool = evt.tool || evt.name;
    return { kind: 'tool', tool, toolStatus: 'done', raw: evt };
  }
  if (t === 'tool.error') {
    const tool = evt.tool || evt.name;
    return { kind: 'tool', tool, toolStatus: 'error', text: evt.message || evt.error || '', raw: evt };
  }
  if (t.startsWith('agent.publish') || t.startsWith('publish')) {
    return { kind: 'status', text: 'published', deploy: evt, raw: evt };
  }

  // Unknown — preserve raw rather than coercing to reasoning so the
  // panes don't render mystery text.
  return { kind: 'unknown', raw: evt };
}

function stringifyArgs(a) {
  if (a == null) return '';
  if (typeof a === 'string') return a;
  try { return JSON.stringify(a); } catch { return ''; }
}

/**
 * Detect a publish result from the builder agent's CHAT PROSE — the FALLBACK.
 *
 * Primary path: smart-app-service now emits a STRUCTURED `publish` event at the
 * turn's `done` (see /publish stamp + the build-chat relay), which
 * normalizeEvent maps to {kind:'status', text:'published', deploy:{...}} and
 * the screen renders deterministically — independent of the agent's wording.
 *
 * This prose parser remains as a fallback for older builder pods / dropped
 * streams: the agent also narrates the result as text per
 * citra-app-publish/SKILL.md (e.g. "✅ Preview is up at <url>",
 * "✅ Live at <url> (version 1)", "Your app is live at **<url>**. Version 1.").
 * We parse that text to drive the preview/live deploy card, mirroring how
 * the cement_e2e harness recognises a publish.
 *
 * A LIVE turn is terminal and also *mentions* the preview URL ("your preview
 * stays available"), so we detect "live at …" FIRST and only fall back to a
 * preview URL when there is no live promotion in the text.
 *
 * @returns {{url:string, slug:string, version:(number|null), isPreview:boolean}|null}
 */
function detectDeployFromText(text) {
  if (!text || typeof text !== 'string') return null;
  const strip = (u) => u.replace(/[.*)\]]+$/, ''); // trailing punctuation / markdown bold
  const slugOf = (u) => {
    const m = String(u).match(/\/([a-z0-9][a-z0-9-_]*)\/?(?:[?#].*)?$/i);
    return m ? m[1] : '';
  };

  // Live/test publish — terminal. "live at <url>", where <url> may be a bare
  // URL, **bold**, or a markdown link [<url>](<url>). Allow the leading `[` /
  // `*` of a markdown/bold wrapper between "live at" and the URL, and stop the
  // URL at `]` so we capture the link label (which is the URL) cleanly. Without
  // this, the agent's common "live at [http://…](http://…)" phrasing failed to
  // match and the BA never got the "Open app" deploy banner.
  let m = text.match(/live at\s+[\[*]*\s*(https?:\/\/[^\s*()\]]+)/i);
  if (m) {
    const url = strip(m[1]);
    const vm = text.match(/version\s+(\d+)/i) || text.match(/\(v(\d+)\)/i);
    return { url, slug: slugOf(url), version: vm ? Number(vm[1]) : null, isPreview: false };
  }

  // Preview — "Preview is up at <url>" / "preview of your app: <url>" (bare or
  // markdown link). Stop at `]` so a markdown label URL is captured cleanly.
  m = text.match(/preview[^\n]*?(https?:\/\/[^\s*()\]]+)/i);
  if (m) {
    const url = strip(m[1]);
    return { url, slug: slugOf(url), version: null, isPreview: true };
  }

  return null;
}

export { QueueMode, detectDeployFromText };

export default {
  streamBuilderChat,
  cancelBuilderTurn,
  steerBuilder,
  setBuilderQueueMode,
  normalizeEvent,
  QueueMode,
};
