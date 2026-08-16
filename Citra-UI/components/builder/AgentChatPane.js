/**
 * AgentChatPane — left pane: the BA ↔ agent dialogue.
 *
 * Renders user / assistant bubbles + a streaming caret while the agent
 * is mid-turn. Bottom composer mirrors QuickChatInput (Enter to send,
 * Shift+Enter for newline on web, multi-line auto-grow).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  ScrollView, Platform, ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import OpsMark from '../brand/OpsMark';

const BRAND_GRADIENT = ['#1E3A8A', '#2563EB'];   // navy → steel blue

// Reusable capability rows. The draft welcome card composes these per the
// surface the BA picked in the build-kind picker, so the greeting reflects
// their choice (App / API / Dashboard / combo) instead of a fixed script.
const CAP_APP  = { icon: 'apps-outline',      name: 'An app',                 text: 'the screens your team works in: forms, queues, tables, detail views.' };
const CAP_DASH = { icon: 'bar-chart-outline', name: 'A dashboard page',       text: 'KPI tiles + charts with an AI brief, right inside the app.' };
const CAP_AUTO = { icon: 'git-network-outline', name: 'Auto-Recommend',       text: "(optional) — a background agent that preprocesses work 24×7 and drops a ready AI recommendation into your queue, so it's waiting before you sit down." };
const CAP_API  = { icon: 'code-slash-outline', name: 'A headless Decision API', text: 'no UI — the decision engine exposed as POST /run + /approve, for you to plug into your own front-end.' };
const CAP_GATE = { icon: 'shield-checkmark-outline', name: 'A policy gate + audit', text: 'every recommendation is governed — auto-committed or escalated — and audited on every action.' };
const CAP_EMBED = { icon: 'browsers-outline', name: 'An embeddable card', text: 'the recommendation and approve/reject rendered inside a screen you already have — one script tag, no framework, your officers never switch systems.' };

// Keyed by the picker choice (session.buildKind). Falls back to
// 'conversational' (the original open-ended greeting) when no surface was
// picked. The insurance example + Auto-Recommend note below the card is shared
// across all surfaces, so it always renders.
const SURFACE_WELCOME = {
  conversational: {
    title: "👋 I'm your app builder",
    body: 'Tell me what you’d like to build — or just say hi and we’ll figure it out together.',
    caps: [CAP_APP, CAP_DASH, CAP_AUTO],
  },
  app: {
    title: '👋 Let’s build your app',
    body: 'You picked an App — the screens your team works in. Tell me the decision or workflow it should handle.',
    caps: [CAP_APP, CAP_DASH, CAP_AUTO],
  },
  dashboard: {
    title: '👋 Let’s build your dashboard',
    body: 'You picked a Dashboard — a live KPI + chart view with an AI brief. Tell me what it should track.',
    caps: [CAP_DASH, CAP_APP, CAP_AUTO],
  },
  app_dashboard: {
    title: '👋 Let’s build your app + dashboard',
    body: 'You picked an App plus a Dashboard page — operator screens and the manager’s live view, in one app. Tell me what it should handle.',
    caps: [CAP_APP, CAP_DASH, CAP_AUTO],
  },
  api: {
    title: '👋 Let’s build your Decision API',
    body: 'You picked a headless Decision API — no UI. Tell me the decision it should make; I’ll expose it as /run + /approve for your own front-end.',
    caps: [CAP_API, CAP_GATE, CAP_AUTO],
  },
  embed: {
    title: '👋 Let’s build your embeddable card',
    body: 'You picked an Embedded card — the recommendation and approve/reject, rendered inside a screen you already have. Tell me the decision it should handle and which record your screen shows.',
    caps: [CAP_EMBED, CAP_GATE, CAP_AUTO],
  },
};

export default function AgentChatPane({
  messages,
  isStreaming,
  onSend,
  onCancel,
  // NEW: when streaming, ``onSteer`` is called instead of ``onSend`` for
  // mid-task input. Forwarded to OpenClaw via chat.send + the active
  // queueMode (steer / collect / followup / steer-backlog).
  onSteer,
  colors,
  disabled = false,
  // NEW (chat-first): ``draft`` = no build session yet; the BA's first
  // message spawns it. ``startBusy`` = the pod is being spun up after that
  // first message. Both only affect the empty state + composer lock; send
  // routing is the parent's job (onSend points at startBuild in draft).
  draft = false,
  // Surface the BA chose in the build-kind picker: 'app' | 'api' |
  // 'dashboard' | 'app_dashboard' | 'conversational'. Drives the draft
  // welcome copy so the greeting reflects their pick. Defaults to the
  // open-ended greeting when unset (e.g. the edit flow).
  surface = 'conversational',
  startBusy = false,
  // NEW (steer discoverability): when provided, show an always-visible hint
  // above the composer that the BA can type while the agent works, with an
  // opener for the queue-mode picker. Only meaningful once a session is live.
  onOpenQueueMode,
  queueModeLabel,
  // NEW: open a URL inside an agent message (e.g. the published test-app URL)
  // WITH the user's JWT appended — passed from the screen (_openWithToken).
  // A bare runtime link 401s without it; the chat holds the token, so links
  // are routed through this on tap. When absent, links render styled but inert.
  onOpenUrl,
}) {
  const [text, setText] = useState('');
  const scrollRef = useRef(null);

  useEffect(() => {
    // Defer to next frame so layout is ready before scrolling.
    requestAnimationFrame(() => {
      scrollRef.current?.scrollToEnd?.({ animated: true });
    });
  }, [messages, isStreaming]);

  const handleSend = useCallback(() => {
    const t = text.trim();
    if (!t || disabled || startBusy) return;
    if (isStreaming) {
      // Mid-stream: route to onSteer if the parent provided one,
      // otherwise silently noop (keeps backwards compat with screens
      // that haven't opted into steer yet).
      if (onSteer) {
        setText('');
        onSteer(t);
      }
      return;
    }
    setText('');
    onSend?.(t);
  }, [text, isStreaming, disabled, startBusy, onSend, onSteer]);

  const handleKeyPress = useCallback((e) => {
    if (Platform.OS !== 'web') return;
    if (e.nativeEvent.key === 'Enter' && !e.nativeEvent.shiftKey) {
      e.preventDefault?.();
      handleSend();
    }
  }, [handleSend]);

  const isSteerSend = isStreaming && !!onSteer;
  const canSend = text.trim().length > 0 && !disabled && !startBusy && (!isStreaming || !!onSteer);

  return (
    <View style={[styles.root, { backgroundColor: colors.background, borderRightColor: colors.border }]}>
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <Ionicons name="chatbubbles-outline" size={16} color={colors.text} />
        <Text style={[styles.headerTitle, { color: colors.text }]}>Agent Chat</Text>
        <Text style={[styles.headerSub, { color: colors.textSecondary }]}>
          Describe a task, we will build you an agent
        </Text>
      </View>

      <ScrollView
        ref={scrollRef}
        style={styles.body}
        contentContainerStyle={styles.bodyContent}
      >
        {messages.length === 0 && !isStreaming && startBusy ? (
          <View style={styles.empty}>
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={[styles.emptyTitle, { color: colors.text }]}>
              Spinning up the builder…
            </Text>
            <Text style={[styles.emptyBody, { color: colors.textSecondary }]}>
              Getting your builder ready — this takes a few seconds.
            </Text>
          </View>
        ) : messages.length === 0 && !isStreaming ? (
          <View style={styles.empty}>
            <OpsMark size={56} radius={16} gradient={BRAND_GRADIENT} style={styles.emptyMark} />
            <Text style={[styles.emptyTitle, { color: colors.text }]}>
              {draft
                ? (SURFACE_WELCOME[surface] || SURFACE_WELCOME.conversational).title
                : 'Agent is reviewing your message'}
            </Text>
            <Text style={[styles.emptyBody, { color: colors.textSecondary }]}>
              {draft
                ? (SURFACE_WELCOME[surface] || SURFACE_WELCOME.conversational).body
                : 'Reply here when the agent asks clarifying questions.'}
            </Text>

            {draft ? (
              <View style={[styles.capsCard, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                <Text style={[styles.capsTitle, { color: colors.text }]}>What I can build for you</Text>

                {(SURFACE_WELCOME[surface] || SURFACE_WELCOME.conversational).caps.map((cap) => (
                  <View style={styles.capRow} key={cap.name}>
                    <Ionicons name={cap.icon} size={15} color={colors.primary} style={styles.capIcon} />
                    <Text style={[styles.capText, { color: colors.textSecondary }]}>
                      <Text style={[styles.capName, { color: colors.text }]}>{cap.name}</Text> — {cap.text}
                    </Text>
                  </View>
                ))}

                <Text style={[styles.capExample, { color: colors.textSecondary }]}>
                  e.g. <Text style={styles.capStrong}>insurance claims</Text> — with Auto-Recommend, every claim is pre-assessed in the background, so you open your queue and just <Text style={styles.capStrong}>Approve or Reject</Text>. Without it, you ask the AI to triage each claim on the spot and wait for its recommendation. To preprocess, turn on Auto-Recommend.
                </Text>

                <Text style={[styles.capNote, { color: colors.textSecondary }]}>
                  Auto-Recommend never changes your source systems — it only precomputes and recommends. The actual write happens when you approve.
                </Text>
              </View>
            ) : null}
          </View>
        ) : null}

        {messages.map((m) => (
          <Bubble key={m.id} m={m} colors={colors} onOpenUrl={onOpenUrl} />
        ))}

        {isStreaming ? (
          <View style={styles.typing}>
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={[styles.typingText, { color: colors.textSecondary }]}>
              Agent is thinking…
            </Text>
          </View>
        ) : null}
      </ScrollView>

      {onOpenQueueMode && onSteer ? (
        <View style={[styles.steerHint, { borderTopColor: colors.border, backgroundColor: colors.surface }]}>
          <Ionicons name="information-circle-outline" size={13} color={colors.textSecondary} />
          <Text style={[styles.steerHintText, { color: colors.textSecondary }]} numberOfLines={2}>
            You can keep typing while I work — I’ll adjust on the fly.
          </Text>
          <TouchableOpacity onPress={onOpenQueueMode} style={styles.steerHintBtn} accessibilityLabel="change mid-task message behavior">
            <Ionicons name="git-branch-outline" size={11} color="#6D28D9" />
            <Text style={styles.steerHintBtnText} numberOfLines={1}>{queueModeLabel || 'Steer'}</Text>
            <Ionicons name="chevron-down" size={10} color="#6D28D9" />
          </TouchableOpacity>
        </View>
      ) : null}

      <View style={[styles.composer, { borderTopColor: colors.border, backgroundColor: colors.surface }]}>
        <TextInput
          value={text}
          onChangeText={setText}
          onKeyPress={handleKeyPress}
          editable={!disabled && !startBusy && (!isStreaming || !!onSteer)}
          placeholder={
            startBusy
              ? 'Spinning up the builder…'
              : isSteerSend
                ? 'Type to steer mid-task — agent will adjust without a restart…'
                : draft
                  ? 'Describe what you’d like to build — or just say hi…'
                  : 'Type your instructions or question here…'
          }
          placeholderTextColor={colors.textSecondary}
          multiline
          style={[
            styles.input,
            { color: colors.text, borderColor: colors.border, backgroundColor: colors.background },
          ]}
        />
        {isStreaming ? (
          <TouchableOpacity onPress={onCancel} style={[styles.cancelBtn, { borderColor: colors.border }]}>
            <Ionicons name="stop-circle-outline" size={18} color={colors.danger || '#DC2626'} />
          </TouchableOpacity>
        ) : null}
        <TouchableOpacity
          onPress={handleSend}
          disabled={!canSend}
          style={styles.sendWrap}
        >
          {isSteerSend ? (
            <View
              style={[
                styles.sendBtn,
                { backgroundColor: '#F3E8FF' },
                !canSend && { opacity: 0.4 },
              ]}
            >
              <Ionicons name="return-down-forward" size={18} color="#6D28D9" />
            </View>
          ) : (
            <LinearGradient
              colors={BRAND_GRADIENT}
              start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
              style={[
                styles.sendBtn,
                !canSend && { opacity: 0.4 },
              ]}
            >
              <Ionicons name="send" size={16} color="#FFFFFF" />
            </LinearGradient>
          )}
        </TouchableOpacity>
      </View>
    </View>
  );
}

// Render agent text with TAPPABLE links. Handles markdown `[label](url)` and
// bare http(s) URLs; a tapped link calls onOpenUrl(url), which appends the
// user's JWT (?_t=) so the launched runtime authenticates. Everything else is
// plain text. Without onOpenUrl the link still renders styled (inert).
// URL char class excludes `*` and `]` so markdown bold (**) / link-close don't
// get swallowed into the URL (a trailing `**` made the runtime 404 on slug**).
const _LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)*\]]+)\)|(https?:\/\/[^\s)*\]]+)/g;
function renderRichText(text, colors, onOpenUrl) {
  if (!text || typeof text !== 'string') return text;
  const parts = [];
  let last = 0;
  let n = 0;
  let mt;
  _LINK_RE.lastIndex = 0;
  while ((mt = _LINK_RE.exec(text)) !== null) {
    if (mt.index > last) parts.push(text.slice(last, mt.index));
    const url = (mt[2] || mt[3]).replace(/[.,*)\]]+$/, ''); // drop trailing punctuation / ** bold
    const label = mt[1] || url;
    parts.push(
      <Text
        key={`lnk-${n++}`}
        style={{ color: colors.primary, textDecorationLine: 'underline' }}
        onPress={onOpenUrl ? () => onOpenUrl(url) : undefined}
      >
        {label}
      </Text>
    );
    last = _LINK_RE.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length ? parts : text;
}

function Bubble({ m, colors, onOpenUrl }) {
  const isUser = m.role === 'user';
  return (
    <View style={[styles.bubbleRow, isUser ? styles.bubbleRowEnd : styles.bubbleRowStart]}>
      <View
        style={[
          styles.bubble,
          isUser
            ? { backgroundColor: colors.primary + '15', borderColor: colors.primary + '30' }
            : { backgroundColor: colors.surface, borderColor: colors.border },
        ]}
      >
        <View style={styles.bubbleRoleRow}>
          <Ionicons
            name={isUser ? 'person-circle-outline' : 'sparkles-outline'}
            size={12}
            color={isUser ? colors.primary : colors.textSecondary}
          />
          <Text style={[styles.bubbleRole, { color: isUser ? colors.primary : colors.textSecondary }]}>
            {isUser ? 'You' : 'Agent'}
          </Text>
        </View>
        <Text style={[styles.bubbleText, { color: colors.text }]}>
          {isUser ? m.text : renderRichText(m.text, colors, onOpenUrl)}
          {m.streaming ? <Text style={{ color: colors.primary }}>▍</Text> : null}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, borderRightWidth: 1, minWidth: 0 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1,
    flexWrap: 'wrap',
  },
  headerTitle: { fontSize: 14, fontWeight: '700' },
  headerSub: { fontSize: 12, flex: 1, minWidth: 0 },

  body: { flex: 1 },
  bodyContent: { padding: 16, gap: 10 },

  empty: { alignItems: 'center', paddingVertical: 32, gap: 8 },
  // OpsMark sets its own 56px tile + radius; this only carries the shadow.
  emptyMark: {
    shadowColor: '#1E3A8A', shadowOpacity: 0.28,
    shadowRadius: 10, shadowOffset: { width: 0, height: 4 },
  },
  emptyTitle: { fontSize: 14, fontWeight: '700' },
  emptyBody: { fontSize: 12, textAlign: 'center', maxWidth: 320, lineHeight: 17 },

  // "What I can build" explainer (draft welcome only).
  capsCard: {
    marginTop: 16, width: '100%', maxWidth: 420,
    borderWidth: 1, borderRadius: 12, padding: 14, gap: 9,
  },
  capsTitle: {
    fontSize: 11, fontWeight: '700', textTransform: 'uppercase',
    letterSpacing: 0.5, marginBottom: 2,
  },
  capRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  capIcon: { marginTop: 1 },
  capText: { fontSize: 12, lineHeight: 17, flex: 1 },
  capName: { fontWeight: '700' },
  capStrong: { fontWeight: '700' },
  capExample: {
    fontSize: 12, lineHeight: 17, marginTop: 4,
    paddingTop: 9, borderTopWidth: 1, borderTopColor: '#E5E7EB',
  },
  capNote: { fontSize: 11, lineHeight: 15, fontStyle: 'italic' },

  bubbleRow: { flexDirection: 'row' },
  bubbleRowStart: { justifyContent: 'flex-start' },
  bubbleRowEnd: { justifyContent: 'flex-end' },
  bubble: {
    maxWidth: '85%', padding: 10, borderWidth: 1, borderRadius: 12, gap: 4,
  },
  bubbleRoleRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  bubbleRole: { fontSize: 10, fontWeight: '700', letterSpacing: 0.4, textTransform: 'uppercase' },
  bubbleText: { fontSize: 13, lineHeight: 19 },

  typing: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6, paddingHorizontal: 4 },
  typingText: { fontSize: 12 },

  steerHint: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 12, paddingTop: 8, borderTopWidth: 1,
  },
  steerHintText: { fontSize: 11, flex: 1, lineHeight: 15 },
  steerHintBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10,
    backgroundColor: '#F3E8FF',
  },
  steerHintBtnText: { fontSize: 11, fontWeight: '700', color: '#6D28D9', maxWidth: 130 },
  composer: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 8,
    padding: 12, borderTopWidth: 1,
  },
  input: {
    flex: 1, minHeight: 44, maxHeight: 140,
    borderWidth: 1, borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 13,
    ...(Platform.OS === 'web' ? { outlineStyle: 'none' } : {}),
  },
  cancelBtn: {
    width: 36, height: 36, borderRadius: 8, borderWidth: 1,
    alignItems: 'center', justifyContent: 'center',
  },
  sendWrap: {},
  sendBtn: {
    width: 40, height: 40, borderRadius: 10,
    alignItems: 'center', justifyContent: 'center',
  },
});
