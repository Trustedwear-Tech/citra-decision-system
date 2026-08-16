/**
 * AgentReasoningPane — center pane: the live trace of agent thinking.
 *
 * Inspired by the demo screenshot: a thinking bubble at the top while
 * the agent is mid-turn, then a list of "tools running" with status
 * dots — green check when done, amber dot when running.
 *
 * This is NOT chat. It surfaces tool.start / tool.result events plus
 * any reasoning prose the agent emits outside the chat-text channel.
 */

import React, { useEffect, useRef } from 'react';
import { View, Text, ScrollView, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';

const STATUS_TINT = {
  running: '#F59E0B', // amber
  done:    '#10B981', // green
  error:   '#DC2626', // red
};

export default function AgentReasoningPane({ events, isThinking, colors }) {
  const scrollRef = useRef(null);
  useEffect(() => {
    requestAnimationFrame(() => {
      scrollRef.current?.scrollToEnd?.({ animated: true });
    });
  }, [events, isThinking]);

  return (
    <View style={[styles.root, { backgroundColor: colors.background, borderRightColor: colors.border }]}>
      <View style={[styles.header, { borderBottomColor: colors.border }]}>
        <Ionicons name="bulb-outline" size={16} color={colors.text} />
        <Text style={[styles.headerTitle, { color: colors.text }]}>Agent Reasoning</Text>
      </View>

      <ScrollView ref={scrollRef} style={styles.body} contentContainerStyle={styles.bodyContent}>
        {/* Thinking pulse — mirrors the demo screenshot's centered bubble */}
        {isThinking ? (
          <View style={styles.thinkingWrap}>
            <LinearGradient
              colors={['#3B82F6', '#1D4ED8']}
              start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
              style={styles.thinkingBubble}
            >
              <View style={styles.dotRow}>
                <View style={styles.dot} />
                <View style={styles.dot} />
                <View style={styles.dot} />
              </View>
            </LinearGradient>
          </View>
        ) : null}

        {events.length === 0 && !isThinking ? (
          <View style={styles.empty}>
            <Text style={[styles.emptyText, { color: colors.textSecondary }]}>
              The agent's tool calls and reasoning will stream here.
            </Text>
          </View>
        ) : null}

        {events.map((evt) => (
          <ReasoningRow key={evt.id} evt={evt} colors={colors} />
        ))}

        {isThinking ? (
          <View style={styles.tail}>
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={[styles.tailText, { color: colors.textSecondary }]}>
              Reasoning…
            </Text>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

function ReasoningRow({ evt, colors }) {
  if (evt.kind === 'tool') {
    const tint = STATUS_TINT[evt.toolStatus] || colors.textSecondary;
    const ic = evt.toolStatus === 'done'
      ? 'checkmark-circle'
      : evt.toolStatus === 'error'
        ? 'alert-circle'
        : 'ellipse';
    return (
      <View style={styles.row}>
        <Ionicons name={ic} size={14} color={tint} />
        <View style={{ flex: 1 }}>
          <Text style={[styles.rowTitle, { color: colors.text }]} numberOfLines={1}>
            {prettifyTool(evt.tool)}
          </Text>
          {evt.text ? (
            <Text style={[styles.rowSub, { color: colors.textSecondary }]} numberOfLines={2}>
              {typeof evt.text === 'string' ? evt.text : JSON.stringify(evt.text).slice(0, 200)}
            </Text>
          ) : null}
        </View>
      </View>
    );
  }
  // OpenClaw native thinking block (model reasoning content). Distinct
  // visual treatment from generic reasoning prose: purple italic block
  // so the user can tell "this is what the model is thinking" apart
  // from "this is the agent's structured plan/output".
  if (evt.kind === 'reasoning') {
    return (
      <View style={styles.thinkingRow}>
        <Ionicons name="bulb-outline" size={14} color="#6D28D9" style={{ marginTop: 1 }} />
        <Text style={styles.thinkingText} selectable>
          {evt.text}
        </Text>
      </View>
    );
  }
  // Canvas / A2UI activity surfaced as a one-line indicator.
  if (evt.kind === 'canvas') {
    return (
      <View style={styles.row}>
        <Ionicons name="easel-outline" size={14} color="#6D28D9" />
        <View style={{ flex: 1 }}>
          <Text style={[styles.rowTitle, { color: colors.text }]} numberOfLines={1}>
            Canvas · {evt.canvasAction || 'update'}
            {evt.surfaceId ? ` · ${evt.surfaceId}` : ''}
          </Text>
        </View>
      </View>
    );
  }
  // Generic reasoning prose (legacy adapter fallback / unknown kinds).
  return (
    <View style={styles.row}>
      <Ionicons name="ellipsis-horizontal-circle-outline" size={14} color={colors.textSecondary} />
      <Text style={[styles.rowTitle, { color: colors.text, flex: 1 }]} numberOfLines={4}>
        {evt.text}
      </Text>
    </View>
  );
}

function prettifyTool(name) {
  if (!name) return 'Tool call';
  return String(name)
    .replace(/^skill[:/]/i, '')
    .replace(/^citra-/, '')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const styles = StyleSheet.create({
  root: { flex: 1, borderRightWidth: 1, minWidth: 0 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1,
  },
  headerTitle: { fontSize: 14, fontWeight: '700' },

  body: { flex: 1 },
  bodyContent: { padding: 16, gap: 10 },

  thinkingWrap: { alignItems: 'center', paddingVertical: 16 },
  thinkingBubble: {
    width: 64, height: 40, borderRadius: 20,
    alignItems: 'center', justifyContent: 'center',
    shadowColor: '#3B82F6', shadowOpacity: 0.4, shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 }, elevation: 6,
  },
  dotRow: { flexDirection: 'row', gap: 4 },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#FFFFFF' },

  empty: { alignItems: 'center', paddingVertical: 32 },
  emptyText: { fontSize: 12, textAlign: 'center', maxWidth: 240, lineHeight: 17 },

  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  rowTitle: { fontSize: 13, fontWeight: '600' },
  rowSub: { fontSize: 11, marginTop: 2, lineHeight: 15 },

  tail: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 4 },
  tailText: { fontSize: 11, fontStyle: 'italic' },

  thinkingRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 10,
    backgroundColor: '#FAF5FF',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#E9D5FF',
  },
  thinkingText: {
    flex: 1,
    fontSize: 12,
    color: '#4C1D95',
    lineHeight: 17,
    fontStyle: 'italic',
  },
});
