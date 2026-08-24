// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * SmartAppBuilderScreen — full-screen 3-pane SmartApp builder.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ BuildTopBar (goal + kind badges + session id + close X) │
 *   ├──────────────┬─────────────────────┬─────────────────── ┤
 *   │ AgentChat    │ AgentReasoning      │ BuildTodo          │
 *   │ (BA dialog)  │ (live tool trace)   │ (phase checklist)  │
 *   └──────────────┴─────────────────────┴────────────────────┘
 *
 * Wide web  (≥1280): three columns side-by-side.
 * Medium    (≥860):  chat is full-height, reasoning + todo stacked on right.
 * Narrow    (<860):  segmented tabs across the top, one pane visible.
 *
 * Streaming protocol: smart-app-service /build/{id}/chat/stream relay,
 * which forwards to the pod adapter's POST /task SSE endpoint and pipes
 * the OpenClaw event frames back. See services/BuilderSession.js.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Modal, View, Text, TouchableOpacity, StyleSheet,
  useWindowDimensions, Platform, Linking,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import BuildTopBar from '../components/builder/BuildTopBar';
import AgentChatPane from '../components/builder/AgentChatPane';
import AgentReasoningPane from '../components/builder/AgentReasoningPane';
import {
  streamBuilderChat,
  normalizeEvent,
  cancelBuilderTurn,
  steerBuilder,
  setBuilderQueueMode,
  detectDeployFromText,
  isPlumbingTool,
} from '../services/BuilderSession';
import { QueueMode, QUEUE_MODE_SHORT } from '../services/openClawShared';
import QueueModePicker from '../components/openclaw/QueueModePicker';
import authService from '../services/authService';

// Open a runtime/app URL WITH the current user's JWT appended as ?_t=, so the
// launched app authenticates as the real user (the runtime has no god token).
// Chat UI lives in Citra-UI, which holds the token — inject it on open.
async function _openWithToken(url) {
  let finalUrl = url;
  try {
    const token = await authService.getToken();
    if (token) {
      const sep = url.includes('?') ? '&' : '?';
      finalUrl = `${url}${sep}_t=${encodeURIComponent(token)}`;
    }
  } catch {
    /* token unavailable — open bare; runtime will 401 until the user re-auths */
  }
  try {
    Linking.openURL(finalUrl);
  } catch {
    /* noop */
  }
}
import SmartAppService from '../services/SmartAppService';

const DEFAULT_THEME = {
  background:    '#FFFFFF',
  surface:       '#F9FAFB',
  text:          '#111827',
  textSecondary: '#6B7280',
  primary:       '#3B82F6',
  border:        '#E5E7EB',
  danger:        '#DC2626',
};

const TABS = [
  { id: 'chat',      label: 'Chat',      icon: 'chatbubbles-outline' },
  { id: 'reasoning', label: 'Reasoning', icon: 'bulb-outline' },
];

let _idSeq = 0;
const nextId = () => `b_${Date.now()}_${++_idSeq}`;

export default function SmartAppBuilderScreen({
  visible,
  session,            // { session_id, builder_url, mode: 'new'|'edit', slug?, goal? } OR draft
  buildKinds: initialBuildKinds = ['app'],
  onClose,
  onPublished,        // (deploy) => void — non-destructive catalog refresh on LIVE publish
  onStartBuild,       // () => Promise<void> — spawns the session from the BA's first message (draft only)
  startBusy = false,  // parent-controlled busy flag while /build is being called
  startError = '',
  theme,
}) {
  const colors = (theme && theme.colors) ? { ...DEFAULT_THEME, ...theme.colors } : DEFAULT_THEME;
  const { width } = useWindowDimensions();
  // Two-pane (chat + reasoning) on wide/medium; segmented tabs when narrow.
  const showTwo  = width >= 860;
  const showTabs = !showTwo;

  // Draft = chat is open but no build session exists yet. The BA's first
  // message spawns the session (see handleDraftSend). There is no goal and
  // no kind picker — the build is always an app; workflows are added in
  // conversation.
  const isDraft = !session?.session_id;
  // First BA message, captured while still in draft and replayed as the
  // first chat turn once the session_id lands.
  const pendingFirstMessageRef = useRef(null);

  const [activeTab, setActiveTab] = useState('chat');
  const [messages, setMessages]   = useState([]);
  const [reasoning, setReasoning] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [deploy, setDeploy] = useState(null); // { url, slug, version }
  // OpenClaw queue mode — controls how mid-stream BA messages behave.
  const [queueMode, setQueueModeLocal] = useState(QueueMode.STEER);
  const [showQueueModePicker, setShowQueueModePicker] = useState(false);
  const [error, setError] = useState('');

  const streamRef = useRef(null);
  const assistantIdRef = useRef(null); // current streaming assistant bubble id
  const assistantTextRef = useRef(''); // accumulated text of the current assistant turn (for deploy detection)

  const buildKinds = session?.buildKinds || initialBuildKinds;

  // Surface the BA picked in the build-kind picker, used to tailor the draft
  // welcome greeting. Prefer the explicit picker choice; fall back to the
  // seeded levers (headless / dashboard primary) and finally the open-ended
  // 'conversational' greeting for callers that don't seed a surface (edit).
  const surface = session?.buildKind
    || (session?.headless ? 'api'
        : session?.primaryPageKind === 'dashboard' ? 'dashboard'
        : session?.primaryPageKind === 'embed' ? 'embed'
        : 'conversational');

  // ── event handling ─────────────────────────────────────────────
  const ingest = useCallback((rawEvt) => {
    const evt = normalizeEvent(rawEvt);
    if (!evt) return;

    if (evt.kind === 'chat') {
      if (evt.delta) {
        // Accumulate the turn text so we can detect the publish/preview URL
        // the agent narrates as prose (the pod emits no structured publish
        // event — see detectDeployFromText).
        if (!assistantIdRef.current) assistantTextRef.current = '';
        assistantTextRef.current += evt.delta;
        setMessages((prev) => {
          let next = prev.slice();
          let id = assistantIdRef.current;
          if (!id) {
            id = nextId();
            assistantIdRef.current = id;
            next.push({ id, role: 'assistant', text: '', streaming: true });
          }
          next = next.map((m) => m.id === id
            ? { ...m, text: (m.text || '') + evt.delta, streaming: true }
            : m);
          return next;
        });
      } else if (evt.text) {
        assistantTextRef.current = evt.text;
        setMessages((prev) => {
          const id = assistantIdRef.current || nextId();
          assistantIdRef.current = id;
          const exists = prev.some((m) => m.id === id);
          if (exists) {
            return prev.map((m) => m.id === id
              ? { ...m, text: evt.text, streaming: !evt.done }
              : m);
          }
          return [...prev, { id, role: 'assistant', text: evt.text, streaming: !evt.done }];
        });
      }
      if (evt.done) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantIdRef.current ? { ...m, streaming: false } : m
        ));
        assistantIdRef.current = null;
        // The completed turn may have announced a preview or live URL.
        const d = detectDeployFromText(assistantTextRef.current);
        assistantTextRef.current = '';
        if (d && d.url) {
          setDeploy(d);
          // A publish refreshes the catalog behind the modal; the session stays
          // open so the BA can keep iterating or promote to prod.
          onPublished?.({ slug: d.slug, version: d.version, deploy_url: d.url });
        }
      }
      return;
    }

    if (evt.kind === 'tool') {
      // Generic file-system / shell / exec plumbing is how the agent does its
      // work, not meaningful reasoning for the BA — keep it out of the pane.
      // Domain tool calls (dept-MCP `Citra__*`, etc.) still surface.
      if (isPlumbingTool(evt.tool)) return;
      setReasoning((prev) => {
        // Merge consecutive same-tool events: replace running with done
        const last = prev[prev.length - 1];
        if (last && last.kind === 'tool' && last.tool === evt.tool) {
          const merged = { ...last, ...evt, id: last.id };
          return [...prev.slice(0, -1), merged];
        }
        return [...prev, { ...evt, id: nextId() }];
      });
      return;
    }

    if (evt.kind === 'reasoning' && evt.text) {
      setReasoning((prev) => [...prev, { ...evt, id: nextId() }]);
      return;
    }

    if (evt.kind === 'canvas') {
      // Canvas/A2UI activity surfaced in the reasoning trace as a
      // one-line indicator. Full inline A2UI rendering needs an
      // iframe + WS proxy and is a follow-up.
      setReasoning((prev) => [...prev, { ...evt, id: nextId() }]);
      return;
    }

    if (evt.kind === 'status' && evt.text === 'published' && evt.deploy) {
      const d = evt.deploy;
      const url = d.deploy_url || d.url;
      const slug = d.slug || '';
      const version = d.version;
      if (url) {
        setDeploy({ url, slug, version });
        // A publish refreshes the catalog behind the modal. We NEVER auto-close —
        // the BA may keep iterating, promote to prod, or add a workflow.
        onPublished?.({ slug, version, deploy_url: url });
      }
      return;
    }

    if (evt.kind === 'error') {
      setReasoning((prev) => [...prev, { ...evt, id: nextId() }]);
      setIsStreaming(false);
      return;
    }
  }, [onPublished]);

  // ── chat send ──────────────────────────────────────────────────
  const sendChat = useCallback((message) => {
    if (!session?.session_id) return;
    setMessages((prev) => [...prev, { id: nextId(), role: 'user', text: message }]);
    setIsStreaming(true);
    assistantIdRef.current = null;
    assistantTextRef.current = '';
    streamRef.current?.cancel?.();
    streamRef.current = streamBuilderChat(session.session_id, message, {
      onEvent: ingest,
      onError: (err) => {
        setReasoning((prev) => [
          ...prev,
          { kind: 'error', text: err?.message || 'stream failed', id: nextId() },
        ]);
        setIsStreaming(false);
        // Surface the error inline as an assistant bubble too so the
        // chat pane doesn't look frozen.
        setMessages((prev) => [
          ...prev,
          {
            id: nextId(),
            role: 'assistant',
            text: `⚠️ ${err?.message || 'stream failed'}`,
            streaming: false,
          },
        ]);
      },
      onDone: () => {
        setIsStreaming(false);
        setMessages((prev) => prev.map((m) =>
          m.id === assistantIdRef.current ? { ...m, streaming: false } : m
        ));
        assistantIdRef.current = null;
      },
    });
  }, [session?.session_id, ingest]);

  const [stopping, setStopping] = useState(false);

  const cancelStream = useCallback(async () => {
    streamRef.current?.cancel?.();
    if (session?.session_id) {
      // Tell OpenClaw to abort the run server-side too — without this
      // the model keeps generating tokens we'd just throw away.
      try { await cancelBuilderTurn(session.session_id); } catch { /* best-effort */ }
    }
    setIsStreaming(false);
  }, [session?.session_id]);

  // Stop button (top bar): abort the in-flight builder run immediately so a
  // runaway / looping build stops spending. Reuses cancelStream (local stream
  // cancel + OpenClaw chat.abort via /build/{id}/cancel).
  const handleStop = useCallback(async () => {
    setStopping(true);
    try { await cancelStream(); } finally { setStopping(false); }
  }, [cancelStream]);

  // Mid-stream BA input — forwards to OpenClaw's chat.send. The active
  // queueMode determines whether OpenClaw injects between tool calls,
  // collects into one followup turn, or waits for the current turn to
  // end. We do NOT cancel the stream — OpenClaw owns that decision.
  //
  // B8 fix: if steer fails (race with cancel that just closed the SSE,
  // transport glitch, no live session on the server), fall back to
  // opening a fresh streamQuery for this message so the BA actually
  // sees a response.
  const steerChat = useCallback(async (message) => {
    if (!session?.session_id) return;
    // Mirror the BA's typed text in the chat pane immediately as a
    // user bubble with a "mid-task" tag so the transcript reflects
    // reality, even before OpenClaw ack arrives.
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'user', text: message, midTask: true },
    ]);
    let ok = false;
    try {
      const res = await steerBuilder(session.session_id, message);
      ok = !!res?.ok;
      if (!ok) setError(res?.error || 'failed to forward steer');
    } catch (e) {
      setError(e?.message || 'failed to forward steer');
    }
    if (!ok) {
      // Fall back to a fresh chat turn so the message isn't dropped.
      // Don't add the user bubble again — we already appended above.
      streamRef.current?.cancel?.();
      setIsStreaming(true);
      assistantIdRef.current = null;
      streamRef.current = streamBuilderChat(session.session_id, message, {
        onEvent: ingest,
        onError: (err) => {
          setIsStreaming(false);
          setError(err?.message || 'stream failed');
        },
        onDone: () => setIsStreaming(false),
      });
    }
  }, [session?.session_id, ingest]);

  const handleSelectQueueMode = useCallback(async (mode) => {
    setShowQueueModePicker(false);
    if (!session?.session_id || mode === queueMode) return;
    const previous = queueMode;
    setQueueModeLocal(mode);
    const res = await setBuilderQueueMode(session.session_id, mode);
    if (!res?.ok) {
      setQueueModeLocal(previous);
      setError(res?.error || 'failed to change queue mode');
    }
    // B5 fix: no optimistic reasoning entry — OpenClaw replies with
    // its own "⚙️ Queue mode set to X." message which already shows
    // in the chat pane. Two notifications looked broken.
  }, [session?.session_id, queueMode]);

  // ── kick-off / cleanup ─────────────────────────────────────────
  useEffect(() => {
    if (!visible || !session?.session_id) return;
    // Chat-first handoff: when the BA sent a first message while we were
    // still in draft, the session has now spawned — replay that message as
    // the first real chat turn. There is no goal to seed; the conversation
    // itself drives the build. (Edit sessions open with a session_id and no
    // pending message, so they simply wait for the BA to speak.)
    if (pendingFirstMessageRef.current && messages.length === 0) {
      const first = pendingFirstMessageRef.current;
      pendingFirstMessageRef.current = null;
      sendChat(first);
    }
    return () => {
      streamRef.current?.cancel?.();
    };
    // We deliberately depend only on visible+session_id — we want this
    // to fire once when the modal opens, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, session?.session_id]);

  // Best-effort teardown when the BA closes the browser tab, refreshes, or
  // navigates away WITHOUT using the in-app close button (which already
  // tears down via handleClose). Web only — pagehide/beforeunload don't
  // exist on native. Even if this never fires (e.g. a hard crash), the
  // server reattaches to the live pod on reopen and the host TTL reaper
  // eventually removes a truly-abandoned one, so a pod is never duplicated.
  useEffect(() => {
    if (!visible || !session?.session_id) return undefined;
    if (typeof window === 'undefined' || !window.addEventListener) return undefined;
    const sid = session.session_id;
    const onUnload = () => { SmartAppService.stopBuildSessionBeacon(sid); };
    window.addEventListener('pagehide', onUnload);
    window.addEventListener('beforeunload', onUnload);
    return () => {
      window.removeEventListener('pagehide', onUnload);
      window.removeEventListener('beforeunload', onUnload);
    };
  }, [visible, session?.session_id]);

  const handleClose = useCallback(async () => {
    streamRef.current?.cancel?.();
    if (session?.session_id) {
      try {
        await SmartAppService.stopBuildSession(session.session_id);
      } catch { /* best-effort */ }
    }
    onClose?.();
  }, [session?.session_id, onClose]);
  // Draft mode: the BA's first message both (a) is remembered so we can
  // replay it as the first chat turn once the session spawns, and (b)
  // triggers the lazy /build. No goal is passed — the message is just chat.
  const handleDraftSend = useCallback((message) => {
    const m = (message || '').trim();
    if (!m || startBusy) return;
    pendingFirstMessageRef.current = m;
    onStartBuild?.();
  }, [startBusy, onStartBuild]);
  // ── render ─────────────────────────────────────────────────────
  if (!visible) return null;

  const chatPane = (
    <AgentChatPane
      messages={messages}
      isStreaming={isStreaming}
      // In draft, the first send spawns the build; afterwards it's a normal
      // chat turn. Steer/cancel only make sense once a session is live.
      onSend={isDraft ? handleDraftSend : sendChat}
      onCancel={isDraft ? undefined : cancelStream}
      onSteer={isDraft ? undefined : steerChat}
      draft={isDraft}
      surface={surface}
      startBusy={startBusy}
      onOpenQueueMode={isDraft ? undefined : () => setShowQueueModePicker(true)}
      queueModeLabel={QUEUE_MODE_SHORT[queueMode] || 'Steer'}
      // Open any app/runtime URL the agent puts in chat WITH the user's JWT
      // appended (?_t=), so the launched app authenticates — same handoff the
      // deploy button uses. Without this, a tapped link 401s.
      onOpenUrl={_openWithToken}
      colors={colors}
    />
  );
  const reasoningPane = (
    <AgentReasoningPane
      events={reasoning}
      isThinking={isStreaming}
      colors={colors}
    />
  );
  // Deploy result surfaced as a slim banner (the old To-Do pane used to host
  // it). Appears on publish; "Open" launches the app.
  const deployBanner = deploy?.url ? (
    <View style={[styles.deployStrip, { borderBottomColor: colors.primary + '40', backgroundColor: colors.primary + '10' }]}>
      <Ionicons name={'rocket'} size={15} color={colors.primary} />
      <Text style={[styles.deployStripText, { color: colors.text }]} numberOfLines={1}>
        {'Live'}
        {deploy.version ? ` · v${deploy.version}` : ''} — {deploy.url}
      </Text>
      <TouchableOpacity onPress={() => { _openWithToken(deploy.url); }}>
        <Text style={[styles.deployOpen, { color: colors.primary }]}>Open</Text>
      </TouchableOpacity>
    </View>
  ) : null;

  return (
    <Modal visible animationType={Platform.OS === 'web' ? 'fade' : 'slide'} transparent={false} onRequestClose={handleClose}>
      <View style={[styles.root, { backgroundColor: colors.background }]}>
        <BuildTopBar
          goal={isDraft ? '' : session?.goal}
          buildKinds={buildKinds}
          sessionId={session?.session_id}
          onClose={handleClose}
          colors={colors}
          draft={isDraft}
          queueMode={isDraft ? null : queueMode}
          onPressQueueMode={isDraft ? null : () => setShowQueueModePicker(true)}
          onStop={isDraft ? null : handleStop}
          stopping={stopping}
        />

        <QueueModePicker
          visible={showQueueModePicker}
          currentMode={queueMode}
          onSelect={handleSelectQueueMode}
          onClose={() => setShowQueueModePicker(false)}
        />

        {startError ? (
          <View style={[styles.errorStrip, { borderBottomColor: colors.danger + '40', backgroundColor: colors.danger + '10' }]}>
            <Ionicons name="alert-circle" size={14} color={colors.danger} />
            <Text style={[styles.errorStripText, { color: colors.danger }]} numberOfLines={2}>{startError}</Text>
          </View>
        ) : null}

        {deployBanner}

        {showTabs ? (
          <View style={[styles.tabBar, { borderBottomColor: colors.border, backgroundColor: colors.surface }]}>
            {TABS.map((t) => {
              const active = t.id === activeTab;
              return (
                <TouchableOpacity
                  key={t.id}
                  onPress={() => setActiveTab(t.id)}
                  style={[
                    styles.tab,
                    {
                      borderBottomColor: active ? colors.primary : 'transparent',
                      backgroundColor: active ? colors.background : 'transparent',
                    },
                  ]}
                >
                  <Ionicons name={t.icon} size={14} color={active ? colors.primary : colors.textSecondary} />
                  <Text style={[styles.tabText, { color: active ? colors.primary : colors.textSecondary }]}>
                    {t.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ) : null}

        {showTwo ? (
          // Wide + medium: chat on the left, live reasoning trace on the right.
          <View style={styles.body}>
            <View style={{ flex: 0.58 }}>{chatPane}</View>
            <View style={{ flex: 0.42, borderLeftWidth: 1, borderLeftColor: colors.border }}>{reasoningPane}</View>
          </View>
        ) : (
          <View style={styles.body}>
            {activeTab === 'chat' ? chatPane : null}
            {activeTab === 'reasoning' ? reasoningPane : null}
          </View>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  body: { flex: 1, flexDirection: 'row' },
  tabBar: {
    flexDirection: 'row', borderBottomWidth: 1,
  },
  tab: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: 6, paddingVertical: 10, borderBottomWidth: 2,
  },
  tabText: { fontSize: 12, fontWeight: '600' },

  // Inline strip shown if the lazy /build (first-message spawn) fails.
  errorStrip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: 1,
  },
  errorStripText: { fontSize: 12, flex: 1 },

  // Slim banner announcing a preview/live publish + an Open action.
  deployStrip: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 9, borderBottomWidth: 1,
  },
  deployStripText: { fontSize: 12.5, flex: 1, fontWeight: '500' },
  deployOpen: { fontSize: 12.5, fontWeight: '700' },
});
